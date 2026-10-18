"""为浏览器中的贴吧业务网络请求提供账本闸门。"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from playwright.sync_api import BrowserContext, Request, Route
from playwright.sync_api import Error as PlaywrightError

_BILLABLE_RESOURCE_TYPES = {"document", "xhr", "fetch"}


@dataclass(frozen=True)
class _PendingRequest:
    request: Request
    request_id: int
    started_ns: int


class BrowserRequestTracker:
    """只闸控页面导航和数据请求，不记录URL、查询参数、正文或请求头。"""

    def __init__(self, closed: threading.Event, emit: Callable[[object], None]) -> None:
        self._closed = closed
        self._emit = emit
        self._lock = threading.Lock()
        self._next_id = 0
        self._decisions: dict[int, queue.Queue[bool]] = {}
        self._pending: dict[int, _PendingRequest] = {}
        self._context: BrowserContext | None = None
        self._deadline: float | None = None

    def install(self, context: BrowserContext) -> None:
        self._context = context
        context.route("**/*", self._route)
        context.on("requestfinished", self._request_finished)
        context.on("requestfailed", self._request_failed)

    def set_deadline(self, deadline: float | None) -> None:
        """设置当前动作的单调截止；等待许可时不会超过它。"""
        self._deadline = deadline

    def decide(self, request_id: int, allowed: bool) -> None:
        with self._lock:
            answer = self._decisions.get(request_id)
        if answer is not None:
            answer.put(allowed)

    def wait_until_idle(self, context: BrowserContext, deadline: float | None) -> None:
        stop_at = time.monotonic() + 2.0
        if deadline is not None:
            stop_at = min(stop_at, deadline)
        page = context.pages[-1] if context.pages else None
        while time.monotonic() < stop_at:
            with self._lock:
                if not self._pending:
                    return
            if self._closed.is_set() or page is None or page.is_closed():
                break
            try:
                page.wait_for_timeout(40)
            except PlaywrightError:
                break
        self._finish_all("request_incomplete")

    def _route(self, route: Route) -> None:
        request = route.request
        parsed = urlsplit(request.url)
        host = (parsed.hostname or "").lower()
        resource_type = request.resource_type
        if not _is_baidu_host(host) or resource_type not in _BILLABLE_RESOURCE_TYPES:
            route.continue_()
            return

        with self._lock:
            self._next_id += 1
            request_id = self._next_id
            answer: queue.Queue[bool] = queue.Queue(maxsize=1)
            self._decisions[request_id] = answer
        self._emit(
            {
                "browser_request": "started",
                "request_id": request_id,
                "host": host,
                "method": request.method,
                "resource_type": resource_type,
            }
        )
        allowed = False
        try:
            while not self._closed.is_set():
                try:
                    allowed = answer.get_nowait()
                    break
                except queue.Empty:
                    pass
                if self._deadline is not None and time.monotonic() >= self._deadline:
                    break
                self._pump_event_loop()
            if not allowed:
                route.abort("blockedbyclient")
                return
            pending = _PendingRequest(request, request_id, time.monotonic_ns())
            with self._lock:
                self._pending[id(request)] = pending
            route.continue_()
        except PlaywrightError:
            self._finish(request, "request_failed")
            raise
        finally:
            with self._lock:
                self._decisions.pop(request_id, None)

    def _pump_event_loop(self) -> None:
        """等待许可时继续派发完成事件，不能让同步路由回调阻塞它们。"""
        context = self._context
        page = context.pages[-1] if context is not None and context.pages else None
        if page is None or page.is_closed():
            time.sleep(0.01)
            return
        try:
            page.wait_for_timeout(25)
        except PlaywrightError:
            time.sleep(0.01)

    def _request_finished(self, request: Request) -> None:
        try:
            response = request.response()
            status = response.status if response is not None else None
        except Exception:
            status = None
        self._finish(
            request,
            f"http_{status}" if isinstance(status, int) else "request_finished",
        )

    def _request_failed(self, request: Request) -> None:
        self._finish(request, "request_failed")

    def _finish(self, request: Request, outcome_code: str) -> None:
        with self._lock:
            pending = self._pending.pop(id(request), None)
        if pending is None:
            return
        self._emit_finished(pending, outcome_code)

    def _finish_all(self, outcome_code: str) -> None:
        with self._lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for item in pending:
            self._emit_finished(item, outcome_code)

    def _emit_finished(self, pending: _PendingRequest, outcome_code: str) -> None:
        duration_ms = max(0, int((time.monotonic_ns() - pending.started_ns) / 1_000_000))
        self._emit(
            {
                "browser_request": "finished",
                "request_id": pending.request_id,
                "outcome_code": outcome_code,
                "duration_ms": duration_ms,
            }
        )


def _is_baidu_host(host: str) -> bool:
    return host == "baidu.com" or host.endswith(".baidu.com")
