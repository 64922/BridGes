"""贴吧公开调用共享预算适配器。"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlsplit

import httpx

from bridges.contracts.modules import ModuleQueryStatus
from bridges.external_call_decision import ExternalCallDecision
from bridges.public_search_budget import (
    bind_outcome_reporter,
    bind_request_tracker,
)
from bridges.tieba.contracts import (
    ReadStatus,
    TiebaOfficialCheck,
    TiebaReadResult,
)
from bridges.tieba.execution_metrics import TiebaExecutionMetrics
from bridges.tieba.official import (
    TiebaOfficialReader,
)
from bridges.tieba.reading import TiebaThreadReader
from bridges.tieba.run_metadata import TiebaBrowserCleanup, TiebaRequestBreakdown
from bridges.tieba.searching import (
    HitDiagnostics,
    SearchOutcome,
    TiebaSearchPort,
    query_record,
    thread_id_of,
)

if TYPE_CHECKING:
    from bridges.chat.run_budget_ledger import RunBudgetLedgerRepository


_REQUEST_BUDGET: ContextVar[Any] = ContextVar("tieba_request_budget", default=None)
_REQUEST_GUARD: ContextVar[Callable[[], str | None] | None] = ContextVar(
    "tieba_request_guard", default=None
)


def current_request_budget() -> TiebaBudget | None:
    """返回本次浏览器端口调用绑定的账本；不把运行状态存到共享适配器。"""
    return cast(TiebaBudget | None, _REQUEST_BUDGET.get())


def current_request_guard() -> Callable[[], str | None] | None:
    """返回当前运行的逐请求守卫，即使本轮没有可用预算账本。"""
    return _REQUEST_GUARD.get()


@contextmanager
def bind_request_budget(budget: TiebaBudget) -> Iterator[None]:
    token: Token[TiebaBudget | None] = _REQUEST_BUDGET.set(budget)
    try:
        with bind_request_tracker(budget.track_external_request):
            yield
    finally:
        _REQUEST_BUDGET.reset(token)


@contextmanager
def bind_request_guard(guard: Callable[[], str | None]) -> Iterator[None]:
    token = _REQUEST_GUARD.set(guard)
    try:
        with bind_request_tracker(lambda _purpose: _guard_request(guard)):
            yield
    finally:
        _REQUEST_GUARD.reset(token)


@contextmanager
def _guard_request(guard: Callable[[], str | None]) -> Iterator[str | None]:
    yield guard()


# ---------------------------------------------------------------------------
# 共享预算视图（工单 09 账本；缺失账本时按无预算模式运行）
# ---------------------------------------------------------------------------
class TiebaBudget:
    """贴吧外部调用的共享运行预算视图（检索、读取、官方抓取同一条账本）。"""

    def __init__(
        self,
        *,
        ledger: RunBudgetLedgerRepository,
        account_id: str,
        run_id: str,
        work_deadline: datetime,
        model_budget: Any = None,
        request_guard: Callable[[], str | None] | None = None,
    ) -> None:
        self._ledger = ledger
        self._account_id = account_id
        self._run_id = run_id
        self._work_deadline = work_deadline
        self.model_budget = model_budget
        self._request_guard = request_guard
        self._keys = _CallKeys()
        self.metrics = TiebaExecutionMetrics()

    def remaining_work_ms(self, now: datetime | None = None) -> int:
        moment = now or datetime.now(UTC)
        return max(0, int((self._work_deadline - moment).total_seconds() * 1000))

    def request_breakdown(self) -> list[TiebaRequestBreakdown]:
        return self.metrics.request_breakdown(
            self._ledger, self._account_id, self._run_id
        )

    def record_browser_cleanup(self, duration_ms: int) -> None:
        self.metrics.record_cleanup(duration_ms)

    def browser_cleanup_summary(self) -> TiebaBrowserCleanup | None:
        return self.metrics.browser_cleanup_summary()

    def request_block_reason(self) -> str | None:
        """执行逐请求守卫；不允许已停止或任务已变化的运行继续联网。"""
        return self._request_guard() if self._request_guard is not None else None

    @contextmanager
    def track_external_request(self, purpose: str) -> Iterator[str | None]:
        """为一次物理 HTTP 调用执行守卫、登记预算并记录结果与耗时。"""
        denial = self.request_block_reason()
        if denial is not None:
            yield denial
            return
        call_key = self.next_call_key(f"tieba.{purpose}")
        if not self.register_external(call_key, purpose=purpose):
            yield "run_budget_exhausted"
            return
        started = time.monotonic()
        outcome_code = "success"

        def report_outcome(code: str) -> None:
            nonlocal outcome_code
            outcome_code = code

        try:
            with bind_outcome_reporter(report_outcome):
                yield None
        except httpx.TimeoutException:
            outcome_code = "timeout"
            raise
        except BaseException as exc:
            if outcome_code == "success":
                code = getattr(exc, "code", None)
                outcome_code = code if isinstance(code, str) else "failed"
            elif outcome_code.startswith("http_2"):
                # A 2xx status alone does not prove that the response was consumed.
                outcome_code = f"{outcome_code}_request_failed"
            raise
        finally:
            self.release_external(
                call_key,
                outcome_code=outcome_code,
                duration_ms=int((time.monotonic() - started) * 1000),
            )

    def register_external(self, call_key: str, *, purpose: str) -> bool:
        return self.register_external_decision(call_key, purpose=purpose).allowed

    def register_external_decision(
        self,
        call_key: str,
        *,
        purpose: str,
        now: datetime | None = None,
    ) -> ExternalCallDecision:
        """返回带原因的许可；暂时容量不足与终止原因分开。"""
        return self._ledger.register_external_call_decision(
            account_id=self._account_id,
            run_id=self._run_id,
            call_key=call_key,
            purpose=purpose,
            now=now or datetime.now(UTC),
        )

    def record_external_call_denial(
        self,
        call_key: str | None,
        *,
        purpose: str | None,
        decision: ExternalCallDecision,
        denial_code: str | None = None,
        now: datetime | None = None,
    ) -> None:
        """记录一次终止性拒绝；容量等待不会走到这里。"""
        self._ledger.record_external_call_denial(
            account_id=self._account_id,
            run_id=self._run_id,
            call_key=call_key,
            purpose=purpose,
            reason=decision.reason,
            active=decision.active,
            limit=decision.limit,
            remaining_ms=decision.remaining_ms,
            now=now or datetime.now(UTC),
            denial_code=denial_code,
        )

    def external_call_stats(self) -> tuple[int, int, int]:
        """读取当前占位、上限与剩余工作时间；供守卫失败拒绝记录使用。"""
        snapshot = self._ledger.load(self._account_id, self._run_id)
        if snapshot is None:
            return (0, 0, 0)
        work_deadline = snapshot.plan.deadline_at - timedelta(
            milliseconds=snapshot.plan.verify_deliver_reserve_ms
        )
        remaining_ms = max(0, int((work_deadline - datetime.now(UTC)).total_seconds() * 1000))
        return (
            snapshot.external_calls_active,
            snapshot.plan.external_parallel_max,
            remaining_ms,
        )

    def record_manual_wait(self, wait_ms: int) -> None:
        """人工登录/验证等待不计入五分钟工作预算，也不重置已用计数。"""
        wait_ms = max(0, int(wait_ms))
        if wait_ms <= 0:
            return
        self._work_deadline += timedelta(milliseconds=wait_ms)
        if self.model_budget is not None:
            self.model_budget.exclude_wait(wait_ms)
        self._ledger.record_manual_auth_wait(
            account_id=self._account_id,
            run_id=self._run_id,
            wait_ms=wait_ms,
            now=datetime.now(UTC),
        )

    def next_call_key(self, prefix: str) -> str:
        return self._keys.next(prefix)

    def release_external(
        self,
        call_key: str,
        *,
        outcome_code: str,
        excluded_wait_ms: int = 0,
        duration_ms: int | None = None,
        queue_ms: int | None = None,
    ) -> None:
        values: dict[str, Any] = {
            "account_id": self._account_id,
            "run_id": self._run_id,
            "call_key": call_key,
            "outcome_code": outcome_code,
            "duration_ms": duration_ms,
            "excluded_wait_ms": excluded_wait_ms,
            "now": datetime.now(UTC),
        }
        if queue_ms is not None:
            values["queue_ms"] = max(0, int(queue_ms))
        self._ledger.record_external_call_result(**values)
        if excluded_wait_ms > 0:
            self._work_deadline += timedelta(milliseconds=excluded_wait_ms)
            if self.model_budget is not None:
                self.model_budget.exclude_wait(excluded_wait_ms)


class _CallKeys:
    """外部调用的唯一键计数器（并发占位的领取与释放按同一键配对）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counter = 0

    def next(self, prefix: str) -> str:
        with self._lock:
            self._counter += 1
            return f"{prefix}:{self._counter}"


