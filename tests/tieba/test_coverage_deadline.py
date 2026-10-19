"""工单 09：跨帖首轮覆盖、墙钟截止与运行节奏记录。"""

from __future__ import annotations

import queue
import threading
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from bridges.contracts.modules import ModuleQueryStatus
from bridges.tieba.browser_errors import TiebaBrowserError
from bridges.tieba.browser_receive import receive_result
from bridges.tieba.browser_request_budget import BrowserRequestBudget
from bridges.tieba.browser_runtime import (
    BROWSER_CLEANUP_TOLERANCE_SECONDS,
    TiebaBrowserRuntime,
)
from bridges.tieba.budget import TiebaBudget
from bridges.tieba.contracts import (
    ReadStatus,
    TiebaReadResult,
    TiebaReply,
    TiebaSearchHit,
    TiebaTimeFilter,
)
from bridges.tieba.evidence import read_candidates
from bridges.tieba.evidence_search import SearchAttempts, search_candidates
from bridges.tieba.execution_metrics import request_type_of
from bridges.tieba.http_requests import tracked_get
from bridges.tieba.node_synthesis import _run_metadata
from bridges.tieba.parsing import parse_tieba_request
from bridges.tieba.run_metadata import TiebaBrowserCleanup, TiebaRequestBreakdown
from bridges.tieba.searching import HitDiagnostics, SearchOutcome, query_record

URLS = [f"https://tieba.baidu.com/p/{index}" for index in range(1, 5)]


def _hit(index: int) -> TiebaSearchHit:
    return TiebaSearchHit(url=URLS[index], title="宿舍条件讨论", snippet="")


def _analysis():
    return parse_tieba_request("华东交通大学吧 宿舍条件")


def _read_result(
    url: str,
    *,
    pages_limit: int,
    pages_read: int,
    total_pages: int = 3,
    content: str = "宿舍条件还不错",
) -> TiebaReadResult:
    return TiebaReadResult(
        url=url,
        thread_id=url.rsplit("/", 1)[-1],
        status=ReadStatus.READ if pages_read >= total_pages else ReadStatus.PARTIAL,
        forum_name="华东交通大学吧",
        title="宿舍条件讨论",
        pages_read=pages_read,
        pages_limit=pages_limit,
        pages_complete=pages_read >= total_pages,
        total_pages=total_pages,
        replies=[TiebaReply(floor=index + 1, content=content) for index in range(pages_read)],
        retrieved_at=datetime.now(UTC),
    )


class _PagesReader:
    def __init__(self, *, deep_status: ReadStatus | None = None) -> None:
        self.calls: list[tuple[str, int, float | None]] = []
        self.deep_status = deep_status

    def read(
        self,
        url: str,
        *,
        pages_limit: int = 2,
        stop_event: object | None = None,
        deadline: float | None = None,
    ) -> TiebaReadResult:
        del stop_event
        self.calls.append((url, pages_limit, deadline))
        if pages_limit > 1 and self.deep_status is not None:
            return TiebaReadResult(
                url=url,
                status=self.deep_status,
                error_code="tieba_read_timeout",
                error_message="深入读取超时",
                retrieved_at=datetime.now(UTC),
            )
        pages_read = 1 if pages_limit == 1 else 2
        return _read_result(url, pages_limit=pages_limit, pages_read=pages_read)


class _DeadlineReader:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def read(
        self,
        url: str,
        *,
        pages_limit: int = 2,
        stop_event: object | None = None,
        deadline: float | None = None,
    ) -> TiebaReadResult:
        del pages_limit, stop_event
        self.urls.append(url)
        if deadline is not None:
            time.sleep(max(0.0, deadline - time.monotonic()))
        return _read_result(url, pages_limit=1, pages_read=1)


def test_first_pass_covers_multiple_posts_before_deepening() -> None:
    reader = _PagesReader()

    outcome = read_candidates(
        reader,
        SearchAttempts(hits=(_hit(0), _hit(1)), rejected=(), records=[]),
        analysis=_analysis(),
        stop_event=None,
        deadline_seconds=10,
    )

    assert [limit for _, limit, _ in reader.calls[:2]] == [1, 1]
    assert any(limit == 2 for _, limit, _ in reader.calls[2:])
    assert outcome.first_pass_read_count == 2
    assert outcome.deepened_post_count == 2
    assert outcome.closeout_reason == "completed"
    assert len(outcome.confirmed) == 2


def test_slow_post_does_not_starve_later_first_pass_reads() -> None:
    reader = _DeadlineReader()

    outcome = read_candidates(
        reader,
        SearchAttempts(hits=(_hit(0), _hit(1), _hit(2)), rejected=(), records=[]),
        analysis=_analysis(),
        stop_event=None,
        deadline_seconds=0.6,
    )

    assert reader.urls == URLS[:3]
    assert outcome.first_pass_read_count == 3
    assert outcome.closeout_reason == "evidence_sufficient"


