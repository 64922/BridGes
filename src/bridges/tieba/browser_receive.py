"""浏览器 worker 结果接收：请求调度与人工认证等待的配对计时。"""

from __future__ import annotations

import json
import queue
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from bridges.tieba.browser_auth import AUTH_INTERRUPTED, AUTH_WAIT_LIMIT_SECONDS
from bridges.tieba.browser_auth_wait import AuthWaitTracker
from bridges.tieba.browser_errors import TiebaBrowserError
from bridges.tieba.browser_request_budget import (
    BrowserRequestBudget,
    request_denial_message,
)

if TYPE_CHECKING:
    from bridges.tieba.budget import TiebaBudget


def receive_result(
    worker: Any,
    *,
    stop_event: object | None,
    deadline: float,
    request_budget: TiebaBudget | None,
    request_guard: Callable[[], str | None] | None,
    send: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    """接收一次 worker 动作；认证等待单独计时并只延长一次。"""
    auth_wait = AuthWaitTracker()
    request_ledger = BrowserRequestBudget(
        request_budget,
        send,
        stop_event=stop_event,
        request_guard=request_guard
        or (
            request_budget.request_block_reason
            if request_budget is not None
            else None
        ),
        deadline=deadline,
    )

    def finish_auth_wait(outcome: str) -> None:
        nonlocal deadline
        waited_ms = auth_wait.finish(time.monotonic())
        if waited_ms is None:
            return
        if request_budget is not None:
            request_budget.record_manual_wait(waited_ms)
        deadline += waited_ms / 1000.0

    try:
        while True:
            checker = getattr(stop_event, "is_set", None)
            if callable(checker) and checker():
                finish_auth_wait("cancelled")
                raise TiebaBrowserError("tieba_browser_cancelled", "已停止贴吧浏览器读取。")
            now = time.monotonic()
            if (
                auth_wait.active
                and auth_wait.started_at is not None
                and now - auth_wait.started_at >= AUTH_WAIT_LIMIT_SECONDS
            ):
                finish_auth_wait("timeout")
                raise TiebaBrowserError(
                    AUTH_INTERRUPTED,
                    "登录或验证等待超时；任务已保留，请完成后说“继续”。",
                    excluded_wait_ms=auth_wait.total_ms,
                )
            denied = request_ledger.tick()
            if denied is not None:
                request_ledger.deny_queued(worker.output)
                finish_auth_wait("interrupted")
                raise TiebaBrowserError(
                    denied,
                    request_denial_message(denied),
                )
            if not auth_wait.active and now >= deadline:
                raise TiebaBrowserError(
                    "tieba_browser_timeout", "贴吧浏览器读取超时，请稍后重试。"
                )
            try:
                line = worker.output.get(timeout=0.1)
            except queue.Empty:
                continue
            if line is None:
                finish_auth_wait("interrupted")
                raise TiebaBrowserError(
                    "tieba_browser_unavailable", "专用浏览器未返回可读取内容。"
                )
            try:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError
            except (ValueError, TypeError):
                finish_auth_wait("interrupted")
                raise TiebaBrowserError(
                    "tieba_browser_unavailable", "专用浏览器未返回可读取内容。"
                ) from None
            auth_event = value.get("auth_wait")
            if auth_event == "started":
                auth_wait.start(str(value.get("reason") or ""), now)
                continue
            if auth_event == "finished":
                finish_auth_wait(str(value.get("outcome") or "finished"))
                continue
            try:
                request_event = request_ledger.handle(value)
            except TiebaBrowserError:
                raise
            except Exception:
                finish_auth_wait("interrupted")
                raise TiebaBrowserError(
                    "tieba_browser_request_guard_failed",
                    request_denial_message("tieba_browser_request_guard_failed"),
                ) from None
            if request_event is not None:
                if not request_event:
                    error_code = (
                        request_ledger.denial_code
                        or "tieba_browser_run_invalidated"
                    )
                    finish_auth_wait("interrupted")
                    raise TiebaBrowserError(
                        error_code,
                        request_denial_message(error_code),
                    )
                continue
            if value.get("error"):
                finish_auth_wait("interrupted")
                raise TiebaBrowserError(
                    str(value["error"]),
                    str(value["message"]),
                    excluded_wait_ms=auth_wait.total_ms,
                )
            if request_ledger.has_pending:
                request_ledger.finish_pending("browser_request_incomplete")
            finish_auth_wait("result")
            if auth_wait.total_ms:
                value["excluded_wait_ms"] = auth_wait.total_ms
            return value
    finally:
        if request_ledger.has_pending:
            request_ledger.finish_pending("browser_action_interrupted")
        finish_auth_wait("interrupted")


__all__ = ["receive_result"]
