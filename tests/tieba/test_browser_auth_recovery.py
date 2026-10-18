"""贴吧浏览器认证等待与认证恢复回归。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bridges.contracts.modules import ModuleQueryStatus
from bridges.tieba.contracts import ReadStatus, TiebaReadResult, TiebaReply, TiebaSearchHit
from bridges.tieba.evidence import read_candidates
from bridges.tieba.evidence_search import SearchAttempts, search_candidates
from bridges.tieba.parsing import parse_tieba_request
from bridges.tieba.searching import HitDiagnostics, SearchOutcome, query_record


def test_read_auth_failure_is_waitable_and_retried_after_auth() -> None:
    url = "https://tieba.baidu.com/p/123"
    hit = TiebaSearchHit(url=url, title="宿舍条件讨论", snippet="")
    attempts = SearchAttempts(hits=(hit,), rejected=(), records=[])

    class Reader:
        calls = 0

        def read(self, requested_url: str, **_: Any) -> TiebaReadResult:
            self.calls += 1
            if self.calls == 1:
                return TiebaReadResult(
                    url=requested_url,
                    status=ReadStatus.ACCESS_RESTRICTED,
                    error_code="tieba_browser_auth_required",
                    error_message="需要登录",
                    retrieved_at=datetime.now(UTC),
                )
            return TiebaReadResult(
                url=requested_url,
                status=ReadStatus.READ,
                forum_name="华东交通大学吧",
                title="宿舍条件讨论",
                replies=[TiebaReply(content="宿舍热水不稳定")],
                retrieved_at=datetime.now(UTC),
            )

    reader = Reader()
    analysis = parse_tieba_request("华东交通大学吧 宿舍条件")
    first = read_candidates(
        reader,
        attempts,
        analysis=analysis,
        stop_event=None,
        deadline_seconds=5,
    )
    assert first.partial is True
    assert first.retry_urls == (url,)
    assert first.unconfirmed[0].read_error_code == "tieba_browser_auth_required"

    resumed = read_candidates(
        reader,
        attempts,
        analysis=analysis,
        stop_event=None,
        deadline_seconds=5,
        resume={
            "confirmed": [],
            "rejected": [],
            "unconfirmed": [item.model_dump(mode="json") for item in first.unconfirmed],
            "attempted_urls": list(first.attempted_urls),
            "retry_urls": list(first.retry_urls),
            "unreadable": first.unreadable,
        },
    )
    assert reader.calls == 2
    assert resumed.retry_urls == ()
    assert len(resumed.confirmed) == 1
    assert not resumed.unconfirmed


def test_auth_link_survives_candidate_link_limit() -> None:
    hits = tuple(
        TiebaSearchHit(
            url=f"https://tieba.baidu.com/p/{index}",
            title=f"宿舍条件讨论 {index}",
            snippet="",
        )
        for index in range(1, 8)
    )
    attempts = SearchAttempts(hits=hits, rejected=(), records=[])

    class Reader:
        def read(self, requested_url: str, **_: Any) -> TiebaReadResult:
            if requested_url.endswith("/7"):
                return TiebaReadResult(
                    url=requested_url,
                    status=ReadStatus.ACCESS_RESTRICTED,
                    error_code="tieba_browser_auth_required",
                    error_message="需要登录",
                    retrieved_at=datetime.now(UTC),
                )
            return TiebaReadResult(
                url=requested_url,
                status=ReadStatus.ACCESS_RESTRICTED,
                error_code="tieba_browser_unavailable",
                error_message="页面暂不可读",
                retrieved_at=datetime.now(UTC),
            )

    outcome = read_candidates(
        Reader(),
        attempts,
        analysis=parse_tieba_request("华东交通大学吧 宿舍条件"),
        stop_event=None,
        deadline_seconds=5,
    )

    assert outcome.unconfirmed_total == 7
    assert len(outcome.unconfirmed) == 6
    assert outcome.unconfirmed[-1].url == hits[-1].url
    assert outcome.unconfirmed[-1].read_error_code == "tieba_browser_auth_required"


def test_auth_search_failure_is_kept_as_a_partial_artifact_input() -> None:
    class Port:
        def search_public(self, _account_id: str, *, query: str, **_: Any) -> SearchOutcome:
            return SearchOutcome(
                record=query_record(
                    query=query,
                    status=ModuleQueryStatus.ERROR,
                    evidence_count=0,
                    error_code="tieba_browser_auth_required",
                    error_message="需要登录",
                    retryable=False,
                ),
                hits=(),
                candidates=(),
                rejected=(),
                diagnostics=HitDiagnostics(),
            )

    attempts = search_candidates(
        Port(),
        "account",
        parse_tieba_request("华东交通大学吧 宿舍条件"),
        stop_event=None,
        deadline_seconds=5,
    )

    assert attempts.partial is True
    assert attempts.records[0].error_code == "tieba_browser_auth_required"


def test_manual_auth_wait_extends_deadline_for_remaining_reads() -> None:
    hits = tuple(
        TiebaSearchHit(
            url=f"https://tieba.baidu.com/p/{index}",
            title=f"宿舍条件讨论 {index}",
            snippet="",
        )
        for index in (1, 2)
    )
    deadlines: list[float | None] = []

    class Reader:
        def read(self, requested_url: str, **kwargs: Any) -> TiebaReadResult:
            deadlines.append(kwargs.get("deadline"))
            return TiebaReadResult(
                url=requested_url,
                status=ReadStatus.READ,
                forum_name="华东交通大学吧",
                title="宿舍条件讨论",
                replies=[TiebaReply(content="宿舍条件讨论")],
                excluded_wait_ms=60_000 if len(deadlines) == 1 else 0,
                retrieved_at=datetime.now(UTC),
            )

    read_candidates(
        Reader(),
        SearchAttempts(hits=hits, rejected=(), records=[]),
        analysis=parse_tieba_request("华东交通大学吧 宿舍条件"),
        stop_event=None,
        deadline_seconds=5,
    )

    assert deadlines[0] is not None and deadlines[1] is not None
    assert deadlines[1] - deadlines[0] >= 59
