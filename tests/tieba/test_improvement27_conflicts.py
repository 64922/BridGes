"""Issue 27：官方规定与帖子冲突的时效和范围核验。"""

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
from bridges.tieba.official import STATUS_VERIFIED, official_query
from bridges.tieba.parsing import parse_tieba_request
from bridges.tieba.searching import plan_queries
from tests.tieba.support import (
    CONFIRMED_URL,
    OFFICIAL_URL,
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
            tieba_query: [TiebaSearchHit(url=CONFIRMED_URL, title="转专业经验", snippet="")],
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
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    tieba = assistant["tieba_research"]
    assert tieba["conflicts"][0]["resolution"] == "official_newer"
    assert tieba["conflicts"][0]["official_date"] == "2026年3月1日"
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
            tieba_query: [TiebaSearchHit(url=CONFIRMED_URL, title="转专业经验", snippet="")],
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
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

    tieba = assistant["tieba_research"]
    assert tieba["conflicts"][0]["resolution"] == "kept_both"
    assert tieba["verification"]["conflicts_disclosed"] is True
    assert "双方分列" in assistant["content"]
    assert "无法确认官方页面已替代帖中说法" in tieba["conflicts"][0]["time_basis"]
