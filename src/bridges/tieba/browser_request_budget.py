"""浏览器请求事件与运行预算账本之间的适配。"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bridges.tieba.budget import TiebaBudget


_DENIAL_MESSAGES = {
    "tieba_browser_cancelled": "已停止贴吧浏览器读取。",
    "tieba_browser_task_changed": "任务条件已变化，已阻止新的贴吧请求。",
    "tieba_browser_run_invalidated": "本轮运行已失效，已阻止新的贴吧请求。",
    "tieba_browser_request_guard_failed": "请求授权检查失败，未放行新的贴吧请求。",
}


def request_denial_message(code: str) -> str:
    return _DENIAL_MESSAGES.get(code, "本轮运行预算已用尽，未发起新的贴吧请求。")


class BrowserRequestBudget:
    """为子进程发来的逐请求事件登记、放行并收敛账本占位。"""

    def __init__(
        self,
        budget: TiebaBudget | None,
        send: Callable[[dict[str, Any]], None],
        *,
        stop_event: object | None = None,
        request_guard: Callable[[], str | None] | None = None,
    ) -> None:
        self._budget = budget
        self._send = send
        self._stop_event = stop_event
        self._request_guard = request_guard
        self._denial_code: str | None = None
        self._pending: dict[int, tuple[str, int]] = {}

    @property
    def has_pending(self) -> bool:
        return bool(self._pending)

    @property
    def denial_code(self) -> str | None:
        return self._denial_code

    def handle(self, value: dict[str, Any]) -> bool | None:
        event = value.get("browser_request")
        if event == "started":
            request_id = int(value.get("request_id", -1))
            host = str(value.get("host", ""))
            method = str(value.get("method", "GET")).upper()
            resource_type = str(value.get("resource_type", ""))
            self._denial_code = self._request_block_reason()
            allowed = self._denial_code is None
            if allowed and self._budget is not None:
                purpose = (
                    f"tieba_browser_{host.replace('.', '_')}_"
                    f"{resource_type}_{method.lower()}"
                )
                call_key = self._budget.next_call_key("tieba.browser.request")
                allowed = self._budget.register_external(call_key, purpose=purpose)
                if allowed:
                    self._pending[request_id] = (call_key, time.monotonic_ns())
            if not allowed and self._denial_code is None:
                self._denial_code = "tieba_browser_run_budget_exhausted"
            self._send(
                {
                    "type": "request_decision",
                    "request_id": request_id,
                    "allowed": allowed,
                }
            )
            return allowed
        if event == "finished":
            request_id = int(value.get("request_id", -1))
            call = self._pending.pop(request_id, None)
            if call is not None and self._budget is not None:
                call_key, _started_ns = call
                self._budget.release_external(
                    call_key,
                    outcome_code=str(value.get("outcome_code") or "request_finished"),
                    duration_ms=max(0, int(value.get("duration_ms") or 0)),
                )
            return True
        return None

    def _request_block_reason(self) -> str | None:
        checker = getattr(self._stop_event, "is_set", None)
        if callable(checker) and checker():
            return "tieba_browser_cancelled"
        if self._request_guard is None:
            return None
        return self._request_guard()

    def finish_pending(self, outcome_code: str) -> None:
        if self._budget is not None:
            for call_key, started_ns in self._pending.values():
                duration_ms = max(
                    0, int((time.monotonic_ns() - started_ns) / 1_000_000)
                )
                self._budget.release_external(
                    call_key,
                    outcome_code=outcome_code,
                    duration_ms=duration_ms,
                )
        self._pending.clear()
