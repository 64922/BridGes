"""Tieba budgets cover each outbound provider and page request."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from time import monotonic, sleep

import httpx
import pytest
from pydantic import SecretStr

from bridges.external_call_decision import ExternalCallDecision
from bridges.tieba.budget import (
    BudgetedOfficialReader,
    BudgetedReader,
    TiebaBudget,
    bind_request_budget,
    bind_request_guard,
)
from bridges.tieba.contracts import ReadStatus
from bridges.tieba.http_requests import MAX_SOURCE_REDIRECTS, TiebaHttpRequestError, tracked_get
from bridges.tieba.official import STATUS_VERIFIED, HttpOfficialSiteReader
from bridges.tieba.reading import HttpTiebaThreadReader
from bridges.web_search.client import WebSearchError
from bridges.web_search.service_runtime import _submit_daemon
from bridges.web_search.tavily import TavilySearchClient


class _Ledger:
    def __init__(self) -> None:
        self.allow_requests = True
        self.registered: list[dict[str, object]] = []
        self.released: list[dict[str, object]] = []

    def register_external_call_decision(
        self, **values: object
    ) -> ExternalCallDecision:
        if self.allow_requests:
            self.registered.append(values)
        return ExternalCallDecision(
            allowed=self.allow_requests,
            reason="allowed" if self.allow_requests else "work_time_exhausted",
        )

    def record_external_call_denial(self, **_values: object) -> None:
        return None

    def load(self, _account_id: str, _run_id: str) -> None:
        return None

    def record_external_call_result(self, **values: object) -> None:
        self.released.append(values)


def _budget(
    ledger: _Ledger, *, request_guard: Callable[[], str | None] | None = None
) -> TiebaBudget:
    return TiebaBudget(
        ledger=ledger,  # type: ignore[arg-type]
        account_id="account-1",
        run_id="run-1",
        work_deadline=datetime.now(UTC) + timedelta(minutes=5),
        request_guard=request_guard,
    )


def test_tavily_search_and_extract_are_each_recorded_from_worker_thread() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/search"):
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "宿舍讨论",
                            "url": "https://example.edu/topic",
                            "content": "住宿情况",
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://example.edu/topic",
                        "raw_content": "页面正文",
                    }
                ]
            },
        )

    ledger = _Ledger()
    client = TavilySearchClient(
        api_key=SecretStr("test-key"),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with bind_request_budget(_budget(ledger)):
        future = _submit_daemon(
            lambda: client.search("宿舍条件", fetch_sources=True),
            name="test-tavily-request-budget",
        )
        result = future.result(timeout=3).value

    assert not isinstance(result, BaseException)
    assert calls == ["/search", "/extract"]
    assert [call["purpose"] for call in ledger.registered] == [
        "tavily_search",
        "tavily_extract",
    ]
    assert len(ledger.released) == 2
    assert [call["outcome_code"] for call in ledger.released] == [
        "http_200",
        "http_200",
    ]
    assert all(call["duration_ms"] is not None for call in ledger.released)


@pytest.mark.parametrize(
    ("status_code", "expected_error_code"),
    [(429, "web_search_rate_limit"), (503, "web_search_provider")],
)
def test_tavily_error_response_status_is_preserved_in_request_ledger(
    status_code: int, expected_error_code: str
) -> None:
    ledger = _Ledger()
    client = TavilySearchClient(
        api_key=SecretStr("test-key"),
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(status_code, request=request)
            )
        ),
    )

    with bind_request_budget(_budget(ledger)), pytest.raises(WebSearchError) as error:
        client.search("宿舍条件", fetch_sources=False)

    assert error.value.code == expected_error_code
    assert len(ledger.registered) == len(ledger.released) == 1
    assert ledger.released[0]["outcome_code"] == f"http_{status_code}"


def test_tavily_body_read_failure_is_not_recorded_as_complete_http_success() -> None:
    class BrokenBody(httpx.SyncByteStream):
        def __iter__(self):
            yield b'{"results":['
            raise httpx.ReadError("simulated response body failure")

        def close(self) -> None:
            return None

    ledger = _Ledger()
    client = TavilySearchClient(
        api_key=SecretStr("test-key"),
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, stream=BrokenBody(), request=request
                )
            )
        ),
    )

    with bind_request_budget(_budget(ledger)), pytest.raises(WebSearchError) as error:
        client.search("宿舍条件", fetch_sources=False)

    assert error.value.code == "web_search_offline"
    assert len(ledger.registered) == len(ledger.released) == 1
    assert ledger.released[0]["outcome_code"] == "http_200_request_failed"


def test_official_redirect_hops_are_metered_and_stay_on_official_domain() -> None:
    calls: list[str] = []
    html = "<html><title>住宿通知</title><main><p>宿舍管理办法正文</p></main></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path == "/start":
            return httpx.Response(
                302,
                headers={"location": "https://jwc.ecjtu.edu.cn/notice"},
                request=request,
            )
        return httpx.Response(200, text=html, request=request)

    ledger = _Ledger()
    reader = HttpOfficialSiteReader(
        client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    result = BudgetedOfficialReader(reader, _budget(ledger)).fetch(
        "https://jwc.ecjtu.edu.cn/start", terms=("宿舍",)
    )

    assert result.status == STATUS_VERIFIED
    assert len(calls) == 2
    assert [call["purpose"] for call in ledger.registered] == [
        "tieba_official_fetch",
        "tieba_official_fetch",
    ]
    assert len(ledger.released) == 2
    assert [call["outcome_code"] for call in ledger.released] == [
        "http_302",
        "http_200",
    ]


def test_official_reader_rejects_redirect_outside_school_domain() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            302,
            headers={"location": "https://example.net/off-domain"},
            request=request,
        )

    ledger = _Ledger()
    reader = HttpOfficialSiteReader(
        client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    result = BudgetedOfficialReader(reader, _budget(ledger)).fetch(
        "https://jwc.ecjtu.edu.cn/start", terms=("宿舍",)
    )

    assert result.error_code == "tieba_external_redirect_rejected"
    assert len(calls) == 1
    assert len(ledger.registered) == len(ledger.released) == 1
    assert ledger.released[0]["outcome_code"] == "http_302"


def test_http_error_status_is_recorded_for_the_physical_request() -> None:
    ledger = _Ledger()
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(503, request=request)
        )
    )

    with bind_request_budget(_budget(ledger)):
        response = tracked_get(
            client,
            "https://tieba.baidu.com/p/123",
            purpose="tieba_thread_read",
            timeout=1,
            allowed_url=lambda url: url.startswith("https://tieba.baidu.com/"),
        )

    assert response.status_code == 503
    assert len(ledger.registered) == len(ledger.released) == 1
    assert ledger.released[0]["outcome_code"] == "http_503"


def test_timeout_releases_the_physical_request_ledger_entry() -> None:
    ledger = _Ledger()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("simulated timeout", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with (
        bind_request_budget(_budget(ledger)),
        pytest.raises(httpx.ReadTimeout),
    ):
        tracked_get(
            client,
            "https://tieba.baidu.com/p/123",
            purpose="tieba_thread_read",
            timeout=1,
            allowed_url=lambda url: url.startswith("https://tieba.baidu.com/"),
        )

    assert len(ledger.registered) == len(ledger.released) == 1
    assert ledger.released[0]["outcome_code"] == "timeout"
    assert ledger.released[0]["duration_ms"] is not None


def test_redirect_limit_records_every_physical_hop() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        next_path = f"/redirect-{len(calls)}"
        return httpx.Response(
            302,
            headers={"location": next_path},
            request=request,
        )

    ledger = _Ledger()
    client = httpx.Client(transport=httpx.MockTransport(handler))
    with (
        bind_request_budget(_budget(ledger)),
        pytest.raises(TiebaHttpRequestError) as error,
    ):
        tracked_get(
            client,
            "https://tieba.baidu.com/p/123",
            purpose="tieba_thread_read",
            timeout=1,
            allowed_url=lambda url: url.startswith("https://tieba.baidu.com/"),
        )

    assert error.value.code == "tieba_external_redirect_limit"
    assert len(calls) == MAX_SOURCE_REDIRECTS + 1
    assert len(ledger.registered) == len(ledger.released) == len(calls)
    assert all(call["outcome_code"] == "http_302" for call in ledger.released)


def test_malformed_redirect_url_is_classified_and_recorded() -> None:
    ledger = _Ledger()
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                302,
                headers={"location": "http://[::1"},
                request=request,
            )
        )
    )
    with (
        bind_request_budget(_budget(ledger)),
        pytest.raises(TiebaHttpRequestError) as error,
    ):
        tracked_get(
            client,
            "https://tieba.baidu.com/p/123",
            purpose="tieba_thread_read",
            timeout=1,
            allowed_url=lambda url: url.startswith("https://tieba.baidu.com/"),
        )

    assert error.value.code == "tieba_external_redirect_rejected"
    assert len(ledger.registered) == len(ledger.released) == 1
    assert ledger.released[0]["outcome_code"] == "tieba_external_redirect_rejected"


def test_redirect_hop_cannot_extend_past_read_deadline() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        sleep(0.03)
        return httpx.Response(
            302,
            headers={"location": "/next"},
            request=request,
        )

    ledger = _Ledger()
    client = httpx.Client(transport=httpx.MockTransport(handler))
    with (
        bind_request_budget(_budget(ledger)),
        pytest.raises(httpx.TimeoutException),
    ):
        tracked_get(
            client,
            "https://tieba.baidu.com/p/123",
            purpose="tieba_thread_read",
            timeout=1,
            allowed_url=lambda url: url.startswith("https://tieba.baidu.com/"),
            deadline=monotonic() + 0.01,
        )

    assert len(calls) == 1
    assert len(ledger.registered) == len(ledger.released) == 1
    assert ledger.released[0]["outcome_code"] == "timeout"


def test_final_http_response_after_read_deadline_is_discarded() -> None:
    ledger = _Ledger()

    def handler(request: httpx.Request) -> httpx.Response:
        sleep(0.03)
        return httpx.Response(200, text="late response", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with (
        bind_request_budget(_budget(ledger)),
        pytest.raises(httpx.TimeoutException),
    ):
        tracked_get(
            client,
            "https://tieba.baidu.com/p/123",
            purpose="tieba_thread_read",
            timeout=1,
            allowed_url=lambda url: url.startswith("https://tieba.baidu.com/"),
            deadline=monotonic() + 0.01,
        )

    assert len(ledger.registered) == len(ledger.released) == 1
    assert ledger.released[0]["outcome_code"] == "timeout"


def test_thread_pages_and_redirect_hops_are_each_recorded() -> None:
    calls: list[tuple[str, str]] = []
    page_data = {
        "forum": {"name": "华东交通大学"},
        "thread": {"title": "宿舍条件"},
        "page": {"total_page": 2},
        "post_list": [],
    }
    html = f"<script>var PageData = {json.dumps(page_data, ensure_ascii=False)};</script>"

    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("pn", "")
        calls.append((request.url.host, page))
        if request.url.host == "tieba.baidu.com" and page == "1":
            return httpx.Response(
                302,
                headers={"location": "https://www.tieba.baidu.com/p/123"},
                request=request,
            )
        return httpx.Response(200, text=html, request=request)

    ledger = _Ledger()
    reader = HttpTiebaThreadReader(
        client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    result = BudgetedReader(reader, _budget(ledger)).read(
        "https://tieba.baidu.com/p/123", pages_limit=2
    )

    assert result.status is ReadStatus.READ
    assert result.pages_read == 2
    assert calls == [
        ("tieba.baidu.com", "1"),
        ("www.tieba.baidu.com", "1"),
        ("tieba.baidu.com", "2"),
    ]
    assert [call["purpose"] for call in ledger.registered] == [
        "tieba_thread_read",
        "tieba_thread_read",
        "tieba_thread_read",
    ]
    assert len(ledger.released) == 3
    assert [call["outcome_code"] for call in ledger.released] == [
        "http_302",
        "http_200",
        "http_200",
    ]


def test_tavily_request_guard_blocks_before_http() -> None:
    calls: list[httpx.Request] = []
    client = TavilySearchClient(
        api_key=SecretStr("test-key"),
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: calls.append(request) or httpx.Response(200, json={})
            )
        ),
    )

    with (
        bind_request_guard(lambda: "task_version_changed"),
        pytest.raises(WebSearchError) as error,
    ):
        client.search("宿舍条件", fetch_sources=False)

    assert error.value.code == "task_version_changed"
    assert calls == []


def test_tavily_budget_exhaustion_blocks_before_http() -> None:
    calls: list[httpx.Request] = []
    client = TavilySearchClient(
        api_key=SecretStr("test-key"),
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: calls.append(request) or httpx.Response(200, json={})
            )
        ),
    )
    ledger = _Ledger()
    ledger.allow_requests = False

    with bind_request_budget(_budget(ledger)), pytest.raises(WebSearchError) as error:
        client.search("宿舍条件", fetch_sources=False)

    assert error.value.code == "run_budget_exhausted"
    assert calls == []
    assert ledger.registered == []
    assert ledger.released == []