class BudgetedSearchPort:
    """共享预算门控的搜索端口：预算耗尽时返回失败记录，不发起调用。"""

    def __init__(self, port: TiebaSearchPort, budget: TiebaBudget, *, purpose: str) -> None:
        self._port = port
        self._budget = budget
        self._purpose = purpose
        self._keys = _CallKeys()

    def search_public(
        self,
        account_id: str,
        *,
        query: str,
        reason: str,
        stop_event: object | None = None,
        deadline: float | None = None,
    ) -> SearchOutcome:
        if getattr(self._port, "uses_per_request_budget", False):
            with bind_request_budget(self._budget):
                return self._port.search_public(
                    account_id,
                    query=query,
                    reason=reason,
                    stop_event=stop_event,
                    deadline=deadline,
                )
        key = self._keys.next(self._purpose)
        if not self._budget.register_external(key, purpose=self._purpose):
            return SearchOutcome(
                record=query_record(
                    query=query,
                    status=ModuleQueryStatus.ERROR,
                    evidence_count=0,
                    error_code="run_budget_exhausted",
                    error_message="本轮运行预算已用尽，未发起新的检索请求。",
                    retryable=False,
                ),
                hits=(),
                candidates=(),
                rejected=(),
                diagnostics=HitDiagnostics(),
            )
        started = time.monotonic()
        outcome = None
        try:
            outcome = self._port.search_public(
                account_id,
                query=query,
                reason=reason,
                stop_event=stop_event,
                deadline=deadline,
            )
            return outcome
        finally:
            self._budget.release_external(
                key,
                outcome_code=(
                    outcome.record.status.value if outcome is not None else "failed"
                ),
                excluded_wait_ms=(outcome.excluded_wait_ms if outcome is not None else 0),
                duration_ms=int((time.monotonic() - started) * 1000),
            )


