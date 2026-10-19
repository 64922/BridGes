"""运行元数据记录真实调用计数、覆盖范围与组件版本。"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from bridges.tieba.contracts import ReadStatus, TiebaPostProjection, TiebaReply
from bridges.tieba.node_synthesis import _run_metadata, _unread_scope


def _post() -> TiebaPostProjection:
    return TiebaPostProjection(
        thread_id="123",
        url="https://tieba.baidu.com/p/123",
        title="宿舍条件",
        affiliation_evidence="已读取帖子页面，页面标题为华东交通大学吧。",
        read_status=ReadStatus.PARTIAL,
        pages_read=1,
        pages_limit=1,
        total_pages=2,
        total_replies=9,
        floor_min=1,
        floor_max=3,
        replies_obtained=True,
        replies=[
            TiebaReply(reply_id="100", floor=1, author="楼主甲", content="主帖"),
            TiebaReply(reply_id="101", floor=2, author="吧友乙", content="回复一"),
            TiebaReply(reply_id="102", floor=3, author=None, content="回复二"),
        ],
        retrieved_at=datetime(2026, 10, 18, tzinfo=UTC),
    )


def test_run_metadata_records_counts_versions_and_unread_scope() -> None:
    started = datetime(2026, 10, 18, 10, 0, tzinfo=UTC)
    finished = datetime(2026, 10, 18, 10, 0, 3, tzinfo=UTC)
    metadata = _run_metadata(
        SimpleNamespace(run_id="run-1"),
        attempts=SimpleNamespace(records=[object(), object()]),
        reads=SimpleNamespace(attempted_urls=["https://tieba.baidu.com/p/123"]),
        checks=[object()],
        confirmed=[_post()],
        started_at=started,
        finished_at=finished,
        model_calls=3,
    )

    assert metadata.run_id == "run-1"
    assert metadata.elapsed_ms == 3000
    assert metadata.request_count == 4
    assert metadata.search_request_count == 2
    assert metadata.read_request_count == 1
    assert metadata.official_request_count == 1
    assert metadata.model_call_count == 3
    assert metadata.confirmed_post_count == 1
    assert metadata.read_reply_count == 3
    assert metadata.source_urls == ["https://tieba.baidu.com/p/123"]
    assert metadata.component_versions["browser_protocol"] == "tieba-browser-protocol-v3"
    assert any("页面声明共 2 页" in note for note in metadata.unread_scope)
    assert any("9 条回复" in note for note in metadata.unread_scope)
    assert any("本轮记录 3 条楼层" in note for note in metadata.unread_scope)


def test_unread_scope_states_when_no_post_was_confirmed() -> None:
    notes = _unread_scope([])
    assert notes == ["本轮没有确认任何帖子，未读范围覆盖全部候选帖链。"]
