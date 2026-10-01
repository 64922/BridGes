"""预算内分批执行接缝：只执行代码登记且完整覆盖原请求的有限计划。"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, TimeoutError
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from bridges.chat.budget import RunBudget
from bridges.chat.run_budget_ledger import RunBudgetLedgerRepository


@dataclass(frozen=True, slots=True)
class BatchSourceRef:
    """来源版本与读取范围；只保存引用，不保存材料正文。"""

    source_id: str
    version: str
    read_range: str


@dataclass(frozen=True, slots=True)
class BudgetBatch:
    batch_id: str
    max_duration_ms: int
    model_call_limit: int
    token_limit: int
    sources: tuple[BatchSourceRef, ...]


@dataclass(frozen=True, slots=True)
class BoundedBatchPlan:
    """原任务指纹和必需来源必须由共同上下文边界核验，模型不能改写。"""

    plan_id: str
    version: str
    task_fingerprint: str
    required_sources: tuple[BatchSourceRef, ...]
    batches: tuple[BudgetBatch, ...]

    def complete(self) -> bool:
        ids = [batch.batch_id for batch in self.batches]
        covered = {source for batch in self.batches for source in batch.sources}
        return bool(
            self.plan_id and self.version and self.task_fingerprint and self.batches
            and len(set(ids)) == len(ids) and all(ids)
            and self.required_sources and covered == set(self.required_sources)
            and all(s.source_id and s.version and s.read_range for s in covered)
            and all(
                batch.max_duration_ms > 0 and batch.model_call_limit >= 0 and batch.token_limit >= 0
                for batch in self.batches
            )
        )


@dataclass(frozen=True, slots=True)
class BatchReceipt:
    batch_id: str
    result_refs: tuple[str, ...]
    sources: tuple[BatchSourceRef, ...]


@dataclass(frozen=True, slots=True)
class BatchRunResult:
    receipts: tuple[BatchReceipt, ...]
    blocked_reason: str | None = None


class BoundedBatchExecutor:
    """顺序执行冻结批次，完成引用可恢复；不自行拆材料或缩小任务。"""

    def __init__(self, ledger: RunBudgetLedgerRepository) -> None:
        self._ledger = ledger

    def execute(
        self, account_id: str, run_id: str, plan: BoundedBatchPlan, budget: RunBudget,
        callback: Callable[[BudgetBatch, RunBudget, float, threading.Event], BatchReceipt],
        *, task_fingerprint: str, required_sources: tuple[BatchSourceRef, ...],
        stop: threading.Event,
    ) -> BatchRunResult:
        snapshot = self._ledger.load(account_id, run_id)
        if (
            not plan.complete() or plan.task_fingerprint != task_fingerprint
            or set(plan.required_sources) != set(required_sources)
        ):
            return BatchRunResult((), "batch_plan_incomplete")
        if snapshot is None or not budget.has_ledger:
            return BatchRunResult((), "run_budget_missing")
        if (
            sum(b.max_duration_ms for b in plan.batches)
            > snapshot.plan.total_budget_ms - snapshot.plan.verify_deliver_reserve_ms
            or sum(b.model_call_limit for b in plan.batches) > snapshot.plan.model_call_limit
            or snapshot.plan.token_budget is not None
            and sum(b.token_limit for b in plan.batches) > snapshot.plan.token_budget
        ):
            return BatchRunResult((), "batch_plan_over_budget")
        # JSON 往返使 tuple/list 的持久化比较口径一致。
        import json

        detail: dict[str, Any] = json.loads(json.dumps(asdict(plan)))
        if not self._ledger.freeze_batch_plan(
            account_id, run_id, plan.plan_id, detail, now=datetime.now(UTC)
        ):
            return BatchRunResult((), "batch_plan_changed")
        entries = self._ledger.list_entries(account_id, run_id)
        receipts: list[BatchReceipt] = []
        for batch in plan.batches:
            key = f"{plan.plan_id}:{batch.batch_id}"
            completed = next(
                (e for e in entries if e.kind == "batch_completed" and e.call_key == key), None
            )
            if completed is not None:
                receipts.append(BatchReceipt(
                    batch.batch_id, tuple(completed.detail["result_refs"]), batch.sources
                ))
                continue
            if stop.is_set() or not budget.can_retry_model_call():
                return BatchRunResult(tuple(receipts), "run_budget_stopped_or_expired")
            if not self._ledger.begin_batch(account_id, run_id, key, now=datetime.now(UTC)):
                return BatchRunResult(tuple(receipts), "batch_already_running")
            started = next(
                e for e in self._ledger.list_entries(account_id, run_id)
                if e.kind == "batch_started" and e.call_key == key
            )
            deadline = min(
                budget.work_deadline(), time.monotonic() + (
                    datetime.fromisoformat(started.detail["deadline_at"]) - datetime.now(UTC)
                ).total_seconds(),
            )
            future: Future[BatchReceipt] = Future()
            cancel = threading.Event()

            def invoke(
                current: BudgetBatch = batch, result: Future[BatchReceipt] = future,
                until: float = deadline, cancellation: threading.Event = cancel,
            ) -> None:
                try:
                    result.set_result(callback(current, budget, until, cancellation))
                except BaseException as exc:
                    result.set_exception(exc)

            threading.Thread(
                target=invoke, daemon=True, name=f"budget-batch-{batch.batch_id}"
            ).start()
            try:
                while True:
                    remaining = deadline - time.monotonic()
                    if stop.is_set() or remaining <= 0 or not budget.can_retry_model_call():
                        cancel.set()
                        budget.mark_exhausted(
                            reason_code="batch_stopped" if stop.is_set() else "batch_timeout"
                        )
                        return BatchRunResult(tuple(receipts), "batch_stopped_or_expired")
                    try:
                        receipt = future.result(timeout=min(0.05, remaining))
                        break
                    except TimeoutError:
                        continue
            except Exception:
                self._ledger.finish_batch(
                    account_id, run_id, key, failed=True, now=datetime.now(UTC)
                )
                return BatchRunResult(tuple(receipts), "batch_failed")
            after = self._ledger.load(account_id, run_id)
            assert after is not None
            if (
                receipt.batch_id != batch.batch_id or not receipt.result_refs
                or set(receipt.sources) != set(batch.sources)
                or after.model_calls_used - started.detail["calls_before"] > batch.model_call_limit
                or after.input_tokens_used + after.output_tokens_used
                - started.detail["tokens_before"] > batch.token_limit
                or time.monotonic() > deadline or stop.is_set()
            ):
                cancel.set()
                if stop.is_set() or time.monotonic() > deadline:
                    budget.mark_exhausted(reason_code="batch_stopped_or_expired")
                else:
                    self._ledger.finish_batch(
                        account_id, run_id, key, failed=True, now=datetime.now(UTC)
                    )
                return BatchRunResult(tuple(receipts), "batch_receipt_invalid_or_over_budget")
            if not self._ledger.finish_batch(
                account_id, run_id, key, now=datetime.now(UTC),
                detail={
                    "result_refs": list(receipt.result_refs), "sources": asdict(batch)["sources"]
                },
            ):
                return BatchRunResult(tuple(receipts), "batch_commit_rejected")
            receipts.append(receipt)
        return BatchRunResult(tuple(receipts))
