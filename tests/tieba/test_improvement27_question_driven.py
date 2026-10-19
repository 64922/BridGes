"""Issue 27：来源路由、适用性与预算共享。"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.chat.run_budget_ledger import RunBudgetLedgerRepository
from bridges.tieba.contracts import (
    ReadStatus,
    TiebaSearchHit,
)
from bridges.tieba.official import official_query
from bridges.tieba.parsing import parse_tieba_request
from bridges.tieba.recipe import OFFICIAL_UNVERIFIED_NOTE, TIEBA_ONLY_OFFICIAL_BLOCKED
from bridges.tieba.searching import plan_queries
from tests.tieba.support import (
    CONFIRMED_URL,
    OFFICIAL_URL,
    REAL_REPLIES,
    _create_conversation,
    _FakeOfficialReader,
    _FakeReader,
    _FakeSearchPort,
    _official_hit,
    _read_result,
    _register,
    _run_and_read,
    _send,
    _wire,
)


def test_policy_checks_official_first_and_shares_one_run_budget(
    sqlite_app: Any,
    client: TestClient,
    generation_helpers: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """规定类：官方先核对，再查贴吧；两路外部调用记在同一条运行预算上。"""
    account = _register(client)
    analysis = parse_tieba_request("华东交通大学吧 转专业 规定 流程")
    official = official_query(analysis)
    tieba_query = plan_queries(analysis)[0]
    port = _FakeSearchPort(
        per_query={
            official: [_official_hit()],
            tieba_query: [TiebaSearchHit(url=CONFIRMED_URL, title="转专业经验", snippet="")],
        }
    )
    reader = _FakeReader(
        {
            CONFIRMED_URL: _read_result(
                url=CONFIRMED_URL,
                status=ReadStatus.READ,
                title="转专业经验",
                replies=REAL_REPLIES,
            )
        }
    )
    official_reader = _FakeOfficialReader()
    _wire(sqlite_app, port, reader=reader, official_reader=official_reader)
    from dataclasses import replace

    from bridges.kernel.contracts import ArtifactTrust
    from bridges.tieba.delivery import TiebaResultDelivery
    from bridges.tieba.quality_gates import verified_projection
    from bridges.tieba.recipe import NODE_VERIFY

    delivered: list[Any] = []
    original_delivery = TiebaResultDelivery._deliver_completed

    def capture(
        self: Any,
        repo: Any,
        account_id: str,
        message_id: str,
        result: Any,
        *,
        remaining_budget_ms: int | None,
        task_ref: tuple[str | None, int | None] | None = None,
    ) -> Any:
        delivered.append(result)
        return original_delivery(
            self,
            repo,
            account_id,
            message_id,
            result,
            remaining_budget_ms=remaining_budget_ms,
            task_ref=task_ref,
        )

    monkeypatch.setattr(TiebaResultDelivery, "_deliver_completed", capture)
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "华东交通大学吧 转专业 规定 流程", module_id="tieba")
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    assert assistant["status"] == "done"
    # 执行顺序：官方域名检索先于贴吧检索（官方域名不自动放行，仍逐页核对适用性）。
    assert port.queries == [official, *plan_queries(analysis)]
    assert official_reader.fetched == [OFFICIAL_URL]
    tieba = assistant["tieba_research"]
    assert tieba["question_kind"] == "policy"
    assert tieba["source_priority"] == ["official", "tieba"]
    assert tieba["parallel_evidence"] is False
    assert "共享同一运行预算" in tieba["plan_rationale"]
    assert tieba["verification"]["official_scope_consistent"] is True
    assert "取证范围" in assistant["content"]

    # 官方检索、官方抓取、贴吧检索与帖子读取都记在同一条运行账本上。
    database = sqlite_app.state.chat_service._repo.database  # noqa: SLF001
    rows = (
        database.scoped(account["id"])
        .execute(
            "SELECT run_id, external_calls_used, external_calls_active "
            "FROM run_budget_ledger WHERE account_id = ? AND conversation_id = ?",
            (account["id"], conversation_id),
        )
        .fetchall()
    )
    assert len(rows) == 1
    snapshot = RunBudgetLedgerRepository(database).load(account["id"], rows[0]["run_id"])
    assert snapshot is not None
    assert snapshot.external_calls_used == (
        len(port.queries) + len(official_reader.fetched) + len(reader.read_urls)
    )
    assert snapshot.external_calls_active == 0
    result = delivered[0]
    assert verified_projection(result) is not None
    assert (
        verified_projection(
            replace(
                result,
                artifacts=tuple(
                    artifact for artifact in result.artifacts if artifact.node != NODE_VERIFY
                ),
            )
        )
        is None
    )
    verify = result.artifact(NODE_VERIFY)
    assert verify is not None
    for bad in (
        replace(verify, trust_state=ArtifactTrust.EVIDENCE_BOUND),
        replace(
            verify,
            payload={
                **verify.payload,
                "projection": {**verify.payload["projection"], "topic": "伪造主题"},
            },
        ),
    ):
        changed = replace(
            result,
            artifacts=tuple(
                bad if artifact.node == NODE_VERIFY else artifact for artifact in result.artifacts
            ),
        )
        assert verified_projection(changed) is None

    service = sqlite_app.state.chat_service.tieba_research_service
    repo = sqlite_app.state.chat_service._repo
    message = repo.get_message(account["id"], assistant["message_id"])
    assert message is not None
    assert service.verify_message(
        repo,
        account_id=account["id"],
        run_id=rows[0]["run_id"],
        conversation_id=conversation_id,
        assistant_message_id=assistant["message_id"],
    )
    monkeypatch.setattr(repo, "get_message", lambda *_args: replace(message, content="篡改正文"))
    assert not service.verify_message(
        repo,
        account_id=account["id"],
        run_id=rows[0]["run_id"],
        conversation_id=conversation_id,
        assistant_message_id=assistant["message_id"],
    )


def test_experience_question_keeps_forum_only_scope(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """体验类：以真实帖子与回复为主，不追加官方核验。"""
    _register(client)
    analysis = parse_tieba_request("华东交通大学吧 食堂 怎么样")
    port = _FakeSearchPort(hits=[TiebaSearchHit(url=CONFIRMED_URL, title="食堂怎么样", snippet="")])
    reader = _FakeReader(
        {
            CONFIRMED_URL: _read_result(
                url=CONFIRMED_URL, status=ReadStatus.READ, replies=REAL_REPLIES
            )
        }
    )
    official_reader = _FakeOfficialReader()
    _wire(sqlite_app, port, reader=reader, official_reader=official_reader)
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "华东交通大学吧 食堂 怎么样", module_id="tieba")
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    assert port.queries == list(plan_queries(analysis))
    assert official_reader.fetched == []
    tieba = assistant["tieba_research"]
    assert tieba["question_kind"] == "experience"
    assert tieba["source_priority"] == ["tieba"]
    assert tieba["official_check_requested"] is False
    assert "官方" not in tieba["plan_rationale"]
    assert "官方" not in assistant["content"]
    assert "体验类" in assistant["content"]


def test_mixed_question_runs_both_routes_in_order(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """混合类：官方规定与吧友经历两路独立取证，冲突按时间与范围分列。"""
    _register(client)
    # 本票明确核实规定才追加官网；搜集经历本身不扩大来源。
    text = "华东交通大学吧 确认 转专业 规定 学长 经历 怎么样"
    analysis = parse_tieba_request(text)
    official = official_query(analysis)
    tieba_query = plan_queries(analysis)[0]
    port = _FakeSearchPort(
        per_query={
            official: [_official_hit()],
            tieba_query: [TiebaSearchHit(url=CONFIRMED_URL, title="转专业经历", snippet="")],
        }
    )
    reader = _FakeReader(
        {
            CONFIRMED_URL: _read_result(
                url=CONFIRMED_URL,
                status=ReadStatus.READ,
                title="转专业经历",
                replies=REAL_REPLIES,
            )
        }
    )
    _wire(sqlite_app, port, reader=reader, official_reader=_FakeOfficialReader())
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, text, module_id="tieba")
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    assert port.queries == [official, *plan_queries(analysis)]
    tieba = assistant["tieba_research"]
    assert tieba["question_kind"] == "mixed"
    assert tieba["source_priority"] == ["official", "tieba"]
    assert "共享同一运行预算" in tieba["plan_rationale"]
    assert "冲突按时间与适用范围分列" in tieba["plan_rationale"]
    assert tieba["official_checks"]


def test_tieba_only_restriction_blocks_official_route(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """只查贴吧：规定类也不得绕过来源限制访问官方页面，保持未核实。"""
    _register(client)
    analysis = parse_tieba_request("只在贴吧查 转专业 规定 流程")
    port = _FakeSearchPort(hits=[TiebaSearchHit(url=CONFIRMED_URL, title="转专业经验", snippet="")])
    reader = _FakeReader(
        {
            CONFIRMED_URL: _read_result(
                url=CONFIRMED_URL, status=ReadStatus.READ, replies=REAL_REPLIES
            )
        }
    )
    official_reader = _FakeOfficialReader()
    _wire(sqlite_app, port, reader=reader, official_reader=official_reader)
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "只在贴吧查 转专业 规定 流程", module_id="tieba")
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    tieba = assistant["tieba_research"]
    assert port.queries == list(plan_queries(analysis))
    assert official_reader.fetched == []
    assert tieba["official_check_requested"] is True
    assert tieba["official_checks"] == []
    assert tieba["official_blocked_reason"] == TIEBA_ONLY_OFFICIAL_BLOCKED
    assert tieba["official_unverified_note"] == (
        "来源限制生效：规定相关内容未核实（未访问学校官方页面）。"
    )
    assert tieba["verification"]["official_scope_consistent"] is True
    assert "来源限制" in assistant["content"]
    assert "未核实" in assistant["content"]
    assert "官方原文摘录" not in assistant["content"]


def test_official_page_without_named_campus_is_not_applicable(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """官方域名不自动放行：页面没点名问题中的校区，适用性未确认。"""
    _register(client)
    text = "华东交通大学吧 南区 转专业 规定 流程"
    analysis = parse_tieba_request(text)
    official = official_query(analysis)
    tieba_query = plan_queries(analysis)[0]
    port = _FakeSearchPort(
        per_query={
            official: [_official_hit()],
            tieba_query: [TiebaSearchHit(url=CONFIRMED_URL, title="转专业经验", snippet="")],
        }
    )
    reader = _FakeReader(
        {
            CONFIRMED_URL: _read_result(
                url=CONFIRMED_URL, status=ReadStatus.READ, replies=REAL_REPLIES
            )
        }
    )
    _wire(sqlite_app, port, reader=reader, official_reader=_FakeOfficialReader())
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, text, module_id="tieba")
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    tieba = assistant["tieba_research"]
    assert tieba["campus_terms"] == ["南区"]
    check = tieba["official_checks"][0]
    assert check["applicability"]["campus_confirmed"] is False
    assert check["applicability"]["applicable"] is False
    assert "未点名「南区」" in check["applicability"]["note"]
    assert tieba["official_unverified_note"] == OFFICIAL_UNVERIFIED_NOTE
    assert tieba["verification"]["official_scope_consistent"] is True
    assert "（适用性未确认）" in assistant["content"]
    assert OFFICIAL_UNVERIFIED_NOTE in assistant["content"]
