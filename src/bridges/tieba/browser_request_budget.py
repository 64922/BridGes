"""浏览器请求事件与运行预算账本之间的适配：有界、可取消的请求调度。

并发上限是「同时在途」的容量，不是预算耗尽。容量不足的请求进入有界队列，
等已有请求完成并由同一账本释放占位后再继续；只有工作时间耗尽、运行失效、
取消或守卫失败才终止整个浏览器操作。队列长度与等待时间都有上界，worker
退出、请求失败、取消与超时都会一次性释放全部占位。
"""

from __future__ import annotations

import json
import logging
import queue as queue_module
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from bridges.external_call_decision import (
    ExternalCallDecision,
    ExternalCallReason,
)

if TYPE_CHECKING:
    from bridges.tieba.budget import TiebaBudget

logger = logging.getLogger(__name__)


#: 有界等待队列：正常页面不会同时触发这么多采集请求。
MAX_WAITING_REQUESTS = 16

#: 版本竞争只重试很短时间，不改变队列语义。
_CONTENTION_RETRY_SECONDS = 0.05

_DENIAL_MESSAGES = {
    "tieba_browser_cancelled": "已停止贴吧浏览器读取。",
    "tieba_browser_task_changed": "任务条件已变化，已阻止新的贴吧请求。",
    "tieba_browser_run_invalidated": "本轮运行已失效，已阻止新的贴吧请求。",
    "tieba_browser_request_guard_failed": "请求授权检查失败，未放行新的贴吧请求。",
    "tieba_browser_work_time_exhausted": "本轮剩余贴吧工作预算已耗尽，未发起后续页面请求。",
    "tieba_browser_request_queue_full": "同时等待的贴吧页面请求过多，已停止继续加载。",
    "tieba_browser_timeout": "贴吧浏览器读取超时，请稍后重试。",
}


def request_denial_message(code: str) -> str:
    return _DENIAL_MESSAGES.get(code, "已阻止新的贴吧请求，请稍后重试。")


@dataclass
class _WaitingRequest:
    request_id: int
    call_key: str
    purpose: str
    last_attempt_at: float
    last_decision: ExternalCallDecision


