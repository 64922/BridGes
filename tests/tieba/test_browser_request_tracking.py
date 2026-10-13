"""浏览器业务请求在发送前受门控，并在完成后输出脱敏结果。"""

from __future__ import annotations

import threading
from typing import Any

from bridges.tieba.browser_request_budget import BrowserRequestBudget
from bridges.tieba.browser_requests import BrowserRequestTracker


class _Request:
    def __init__(self, url: str, *, resource_type: str, method: str = "GET") -> None:
        self.url = url
        self.method = method
        self.resource_type = resource_type
        self.status = 200

    def response(self) -> Any:
        return type("Response", (), {"status": self.status})()


class _Route:
    def __init__(self, request: _Request) -> None:
        self.request = request
        self.continued = False
        self.aborted = False

    def continue_(self) -> None:
        self.continued = True

    def abort(self, _reason: str) -> None:
        self.aborted = True


def test_tieba_data_request_is_gated_and_result_omits_url() -> None:
    events: list[dict[str, Any]] = []
    tracker: BrowserRequestTracker

    def emit(event: object) -> None:
        assert isinstance(event, dict)
        events.append(event)
        if event.get("browser_request") == "started":
            tracker.decide(int(event["request_id"]), True)

    tracker = BrowserRequestTracker(threading.Event(), emit)
    route = _Route(
        _Request(
            "https://tieba.baidu.com/c/s/pc/sync?query=private",
            resource_type="fetch",
            method="POST",
        )
    )

    tracker._route(route)  # type: ignore[arg-type]
    tracker._request_finished(route.request)  # type: ignore[arg-type]

    assert route.continued
    assert not route.aborted
    assert [event["browser_request"] for event in events] == ["started", "finished"]
    assert events[0]["host"] == "tieba.baidu.com"
    assert events[0]["resource_type"] == "fetch"
    assert events[1]["outcome_code"] == "http_200"
    assert "query=private" not in repr(events)


def test_same_url_requests_are_attributed_by_request_identity() -> None:
    events: list[dict[str, Any]] = []
    tracker: BrowserRequestTracker

    def emit(event: object) -> None:
        assert isinstance(event, dict)
        events.append(event)
        if event.get("browser_request") == "started":
            tracker.decide(int(event["request_id"]), True)

    tracker = BrowserRequestTracker(threading.Event(), emit)
    url = "https://tieba.baidu.com/c/s/pc/sync"
    first = _Route(_Request(url, resource_type="fetch"))
    second = _Route(_Request(url, resource_type="fetch"))

    tracker._route(first)  # type: ignore[arg-type]
    tracker._route(second)  # type: ignore[arg-type]
    second.request.status = 201
    tracker._request_finished(second.request)  # type: ignore[arg-type]
    first.request.status = 503
    tracker._request_finished(first.request)  # type: ignore[arg-type]

    finished = [event for event in events if event["browser_request"] == "finished"]
    assert [event["request_id"] for event in finished] == [2, 1]
    assert [event["outcome_code"] for event in finished] == ["http_201", "http_503"]
    assert all(isinstance(event["duration_ms"], int) for event in finished)


def test_budget_denial_aborts_request_before_network() -> None:
    events: list[dict[str, Any]] = []
    tracker: BrowserRequestTracker

    def emit(event: object) -> None:
        assert isinstance(event, dict)
        events.append(event)
        if event.get("browser_request") == "started":
            tracker.decide(int(event["request_id"]), False)

    tracker = BrowserRequestTracker(threading.Event(), emit)
    route = _Route(
        _Request("https://passport.baidu.com/v2/", resource_type="document")
    )

    tracker._route(route)  # type: ignore[arg-type]

    assert route.aborted
    assert not route.continued
    assert len(events) == 1
    assert events[0]["host"] == "passport.baidu.com"


def test_non_data_resource_is_not_charged_as_browser_business_request() -> None:
    events: list[object] = []
    tracker = BrowserRequestTracker(threading.Event(), events.append)
    route = _Route(
        _Request("https://tieba.baidu.com/static/site.css", resource_type="stylesheet")
    )

    tracker._route(route)  # type: ignore[arg-type]

    assert route.continued
    assert not route.aborted
    assert events == []


class _Budget:
    def __init__(self, *, allowed: bool = True) -> None:
        self.allowed = allowed
        self.registered: list[tuple[str, str]] = []
        self.released: list[dict[str, Any]] = []

    def next_call_key(self, _prefix: str) -> str:
        return "call-1"

    def register_external(self, call_key: str, *, purpose: str) -> bool:
        self.registered.append((call_key, purpose))
        return self.allowed

    def release_external(self, call_key: str, **values: Any) -> None:
        self.released.append({"call_key": call_key, **values})


def test_request_budget_records_result_and_duration() -> None:
    budget = _Budget()
    sent: list[dict[str, Any]] = []
    ledger = BrowserRequestBudget(budget, sent.append)  # type: ignore[arg-type]

    assert ledger.handle(
        {
            "browser_request": "started",
            "request_id": 7,
            "host": "tieba.baidu.com",
            "method": "GET",
            "resource_type": "document",
        }
    )
    assert ledger.handle(
        {
            "browser_request": "finished",
            "request_id": 7,
            "outcome_code": "http_200",
            "duration_ms": 128,
        }
    )

    assert sent == [{"type": "request_decision", "request_id": 7, "allowed": True}]
    assert budget.registered == [
        ("call-1", "tieba_browser_tieba_baidu_com_document_get")
    ]
    assert budget.released == [
        {"call_key": "call-1", "outcome_code": "http_200", "duration_ms": 128}
    ]


def test_request_budget_denial_never_creates_an_active_call() -> None:
    budget = _Budget(allowed=False)
    sent: list[dict[str, Any]] = []
    ledger = BrowserRequestBudget(budget, sent.append)  # type: ignore[arg-type]

    allowed = ledger.handle(
        {
            "browser_request": "started",
            "request_id": 2,
            "host": "tieba.baidu.com",
            "method": "GET",
            "resource_type": "xhr",
        }
    )

    assert allowed is False
    assert sent[0]["allowed"] is False
    assert not ledger.has_pending
    assert budget.released == []


def test_request_budget_guard_blocks_before_ledger_registration() -> None:
    budget = _Budget()
    sent: list[dict[str, Any]] = []
    ledger = BrowserRequestBudget(
        budget,
        sent.append,
        request_guard=lambda: "tieba_browser_task_changed",
    )  # type: ignore[arg-type]

    allowed = ledger.handle(
        {
            "browser_request": "started",
            "request_id": 3,
            "host": "tieba.baidu.com",
            "method": "GET",
            "resource_type": "document",
        }
    )

    assert allowed is False
    assert ledger.denial_code == "tieba_browser_task_changed"
    assert budget.registered == []
    assert sent == [{"type": "request_decision", "request_id": 3, "allowed": False}]


def test_request_budget_stop_blocks_before_ledger_registration() -> None:
    budget = _Budget()
    sent: list[dict[str, Any]] = []
    stopped = threading.Event()
    stopped.set()
    ledger = BrowserRequestBudget(
        budget,
        sent.append,
        stop_event=stopped,
    )  # type: ignore[arg-type]

    allowed = ledger.handle(
        {
            "browser_request": "started",
            "request_id": 4,
            "host": "tieba.baidu.com",
            "method": "GET",
            "resource_type": "xhr",
        }
    )

    assert allowed is False
    assert ledger.denial_code == "tieba_browser_cancelled"
    assert budget.registered == []
    assert sent == [{"type": "request_decision", "request_id": 4, "allowed": False}]
