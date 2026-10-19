"""工单 14：深入读取遇到账户删除时的公开降级原因。"""

from __future__ import annotations

from datetime import UTC, datetime

from bridges.tieba.contracts import (
    ReadStatus,
    TiebaQuestionAnalysis,
    TiebaReadResult,
    TiebaReply,
    TiebaSearchHit,
)
from bridges.tieba.evidence import read_candidates
from bridges.tieba.evidence_search import SearchAttempts
from bridges.tieba.parsing import parse_tieba_request
from bridges.tieba.reading import TiebaThreadReader
from bridges.tieba.searching import HitDiagnostics


def test_read_candidates_marks_remaining_links_after_deep_read_deletion() -> None:
    analysis: TiebaQuestionAnalysis = parse_tieba_request("华东交通大学吧 宿舍条件")
    hits = tuple(
        TiebaSearchHit(
            url=f"https://tieba.baidu.com/p/{index}",
            title="宿舍条件讨论",
            snippet="",
        )
        for index in range(1, 10)
    )
    attempts = SearchAttempts(
        hits=hits,
        rejected=(),
        records=[],
        diagnostics=HitDiagnostics(raw_hits=9, usable=9),
    )

    class Reader(TiebaThreadReader):
        def __init__(self) -> None:
            self.calls = 0

        def read(
            self,
            url: str,
            *,
            pages_limit: int = 2,
            stop_event: object | None = None,
            deadline: float | None = None,
        ) -> TiebaReadResult:
            del stop_event, deadline
            self.calls += 1
            if pages_limit > 1:
                return TiebaReadResult(
                    url=url,
                    thread_id=url.rsplit("/", 1)[-1],
                    status=ReadStatus.ERROR,
                    error_code="tieba_browser_account_deleting",
                    error_message="账户正在删除，贴吧专用会话不可继续使用。",
                    retrieved_at=datetime.now(UTC),
                )
            if self.calls == 1:
                return TiebaReadResult(
                    url=url,
                    thread_id=url.rsplit("/", 1)[-1],
                    status=ReadStatus.PARTIAL,
                    forum_name="华东交通大学吧",
                    title="宿舍条件讨论",
                    pages_read=1,
                    pages_limit=1,
                    total_pages=3,
                    replies=[TiebaReply(floor=1, content="宿舍有空调")],
                    retrieved_at=datetime.now(UTC),
                )
            return TiebaReadResult(
                url=url,
                thread_id=url.rsplit("/", 1)[-1],
                status=ReadStatus.READ,
                forum_name="其他大学吧",
                title="宿舍条件讨论",
                replies=[TiebaReply(floor=1, content="其他学校的讨论")],
                retrieved_at=datetime.now(UTC),
            )

    reader = Reader()
    outcome = read_candidates(
        reader,
        attempts,
        analysis=analysis,
        stop_event=None,
        deadline_seconds=10,
    )

    assert reader.calls == 9
    assert outcome.partial is True
    assert outcome.closeout_reason == "account_deleting"
    remaining = next(link for link in outcome.unconfirmed if link.url == hits[-1].url)
    assert remaining.unconfirmed_reason == "未读取：账户正在删除"
