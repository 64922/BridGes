"""Issue 12：官方核验只在明确要求时追加，并与贴吧讨论分列保留。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from bridges.tieba.contracts import (
    ReadStatus,
    TiebaOfficialCheck,
    TiebaReply,
    TiebaSearchHit,
)
from bridges.tieba.evidence_official import assess_applicability
from bridges.tieba.official import (
    STATUS_FETCH_FAILED,
    STATUS_VERIFIED,
    official_query,
)
from bridges.tieba.parsing import parse_tieba_request
from bridges.tieba.searching import plan_queries
from tests.tieba.support import (
    CONFIRMED_URL,
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

OFFICIAL_URL = "https://jwc.ecjtu.edu.cn/info/1004/3042.htm"

DISCUSSION_QUESTION = "搜集华东交通大学吧关于住宿费的讨论"
VERIFICATION_QUESTION = "核实华东交通大学住宿费规定"
NON_POLICY_VERIFICATION_QUESTION = "核实华东交通大学宿舍条件"


def _official_check(
    *,
    title: str = "华东交通大学学生住宿费管理办法",
    excerpt: str = "住宿费按学年收取，具体标准按学校规定执行。",
    status: str = STATUS_VERIFIED,
    error_message: str | None = None,
) -> TiebaOfficialCheck:
    return TiebaOfficialCheck(
        title=title,
        url=OFFICIAL_URL,
        host="jwc.ecjtu.edu.cn",
        fetched_at=datetime(2026, 10, 19, tzinfo=UTC),
        status=status,
        excerpt=excerpt if status == STATUS_VERIFIED else None,
        matched_terms=["住宿费", "规定"] if status == STATUS_VERIFIED else [],
        error_code=None if status == STATUS_VERIFIED else "official_fetch_failed",
        error_message=error_message,
    )


def _fee_post() -> TiebaSearchHit:
    return TiebaSearchHit(
        url=CONFIRMED_URL,
        title="住宿费一年多少钱",
        snippet="华东交通大学吧",
    )


def _fee_reader() -> _FakeReader:
    return _FakeReader(
        {
            CONFIRMED_URL: _read_result(
                url=CONFIRMED_URL,
                status=ReadStatus.READ,
                title="住宿费一年多少钱",
                replies=(
                    TiebaReply(
                        floor=1,
                        posted_at="2026-09-01 10:00",
                        content="住宿费一年一千二，四人间，空调另算。",
                    ),
                ),
            )
        }
    )


def _send_and_read(
    sqlite_app: Any,
    client: TestClient,
    generation_helpers: dict[str, Any],
    text: str,
) -> tuple[dict[str, Any], _FakeSearchPort, _FakeOfficialReader]:
    _register(client)
    analysis = parse_tieba_request(text)
    official = _official_check()
    port = _FakeSearchPort(
        per_query={
            official_query(analysis): [
                TiebaSearchHit(url=OFFICIAL_URL, title=official.title, snippet="")
            ],
            plan_queries(analysis)[0]: [_fee_post()],
        }
    )
    official_reader = _FakeOfficialReader({OFFICIAL_URL: official})
    _install_tieba_service(
        sqlite_app,
        port=port,
        reader=_fee_reader(),
        official_reader=official_reader,
    )
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)
    _send(client, conversation_id, text, module_id="tieba")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )
    return assistant, port, official_reader


def test_collecting_fee_discussion_does_not_touch_official_source(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """「搜集讨论」只查贴吧；住宿费是规定类名词也不自动访问官网。"""
    _register(client)
    analysis = parse_tieba_request(DISCUSSION_QUESTION)
    port = _FakeSearchPort(
        per_query={plan_queries(analysis)[0]: [_fee_post()]}
    )
    official_reader = _FakeOfficialReader()
    _install_tieba_service(
        sqlite_app,
        port=port,
        reader=_fee_reader(),
        official_reader=official_reader,
    )
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, DISCUSSION_QUESTION, module_id="tieba")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    assert port.queries == list(plan_queries(analysis))
    assert official_reader.fetched == []
    assert assistant["tieba_research"]["official_check_requested"] is False
    assert assistant["tieba_research"]["official_checks"] == []
    assert "官方原文摘录" not in assistant["content"]
    assert "住宿费一年一千二" in assistant["content"]


def test_explicit_verification_adds_official_excerpt_and_keeps_discussion(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """明确要求核实规定：官网原文与吧友经历分列，二者都保留。"""
    assistant, port, official_reader = _send_and_read(
        sqlite_app, client, generation_helpers, VERIFICATION_QUESTION
    )

    assert official_reader.fetched == [OFFICIAL_URL]
    assert port.queries[0] == official_query(parse_tieba_request(VERIFICATION_QUESTION))
    assert "核实" not in port.queries[0]
    tieba = assistant["tieba_research"]
    assert tieba["official_check_requested"] is True
    check = tieba["official_checks"][0]
    assert check["status"] == "verified"
    assert check["applicability"]["applicable"] is True
    assert "官方原文摘录" in assistant["content"]
    assert "住宿费按学年收取" in assistant["content"]
    assert "住宿费一年一千二" in assistant["content"]


def test_official_fetch_failure_keeps_discussion_and_marks_unverified(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """官网读取失败：不冒充官方确认，贴吧讨论仍完整保留。"""
    _register(client)
    text = VERIFICATION_QUESTION
    analysis = parse_tieba_request(text)
    failed = _official_check(
        status=STATUS_FETCH_FAILED, error_message="官方页面读取失败，未取得原文"
    )
    port = _FakeSearchPort(
        per_query={
            official_query(analysis): [
                TiebaSearchHit(url=OFFICIAL_URL, title=failed.title, snippet="")
            ],
            plan_queries(analysis)[0]: [_fee_post()],
        }
    )
    official_reader = _FakeOfficialReader({OFFICIAL_URL: failed})
    _install_tieba_service(
        sqlite_app,
        port=port,
        reader=_fee_reader(),
        official_reader=official_reader,
    )
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, text, module_id="tieba")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    tieba = assistant["tieba_research"]
    assert tieba["official_checks"][0]["status"] == STATUS_FETCH_FAILED
    assert tieba["official_checks"][0]["excerpt"] is None
    assert "未核实" in (tieba["official_unverified_note"] or "")
    assert "住宿费一年一千二" in assistant["content"]
    assert "住宿费按学年收取" not in assistant["content"]


def test_expired_official_notice_is_not_confirmed_but_discussion_is_kept(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """官网页面已失效：保留页面与贴吧经历，但不列为当前官方确认。"""
    _register(client)
    text = VERIFICATION_QUESTION
    analysis = parse_tieba_request(text)
    expired = _official_check(
        title="关于2018年学生住宿费规定的通知",
        excerpt="住宿费标准自2018年9月1日至2018年9月30日受理，逾期不再办理。",
    )
    port = _FakeSearchPort(
        per_query={
            official_query(analysis): [
                TiebaSearchHit(url=OFFICIAL_URL, title=expired.title, snippet="")
            ],
            plan_queries(analysis)[0]: [_fee_post()],
        }
    )
    _install_tieba_service(
        sqlite_app,
        port=port,
        reader=_fee_reader(),
        official_reader=_FakeOfficialReader({OFFICIAL_URL: expired}),
    )
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(client, conversation_id, text, module_id="tieba")
    assistant = _run_and_read(
        sqlite_app, client, generation_helpers["drive"], conversation_id
    )

    tieba = assistant["tieba_research"]
    check = tieba["official_checks"][0]
    assert check["status"] == "verified"
    assert check["applicability"]["applicable"] is False
    assert "期限" in check["applicability"]["note"] or "失效" in check["applicability"]["note"]
    assert "未核实" in (tieba["official_unverified_note"] or "")
    assert "住宿费一年一千二" in assistant["content"]
    assert "经官方确认" not in assistant["content"]


def test_verification_of_non_policy_topic_does_not_add_official_source(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """「核实宿舍条件」是体验类核实，没有规定类名词就不追加官网。"""
    analysis = parse_tieba_request(NON_POLICY_VERIFICATION_QUESTION)
    assert analysis.official_topics == []
    assert analysis.needs_official_check is False

    _register(client)
    port = _FakeSearchPort(
        per_query={plan_queries(analysis)[0]: [_fee_post()]}
    )
    official_reader = _FakeOfficialReader()
    _install_tieba_service(
        sqlite_app,
        port=port,
        reader=_fee_reader(),
        official_reader=official_reader,
    )
    sqlite_app.state.chat_service._gateway = _gateway_with(_SilentAdapter())  # noqa: SLF001
    conversation_id = _create_conversation(client)

    _send(
        client,
        conversation_id,
        NON_POLICY_VERIFICATION_QUESTION,
        module_id="tieba",
    )
    _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    assert port.queries == list(plan_queries(analysis))
    assert official_reader.fetched == []


def test_verification_action_words_do_not_become_topic_terms() -> None:
    """核实动作是来源意图，不是检索名词；查询词里不得残留。"""
    analysis = parse_tieba_request(VERIFICATION_QUESTION)

    assert "核实" not in analysis.topic_terms
    assert all("核实" not in query for query in plan_queries(analysis))
    assert "核实" not in official_query(analysis)


def test_applicability_note_surfaces_publication_and_effective_basis() -> None:
    """适用性说明要点出页面日期/生效依据；没有时如实说明。"""
    analysis = parse_tieba_request(VERIFICATION_QUESTION)
    dated = _official_check(
        title="华东交通大学学生住宿费管理办法（2026年修订）",
        excerpt="本办法自2026年9月1日起执行。住宿费按学年收取。",
    )
    note = assess_applicability(dated, analysis).note
    assert "2026年9月1日" in note or "2026年" in note
    assert "生效" in note or "发布" in note or "日期" in note

    undated = _official_check(
        title="华东交通大学学生住宿费管理办法",
        excerpt="住宿费按学年收取。",
    )
    note = assess_applicability(undated, analysis).note
    assert "未标注" in note