def test_sufficient_first_pass_coverage_skips_deepening() -> None:
    reader = _PagesReader()

    outcome = read_candidates(
        reader,
        SearchAttempts(hits=(_hit(0), _hit(1), _hit(2)), rejected=(), records=[]),
        analysis=_analysis(),
        stop_event=None,
        deadline_seconds=10,
    )

    assert [limit for _, limit, _ in reader.calls] == [1, 1, 1]
    assert outcome.first_pass_read_count == 3
    assert outcome.deepened_post_count == 0
    assert outcome.closeout_reason == "evidence_sufficient"


def test_failed_deepening_keeps_the_successful_shallow_page() -> None:
    reader = _PagesReader(deep_status=ReadStatus.ERROR)

    outcome = read_candidates(
        reader,
        SearchAttempts(hits=(_hit(0),), rejected=(), records=[]),
        analysis=_analysis(),
        stop_event=None,
        deadline_seconds=10,
    )

    assert len(outcome.confirmed) == 1
    assert outcome.confirmed[0].pages_read == 1
    assert outcome.confirmed[0].replies
    assert outcome.deepened_post_count == 0
    assert outcome.closeout_reason == "deepen_incomplete"


class _RateLimitedSearch:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def search_public(
        self,
        _account_id: str,
        *,
        query: str,
        reason: str,
        stop_event: object | None = None,
        deadline: float | None = None,
    ) -> SearchOutcome:
        del reason, stop_event, deadline
        self.queries.append(query)
        return SearchOutcome(
            record=query_record(
                query=query,
                status=ModuleQueryStatus.RATE_LIMITED,
                evidence_count=0,
                error_code="web_search_rate_limit",
                error_message="上游限流",
                retryable=True,
            ),
            hits=(),
            candidates=(),
            rejected=(),
            diagnostics=HitDiagnostics(),
        )


def test_rate_limit_stops_planned_queries_without_fallback() -> None:
    port = _RateLimitedSearch()

    attempts = search_candidates(
        port,
        "account-1",
        _analysis(),
        stop_event=None,
        deadline_seconds=10,
    )

    assert len(port.queries) == 1
    assert attempts.closeout_reason == "rate_limited"
    assert attempts.partial is True


class _SlowStream(httpx.SyncByteStream):
    def __init__(self) -> None:
        self.yielded = 0

    def __iter__(self):
        while self.yielded < 100:
            time.sleep(0.02)
            self.yielded += 1
            yield b"x"

    def close(self) -> None:
        return None


def test_slow_chunked_response_is_stopped_at_the_wall_clock_deadline() -> None:
    stream = _SlowStream()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    started = time.monotonic()
    with pytest.raises(httpx.TimeoutException):
        tracked_get(
            client,
            URLS[0],
            purpose="tieba_thread_read",
            timeout=1,
            allowed_url=lambda url: url.startswith("https://tieba.baidu.com/"),
            deadline=started + 0.08,
            max_bytes=1000,
        )

    assert time.monotonic() - started < 0.5
    assert stream.yielded < 100


class _QueueBudget:
    def __init__(self) -> None:
        self.active = 0
        self.released: list[dict[str, Any]] = []
        self.denied: list[dict[str, Any]] = []

    def next_call_key(self, _prefix: str) -> str:
        return f"call-{self.active + len(self.released) + len(self.denied) + 1}"

    def register_external_decision(self, _call_key: str, *, purpose: str) -> Any:
        del purpose
        from bridges.external_call_decision import ExternalCallDecision

        if self.active >= 2:
            return ExternalCallDecision(
                allowed=False,
                reason="capacity",
                active=self.active,
                limit=2,
                remaining_ms=100_000,
            )
        self.active += 1
        return ExternalCallDecision(
            allowed=True,
            reason="allowed",
            active=self.active,
            limit=2,
            remaining_ms=100_000,
        )

    def release_external(self, call_key: str, **values: Any) -> None:
        self.active = max(0, self.active - 1)
        self.released.append({"call_key": call_key, **values})

    def record_external_call_denial(self, call_key: str | None, **values: Any) -> None:
        self.denied.append({"call_key": call_key, **values})

    def remaining_work_ms(self) -> int:
        return 100_000


def _started(request_id: int) -> dict[str, Any]:
    return {
        "browser_request": "started",
        "request_id": request_id,
        "host": "tieba.baidu.com",
        "method": "GET",
        "resource_type": "xhr",
    }


def _finished(request_id: int) -> dict[str, Any]:
    return {
        "browser_request": "finished",
        "request_id": request_id,
        "outcome_code": "http_200",
        "duration_ms": 10,
    }


