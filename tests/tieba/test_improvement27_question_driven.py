"""Issue 27 端到端验收：按规定、体验与混合问题编排贴吧取证。

覆盖工单五条验收：

- 规定类官方先行、体验类只查贴吧、混合类两路独立且共享同一运行预算；
- 只有标题／摘要时不总结未读回复，也不出现共识式表述；
- 官方与帖子冲突按时间与适用范围核对后分列，官方域名不自动放行；
- 只查贴吧的来源限制不被官方核验节点绕过，访问不可得按真实层次降级；
- 恢复轮沿用已完成的检索／读取／核验产物，不重复发起有效取证。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.chat.run_budget_ledger import RunBudgetLedgerRepository
from bridges.contracts.modules import ModuleQueryStatus
from bridges.tieba import evidence
from bridges.tieba.contracts import (
    ReadStatus,
    TiebaOfficialCheck,
    TiebaPostProjection,
    TiebaReply,
    TiebaSearchHit,
)
from bridges.tieba.kernel import OFFICIAL_UNVERIFIED_NOTE, TIEBA_ONLY_OFFICIAL_BLOCKED
from bridges.tieba.official import STATUS_VERIFIED, official_query
from bridges.tieba.parsing import parse_tieba_request
from bridges.tieba.searching import plan_queries, query_record
from tests.tieba.test_tieba_module_flow import (
    CONFIRMED_URL,
    REAL_REPLIES,
    _create_conversation,
    _FakeOfficialReader,
    _FakeReader,
    _FakeSearchPort,
    _gateway_with,
    _install_tieba_service,
    _read_result,
    _register,
    _run_and_read,
    _send,
    _SilentAdapter,
)

OFFICIAL_URL = "https://jwc.ecjtu.edu.cn/info/1041/4572.htm"


@pytest.fixture
def sqlite_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from bridges.api.main import create_app
    from bridges.config import get_settings

    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "api-test-secret-key")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    return create_app()


@pytest.fixture
def client(sqlite_app: Any) -> TestClient:
    return TestClient(sqlite_app)


def _wire(
    sqlite_app: Any,
    port: _FakeSearchPort,
    *,
    reader: _FakeReader | None = None,
    official_reader: _FakeOfficialReader | None = None,
) -> None:
    _install_tieba_service(
        sqlite_app, port=port, reader=reader, official_reader=official_reader
    )
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001


def _official_hit() -> TiebaSearchHit:
    return TiebaSearchHit(url=OFFICIAL_URL, title="转专业管理办法-教务处", snippet="")


def test_policy_checks_official_first_and_shares_one_run_budget(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """规定类：官方先核对，再查贴吧；两路外部调用记在同一条运行预算上。"""
    account = _register(client)
    analysis = parse_tieba_request("华东交通大学吧 转专业 规定 流程")
    official = official_query(analysis)
    tieba_query = plan_queries(analysis)[0]
    port = _FakeSearchPort(
        per_query={
            official: [_official_hit()],
            tieba_query: [
                TiebaSearchHit(url=CONFIRMED_URL, title="转专业经验", snippet="")
            ],
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
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "华东交通大学吧 转专业 规定 流程", module_id="tieba")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    assert assistant["status"] == "done"
    # 执行顺序：官方域名检索先于贴吧检索（官方域名不自动放行，仍逐页核对适用性）。
    assert port.queries == [official, tieba_query]
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
    rows = database.scoped(account["id"]).execute(
        "SELECT run_id, external_calls_used, external_calls_active "
        "FROM run_budget_ledger WHERE account_id = ? AND conversation_id = ?",
        (account["id"], conversation_id),
    ).fetchall()
    assert len(rows) == 1
    snapshot = RunBudgetLedgerRepository(database).load(account["id"], rows[0]["run_id"])
    assert snapshot is not None
    assert snapshot.external_calls_used == 4
    assert snapshot.external_calls_active == 0


def test_experience_question_keeps_forum_only_scope(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """体验类：以真实帖子与回复为主，不追加官方核验。"""
    _register(client)
    analysis = parse_tieba_request("华东交通大学吧 食堂 怎么样")
    port = _FakeSearchPort(
        hits=[TiebaSearchHit(url=CONFIRMED_URL, title="食堂怎么样", snippet="")]
    )
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
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    assert port.queries == [plan_queries(analysis)[0]]
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
    text = "华东交通大学吧 转专业 规定 学长 经历 怎么样"
    analysis = parse_tieba_request(text)
    official = official_query(analysis)
    tieba_query = plan_queries(analysis)[0]
    port = _FakeSearchPort(
        per_query={
            official: [_official_hit()],
            tieba_query: [
                TiebaSearchHit(url=CONFIRMED_URL, title="转专业经历", snippet="")
            ],
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
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    assert port.queries == [official, tieba_query]
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
    tieba_query = plan_queries(analysis)[0]
    port = _FakeSearchPort(
        hits=[TiebaSearchHit(url=CONFIRMED_URL, title="转专业经验", snippet="")]
    )
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
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    tieba = assistant["tieba_research"]
    assert port.queries == [tieba_query]
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
            tieba_query: [
                TiebaSearchHit(url=CONFIRMED_URL, title="转专业经验", snippet="")
            ],
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
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

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


def test_conflict_with_newer_official_rule_uses_time_and_scope_basis(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """官方带生效依据且日期不早于帖子：按新规定呈现，帖子经历一并保留。"""
    _register(client)
    text = "华东交通大学吧 转专业 规定 流程"
    analysis = parse_tieba_request(text)
    official = official_query(analysis)
    tieba_query = plan_queries(analysis)[0]
    check = TiebaOfficialCheck(
        title="华东交通大学转专业管理办法（2026年修订）",
        url=OFFICIAL_URL,
        host="jwc.ecjtu.edu.cn",
        fetched_at=datetime(2026, 9, 25, 3, 0, tzinfo=UTC),
        status=STATUS_VERIFIED,
        excerpt="自2026年3月1日起执行：转专业申请调整为每学年一次，由教务处统一审批。",
        matched_terms=["转专业"],
    )
    reply = TiebaReply(
        floor=5,
        posted_at="2025-05-20 10:00",
        content="转专业不是每学年一次，我去年申请的时候是随时受理的。",
    )
    port = _FakeSearchPort(
        per_query={
            official: [_official_hit()],
            tieba_query: [
                TiebaSearchHit(url=CONFIRMED_URL, title="转专业经验", snippet="")
            ],
        }
    )
    reader = _FakeReader(
        {
            CONFIRMED_URL: _read_result(
                url=CONFIRMED_URL,
                status=ReadStatus.READ,
                title="转专业经验",
                replies=(reply,),
            )
        }
    )
    _wire(
        sqlite_app,
        port,
        reader=reader,
        official_reader=_FakeOfficialReader(checks={OFFICIAL_URL: check}),
    )
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, text, module_id="tieba")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    tieba = assistant["tieba_research"]
    assert tieba["conflicts"][0]["resolution"] == "official_newer"
    assert tieba["conflicts"][0]["official_date"] == "2026"
    assert tieba["conflicts"][0]["post_refs"][0]["floor"] == 5
    assert tieba["verification"]["conflicts_disclosed"] is True
    assert "【官方与帖子经历的冲突（按时间与适用范围核对）】" in assistant["content"]
    assert "按新规定呈现" in assistant["content"]
    assert "帖子（https://tieba.baidu.com/p/10745250786｜第 5 楼" in assistant["content"]


def test_conflict_without_supersession_keeps_both_sides(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """官方没有生效／修订依据时不替任何一方下结论，双方分列保留冲突。"""
    _register(client)
    text = "华东交通大学吧 转专业 规定 流程"
    analysis = parse_tieba_request(text)
    official = official_query(analysis)
    tieba_query = plan_queries(analysis)[0]
    check = TiebaOfficialCheck(
        title="转专业管理办法-教务处",
        url=OFFICIAL_URL,
        host="jwc.ecjtu.edu.cn",
        fetched_at=datetime(2026, 9, 25, 3, 0, tzinfo=UTC),
        status=STATUS_VERIFIED,
        excerpt="转专业申请由教务处统一审批，材料交所在学院。",
        matched_terms=["转专业"],
    )
    reply = TiebaReply(
        floor=2,
        posted_at="2025-05-20 10:00",
        content="转专业不是交到教务处就行，我那年学院说不用报教务处。",
    )
    port = _FakeSearchPort(
        per_query={
            official: [_official_hit()],
            tieba_query: [
                TiebaSearchHit(url=CONFIRMED_URL, title="转专业经验", snippet="")
            ],
        }
    )
    reader = _FakeReader(
        {
            CONFIRMED_URL: _read_result(
                url=CONFIRMED_URL,
                status=ReadStatus.READ,
                title="转专业经验",
                replies=(reply,),
            )
        }
    )
    _wire(
        sqlite_app,
        port,
        reader=reader,
        official_reader=_FakeOfficialReader(checks={OFFICIAL_URL: check}),
    )
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, text, module_id="tieba")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    tieba = assistant["tieba_research"]
    assert tieba["conflicts"][0]["resolution"] == "kept_both"
    assert tieba["verification"]["conflicts_disclosed"] is True
    assert "双方分列" in assistant["content"]
    assert "无法确认官方页面已替代帖中说法" in tieba["conflicts"][0]["time_basis"]


def test_links_only_never_summarizes_unread_replies(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """页面不可读时按真实层次降级为仅帖链，不用标题／摘要总结未读回复。"""
    _register(client)
    port = _FakeSearchPort(
        hits=[
            TiebaSearchHit(
                url=CONFIRMED_URL,
                title="大家都说食堂很好",
                snippet="华东交通大学吧. 关注18.9w贴子786.1w.",
            )
        ]
    )
    reader = _FakeReader()  # 未提供结果：读取会按访问受限降级
    _wire(sqlite_app, port, reader=reader, official_reader=_FakeOfficialReader())
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, "华东交通大学吧 食堂 怎么样", module_id="tieba")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    tieba = assistant["tieba_research"]
    assert tieba["status"] == "links_only"
    assert tieba["confirmed_posts"] == []
    assert tieba["sections"] == []
    assert tieba["verification"]["consensus_claims_absent"] is True
    assert tieba["verification"]["unread_claims_absent"] is True
    assert "候选帖链" in assistant["content"]
    assert "未取得回复内容" in assistant["content"]
    assert "普遍" not in assistant["content"]


def test_resume_reuses_completed_search_reads_and_official_checks() -> None:
    """恢复轮沿用已完成的检索、读取与官方核验产物，不重复发起外部调用。"""
    analysis = parse_tieba_request("华东交通大学吧 食堂 怎么样")
    hit = TiebaSearchHit(url=CONFIRMED_URL, title="食堂怎么样", snippet="")
    record = query_record(
        query=plan_queries(analysis)[0],
        status=ModuleQueryStatus.SUCCESS,
        evidence_count=1,
    )
    search_port = _FakeSearchPort(status=ModuleQueryStatus.ERROR)
    attempts = evidence.search_candidates(
        search_port,
        "acc-27",
        analysis,
        stop_event=None,
        deadline_seconds=5.0,
        resume={
            "queries": [record.model_dump(mode="json")],
            "candidates": [hit.model_dump(mode="json")],
            "rejected": [],
            "rounds": ["上一轮已完成精确词查询。"],
            "raw_hits": 1,
        },
    )
    assert search_port.queries == []
    assert [item.url for item in attempts.hits] == [CONFIRMED_URL]
    assert attempts.partial is False

    post = TiebaPostProjection(
        thread_id="10745250786",
        url=CONFIRMED_URL,
        title="食堂怎么样",
        affiliation_evidence="已读取帖子页面，页面声明所属贴吧为「华东交通大学吧」",
        read_status=ReadStatus.READ,
        pages_read=1,
        pages_limit=2,
        floor_min=1,
        floor_max=3,
        replies_obtained=True,
        replies=list(REAL_REPLIES),
        retrieved_at=datetime(2026, 9, 25, tzinfo=UTC),
    )
    thread_reader = _FakeReader()
    reads = evidence.read_candidates(
        thread_reader,
        attempts,
        stop_event=None,
        deadline_seconds=5.0,
        resume={
            "confirmed": [post.model_dump(mode="json")],
            "attempted_urls": [CONFIRMED_URL],
        },
    )
    assert thread_reader.read_urls == []
    assert [item.url for item in reads.confirmed] == [CONFIRMED_URL]

    check = TiebaOfficialCheck(
        title="转专业管理办法-教务处",
        url=OFFICIAL_URL,
        host="jwc.ecjtu.edu.cn",
        fetched_at=datetime(2026, 9, 25, 3, 0, tzinfo=UTC),
        status=STATUS_VERIFIED,
        excerpt="转专业申请由教务处统一审批。",
        matched_terms=["转专业"],
    )
    official_reader = _FakeOfficialReader()
    check_port = _FakeSearchPort(status=ModuleQueryStatus.ERROR)
    checks = evidence.verify_official(
        check_port,
        official_reader,
        "acc-27",
        parse_tieba_request("华东交通大学吧 转专业 规定 流程"),
        stop_event=None,
        deadline_seconds=5.0,
        resume={"checks": [check.model_dump(mode="json")]},
    )
    # 已取得的官方页面直接续用，不再重复抓取（发现查询只用于补未抓取的候选）。
    assert official_reader.fetched == []
    assert [item.url for item in checks] == [OFFICIAL_URL]
