"""Issue 27：未读内容边界与运行续接。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from bridges.contracts.modules import ModuleQueryStatus
from bridges.tieba.contracts import (
    ReadStatus,
    TiebaOfficialCheck,
    TiebaPostProjection,
    TiebaSearchHit,
)
from bridges.tieba.evidence import read_candidates
from bridges.tieba.evidence_official import verify_official
from bridges.tieba.evidence_search import search_candidates
from bridges.tieba.official import STATUS_VERIFIED
from bridges.tieba.parsing import parse_tieba_request
from bridges.tieba.searching import plan_queries, query_record
from tests.tieba.support import (
    CONFIRMED_URL,
    OFFICIAL_URL,
    REAL_REPLIES,
    _create_conversation,
    _FakeOfficialReader,
    _FakeReader,
    _FakeSearchPort,
    _register,
    _run_and_read,
    _send,
    _wire,
)


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
    assistant = _run_and_read(sqlite_app, client, generation_helpers["drive"], conversation_id)

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
    search_port = _FakeSearchPort()
    attempts = search_candidates(
        search_port,
        "acc-27",
        analysis,
        stop_event=None,
        deadline_seconds=5.0,
        resume={
            "records": [record.model_dump(mode="json")],
            "candidates": [hit.model_dump(mode="json")],
            "rejected": [],
            "rounds": ["上一轮已完成精确词查询。"],
            "raw_hits": 1,
            "usable": 1,
        },
    )
    assert search_port.queries == list(plan_queries(analysis)[1:])
    assert [item.url for item in attempts.hits] == [CONFIRMED_URL]
    assert attempts.diagnostics.usable == 1
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
    reads = read_candidates(
        thread_reader,
        attempts,
        analysis=analysis,
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
    checks = verify_official(
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
    assert [item.url for item in checks.checks] == [OFFICIAL_URL]