def test_browser_queue_wait_is_measured_and_recorded() -> None:
    budget = _QueueBudget()
    scheduler = BrowserRequestBudget(
        budget,  # type: ignore[arg-type]
        lambda _payload: None,
        deadline=time.monotonic() + 5,
    )

    assert scheduler.handle(_started(1)) is True
    assert scheduler.handle(_started(2)) is True
    assert scheduler.handle(_started(3)) is None
    assert scheduler.handle(_finished(1)) is True
    assert scheduler.handle(_finished(3)) is True
    assert scheduler.handle(_finished(2)) is True

    queued = next(item for item in budget.released if item["call_key"] == "call-3")
    assert "queue_ms" in queued
    assert queued["queue_ms"] >= 0


class _IdleWorker:
    def __init__(self) -> None:
        self.output: queue.Queue[str | None] = queue.Queue()


def test_worker_without_output_times_out_without_unbounded_wait() -> None:
    started = time.monotonic()

    with pytest.raises(TiebaBrowserError) as error:
        receive_result(
            _IdleWorker(),
            stop_event=threading.Event(),
            deadline=time.monotonic() + 0.12,
            request_budget=None,
            request_guard=None,
            send=lambda _payload: None,
        )

    assert error.value.code == "tieba_browser_timeout"
    assert time.monotonic() - started < 1


class _MetricsLedger:
    def register_external_call_decision(self, **_values: Any) -> Any:
        from bridges.external_call_decision import ExternalCallDecision

        return ExternalCallDecision(allowed=True, reason="allowed")

    def record_external_call_denial(self, **_values: Any) -> None:
        return None

    def record_external_call_result(self, **_values: Any) -> None:
        return None

    def load(self, _account_id: str, _run_id: str) -> None:
        return None


def test_cleanup_tolerance_is_defined_and_measured(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    budget = TiebaBudget(
        ledger=_MetricsLedger(),  # type: ignore[arg-type]
        account_id="account-1",
        run_id="run-1",
        work_deadline=datetime.now(UTC) + timedelta(minutes=5),
    )
    runtime = TiebaBrowserRuntime(tmp_path)
    worker = SimpleNamespace()
    monkeypatch.setattr(runtime, "_dispose", lambda _worker: 25)

    runtime._drop_worker("account-1", worker, request_budget=budget)  # type: ignore[arg-type]

    summary = budget.browser_cleanup_summary()
    assert BROWSER_CLEANUP_TOLERANCE_SECONDS >= 8
    assert summary is not None
    assert summary.count == 1
    assert summary.max_ms == 25
    assert summary.timed_out == 0


def test_run_metadata_records_coverage_closeout_timings_and_requests() -> None:
    started = datetime(2026, 10, 18, 10, 0, tzinfo=UTC)
    finished = started + timedelta(seconds=4)
    metadata = _run_metadata(
        SimpleNamespace(run_id="run-1"),
        attempts=SimpleNamespace(records=[object()], closeout_reason="rate_limited"),
        reads=SimpleNamespace(
            attempted_urls=[URLS[0], URLS[1]],
            first_pass_read_count=2,
            deepened_post_count=1,
            closeout_reason="deadline",
        ),
        checks=[object()],
        confirmed=[],
        time_filter=TiebaTimeFilter(note="未指定时间"),
        started_at=started,
        finished_at=finished,
        model_calls=1,
        official_closeout_reason="completed",
        stage_timings_ms={"tieba.search": 1200, "tieba.read": 2400},
        remaining_work_ms=9000,
        request_breakdown=[
            TiebaRequestBreakdown(
                request_type="read",
                purpose="tieba_thread_read",
                count=2,
                network_ms=800,
                queued_ms=50,
            )
        ],
        browser_cleanup=TiebaBrowserCleanup(
            count=1, max_ms=25, tolerance_ms=13_000, timed_out=0
        ),
    )

    assert metadata.first_pass_post_count == 2
    assert metadata.deepened_post_count == 1
    assert metadata.closeout_reasons == {
        "search": "rate_limited",
        "read": "deadline",
        "official": "completed",
    }
    assert metadata.stage_timings_ms == {"tieba.search": 1200, "tieba.read": 2400}
    assert metadata.remaining_work_ms == 9000
    assert metadata.physical_request_count == 2
    assert metadata.request_breakdown[0].queued_ms == 50
    assert metadata.browser_cleanup is not None
    assert metadata.browser_cleanup.tolerance_ms == 13_000


def test_request_type_mapping_covers_ledger_purposes() -> None:
    assert request_type_of("tieba.search") == "search"
    assert request_type_of("tieba_read") == "read"
    assert request_type_of("tieba_thread_read") == "read"
    assert request_type_of("tieba_official_fetch") == "official"
    assert request_type_of("tieba_browser_tieba_baidu_com_document_get") == "browser"