class BudgetedReader:
    """共享预算门控的帖子读取：预算耗尽时如实返回未读取。"""

    def __init__(self, reader: TiebaThreadReader, budget: TiebaBudget) -> None:
        self._reader = reader
        self._budget = budget
        self._keys = _CallKeys()

    def read(
        self,
        url: str,
        *,
        pages_limit: int | None = None,
        stop_event: object | None = None,
        deadline: float | None = None,
    ) -> TiebaReadResult:
        if getattr(self._reader, "uses_per_request_budget", False):
            with bind_request_budget(self._budget):
                if pages_limit is None:
                    return self._reader.read(
                        url, stop_event=stop_event, deadline=deadline
                    )
                return self._reader.read(
                    url,
                    pages_limit=pages_limit,
                    stop_event=stop_event,
                    deadline=deadline,
                )
        key = self._keys.next("tieba.read")
        if not self._budget.register_external(key, purpose="tieba_read"):
            return TiebaReadResult(
                url=url,
                thread_id=thread_id_of(url),
                status=ReadStatus.ERROR,
                error_code="run_budget_exhausted",
                error_message="本轮运行预算已用尽，未读取该帖子页面。",
                retrieved_at=datetime.now(UTC),
            )
        started = time.monotonic()
        result = None
        try:
            if pages_limit is None:
                result = self._reader.read(url, stop_event=stop_event, deadline=deadline)
            else:
                result = self._reader.read(
                    url,
                    pages_limit=pages_limit,
                    stop_event=stop_event,
                    deadline=deadline,
                )
            return result
        finally:
            self._budget.release_external(
                key,
                outcome_code=result.status.value if result is not None else "failed",
                excluded_wait_ms=(result.excluded_wait_ms if result is not None else 0),
                duration_ms=int((time.monotonic() - started) * 1000),
            )


class BudgetedOfficialReader:
    """共享预算门控的官方页面读取：预算耗尽时如实返回未取得。"""

    def __init__(self, reader: TiebaOfficialReader, budget: TiebaBudget) -> None:
        self._reader = reader
        self._budget = budget
        self._keys = _CallKeys()

    def fetch(
        self,
        url: str,
        *,
        terms: tuple[str, ...],
        stop_event: object | None = None,
        deadline: float | None = None,
    ) -> TiebaOfficialCheck:
        if getattr(self._reader, "uses_per_request_budget", False):
            with bind_request_budget(self._budget):
                return self._reader.fetch(
                    url, terms=terms, stop_event=stop_event, deadline=deadline
                )
        key = self._keys.next("tieba.official")
        if not self._budget.register_external(key, purpose="tieba_official"):
            return TiebaOfficialCheck(
                title=url,
                url=url,
                host=urlsplit(url).hostname or "",
                fetched_at=datetime.now(UTC),
                status="fetch_failed",
                error_code="run_budget_exhausted",
                error_message="本轮运行预算已用尽，未读取官方页面。",
            )
        started = time.monotonic()
        result: TiebaOfficialCheck | None = None
        try:
            result = self._reader.fetch(
                url, terms=terms, stop_event=stop_event, deadline=deadline
            )
            return result
        finally:
            self._budget.release_external(
                key,
                outcome_code=result.status if result is not None else "failed",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