class BrowserRequestBudget:
    """为子进程发来的逐请求事件登记、排队、放行并收敛账本占位。"""

    def __init__(
        self,
        budget: TiebaBudget | None,
        send: Callable[[dict[str, Any]], None],
        *,
        stop_event: object | None = None,
        request_guard: Callable[[], str | None] | None = None,
        deadline: float | None = None,
    ) -> None:
        self._budget = budget
        self._send = send
        self._stop_event = stop_event
        self._request_guard = request_guard
        self._deadline = deadline
        self._denial_code: str | None = None
        self._terminal = False
        self._pending: dict[int, tuple[str, int]] = {}
        self._waiting: deque[_WaitingRequest] = deque()
        self._clock = time.monotonic

    @property
    def has_pending(self) -> bool:
        return bool(self._pending) or bool(self._waiting)

    @property
    def active_count(self) -> int:
        return len(self._pending)

    @property
    def waiting_count(self) -> int:
        return len(self._waiting)

    @property
    def denial_code(self) -> str | None:
        return self._denial_code

    def handle(self, value: dict[str, Any]) -> bool | None:
        event = value.get("browser_request")
        if event == "started":
            return self._started(value)
        if event == "finished":
            self._finished(value)
            return True
        return None

    def tick(self) -> str | None:
        """由接收循环周期调用：处理停止、守卫与等待上界。"""
        if self._terminal:
            return self._denial_code
        block_reason = self._request_block_reason()
        if block_reason is not None:
            self._block(block_reason)
            self._deny_all_pending(block_reason)
            self._deny_all_waiting(block_reason)
            return self._denial_code
        if self._waiting:
            now = self._clock()
            if self._deadline is not None and now >= self._deadline:
                self._deny_all_waiting("tieba_browser_timeout")
                return self._denial_code
            if self._budget_work_time_is_exhausted():
                self._deny_all_waiting("tieba_browser_work_time_exhausted")
                return self._denial_code
            self._grant_waiting()
        return self._denial_code if self._terminal else None

    def deny_queued(self, output: Any) -> None:
        """终止动作前拒绝已到达但尚未消费的请求，避免 worker 等决策。"""
        while True:
            try:
                line = output.get_nowait()
            except queue_module.Empty:
                return
            if line is None:
                return
            try:
                value = json.loads(line)
            except (TypeError, ValueError):
                continue
            if not isinstance(value, dict):
                continue
            if value.get("browser_request") == "started":
                self._send(
                    {
                        "type": "request_decision",
                        "request_id": int(value.get("request_id", -1)),
                        "allowed": False,
                    }
                )

    def _started(self, value: dict[str, Any]) -> bool | None:
        request_id = int(value.get("request_id", -1))
        host = str(value.get("host", ""))
        method = str(value.get("method", "GET")).upper()
        resource_type = str(value.get("resource_type", ""))
        purpose = (
            f"tieba_browser_{host.replace('.', '_')}_"
            f"{resource_type}_{method.lower()}"
        )
        block_reason = self._request_block_reason()
        if block_reason is not None:
            self._deny_now(request_id, call_key=None, purpose=purpose, code=block_reason)
            return False
        if len(self._waiting) >= MAX_WAITING_REQUESTS:
            self._deny_now(
                request_id,
                call_key=None,
                purpose=purpose,
                code="tieba_browser_request_queue_full",
            )
            return False
        call_key = (
            self._budget.next_call_key("tieba.browser.request")
            if self._budget is not None
            else f"browser-request-{request_id}"
        )
        decision = self._decide(call_key, purpose)
        if decision.allowed:
            self._activate(request_id, call_key)
            return True
        if self._is_waitable(decision):
            self._waiting.append(
                _WaitingRequest(
                    request_id=request_id,
                    call_key=call_key,
                    purpose=purpose,
                    last_attempt_at=self._clock(),
                    last_decision=decision,
                )
            )
            return None
        self._deny_now(
            request_id,
            call_key=call_key,
            purpose=purpose,
            code=self._code_for_reason(decision),
            decision=decision,
        )
        return False

    def _finished(self, value: dict[str, Any]) -> None:
        request_id = int(value.get("request_id", -1))
        call = self._pending.pop(request_id, None)
        if call is not None and self._budget is not None:
            call_key, started_ns = call
            self._budget.release_external(
                call_key,
                outcome_code=str(value.get("outcome_code") or "request_finished"),
                duration_ms=max(0, int(value.get("duration_ms") or 0)),
            )
        self._grant_waiting()

    def _grant_waiting(self) -> None:
        now = self._clock()
        while self._waiting and not self._terminal:
            waiter = self._waiting[0]
            if (
                waiter.last_decision.reason == ExternalCallReason.CONTENDED.value
                and now - waiter.last_attempt_at < _CONTENTION_RETRY_SECONDS
            ):
                return
            decision = self._decide(waiter.call_key, waiter.purpose)
            waiter.last_decision = decision
            waiter.last_attempt_at = now
            if decision.allowed:
                self._waiting.popleft()
                self._activate(waiter.request_id, waiter.call_key)
                continue
            if self._is_waitable(decision):
                return
            self._waiting.popleft()
            self._deny_now(
                waiter.request_id,
                call_key=waiter.call_key,
                purpose=waiter.purpose,
                code=self._code_for_reason(decision),
                decision=decision,
            )

    def _activate(self, request_id: int, call_key: str) -> None:
        self._pending[request_id] = (call_key, time.monotonic_ns())
        self._send(
            {
                "type": "request_decision",
                "request_id": request_id,
                "allowed": True,
            }
        )

    def _block(self, code: str) -> None:
        """没有在途请求时也要记录守卫/停止原因并终止本次动作。"""
        if self._budget is not None and self._denial_code != code:
            decision = self._stats_decision(code)
            self._budget.record_external_call_denial(
                None,
                purpose=None,
                decision=decision,
                denial_code=code,
            )
        self._denial_code = code
        self._terminal = True

    def _deny_now(
        self,
        request_id: int,
        *,
        call_key: str | None,
        purpose: str,
        code: str,
        decision: ExternalCallDecision | None = None,
    ) -> None:
        if self._budget is not None:
            if decision is None:
                decision = self._stats_decision(code)
            record = ExternalCallDecision(
                allowed=False,
                reason=self._ledger_reason(code),
                active=decision.active,
                limit=decision.limit,
                remaining_ms=decision.remaining_ms,
            )
            self._budget.record_external_call_denial(
                call_key,
                purpose=purpose,
                decision=record,
                denial_code=code,
            )
        self._denial_code = code
        self._terminal = True
        self._send(
            {
                "type": "request_decision",
                "request_id": request_id,
                "allowed": False,
            }
        )

    def _deny_all_pending(self, code: str) -> None:
        """在途请求也必须收到拒绝，否则 worker 会一直等待路由决策。"""
        for request_id in list(self._pending):
            self._send(
                {
                    "type": "request_decision",
                    "request_id": request_id,
                    "allowed": False,
                }
            )
        self._denial_code = code
        self._terminal = True

    def _deny_all_waiting(self, code: str) -> None:
        while self._waiting:
            waiter = self._waiting.popleft()
            self._deny_now(
                waiter.request_id,
                call_key=waiter.call_key,
                purpose=waiter.purpose,
                code=code,
            )

    def _decide(self, call_key: str, purpose: str) -> ExternalCallDecision:
        if self._budget is None:
            return ExternalCallDecision(
                allowed=True,
                reason=ExternalCallReason.ALLOWED.value,
            )
        return self._budget.register_external_decision(call_key, purpose=purpose)

    def _stats_decision(self, code: str) -> ExternalCallDecision:
        active, limit, remaining_ms = (0, 0, 0)
        if self._budget is not None:
            stats = getattr(self._budget, "external_call_stats", None)
            if callable(stats):
                active, limit, remaining_ms = stats()
        return ExternalCallDecision(
            allowed=False,
            reason=self._ledger_reason(code),
            active=active,
            limit=limit,
            remaining_ms=remaining_ms,
        )

    def _ledger_reason(self, code: str) -> str:
        return {
            "tieba_browser_cancelled": ExternalCallReason.CANCELLED.value,
            "tieba_browser_work_time_exhausted": (
                ExternalCallReason.WORK_TIME_EXHAUSTED.value
            ),
            "tieba_browser_request_queue_full": ExternalCallReason.QUEUE_FULL.value,
            "tieba_browser_request_guard_failed": (
                ExternalCallReason.GUARD_FAILED.value
            ),
            "tieba_browser_timeout": ExternalCallReason.TIMEOUT.value,
        }.get(code, ExternalCallReason.RUN_INVALIDATED.value)

    def _is_waitable(self, decision: ExternalCallDecision) -> bool:
        return decision.reason in {
            ExternalCallReason.CAPACITY.value,
            ExternalCallReason.CONTENDED.value,
        }

    def _code_for_reason(self, decision: ExternalCallDecision) -> str:
        return {
            ExternalCallReason.CANCELLED.value: "tieba_browser_cancelled",
            ExternalCallReason.WORK_TIME_EXHAUSTED.value: "tieba_browser_work_time_exhausted",
            ExternalCallReason.RUN_INVALIDATED.value: "tieba_browser_run_invalidated",
            ExternalCallReason.TIMEOUT.value: "tieba_browser_timeout",
        }.get(decision.reason, "tieba_browser_run_invalidated")

    def _budget_work_time_is_exhausted(self) -> bool:
        if self._budget is None:
            return False
        return self._budget.remaining_work_ms() <= 0

    def _request_block_reason(self) -> str | None:
        checker = getattr(self._stop_event, "is_set", None)
        if callable(checker) and checker():
            return "tieba_browser_cancelled"
        if self._request_guard is None:
            return None
        try:
            return self._request_guard()
        except Exception:
            logger.warning("贴吧浏览器请求守卫检查失败", exc_info=True)
            return "tieba_browser_request_guard_failed"

    def finish_pending(self, outcome_code: str) -> None:
        """请求动作结束时释放全部在途占位；等待项不再等待。"""
        pending = list(self._pending.items())
        self._pending.clear()
        for _request_id, (call_key, started_ns) in pending:
            if self._budget is not None:
                duration_ms = max(
                    0, int((time.monotonic_ns() - started_ns) / 1_000_000)
                )
                self._budget.release_external(
                    call_key,
                    outcome_code=outcome_code,
                    duration_ms=duration_ms,
                )
        while self._waiting:
            waiter = self._waiting.popleft()
            self._deny_now(
                waiter.request_id,
                call_key=waiter.call_key,
                purpose=waiter.purpose,
                code="tieba_browser_run_invalidated",
            )
