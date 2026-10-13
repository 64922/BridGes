from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from bridges.chat.run_budget_ledger_storage import _RunBudgetLedgerStorage
from bridges.chat.run_budget_ledger_types import (
    RunBudgetSnapshot,
    _iso,
    _parse,
)


class _RunBudgetLedgerBatches(_RunBudgetLedgerStorage):
    """内部账本操作实现。"""
    def freeze_batch_plan(
        self, account_id: str, run_id: str, plan_id: str, detail: dict[str, Any],
        *, now: datetime,
    ) -> bool:
        """冻结经代码核验的分批计划；重放必须与原计划完全一致。"""
        with self.transaction():
            plans = [e for e in self.list_entries(account_id, run_id) if e.kind == "batch_plan"]
            if plans:
                return plans[0].call_key == plan_id and plans[0].detail == detail
            return self._mutate(
                account_id, run_id, now, kind="batch_plan", call_key=plan_id,
                counter_updates={}, gate=lambda _snapshot: True, extra_detail_values=detail,
            )

    def begin_batch(self, account_id: str, run_id: str, batch_key: str, *, now: datetime) -> bool:
        """当前进程尚有此批执行时拒绝重复启动，重启仍使用同一运行额度。"""
        def consume(_snapshot: RunBudgetSnapshot) -> bool:
            entries = [e for e in self.list_entries(account_id, run_id) if e.call_key == batch_key]
            if not entries:
                return True
            first = next(e for e in entries if e.kind == "batch_started")
            deadline = _parse(first.detail["deadline_at"])
            if deadline is None or now >= deadline:
                return False
            last = entries[-1]
            return last.kind == "batch_failed" or (
                last.kind == "batch_started"
                and last.detail.get("execution_process") != self._execution_process_token()
            )

        def limits(snapshot: RunBudgetSnapshot) -> dict[str, Any]:
            previous = next((
                e for e in self.list_entries(account_id, run_id)
                if e.call_key == batch_key and e.kind == "batch_started"
            ), None)
            if previous is not None:
                return {**previous.detail, "execution_process": self._execution_process_token()}
            plan = next(e for e in self.list_entries(account_id, run_id) if e.kind == "batch_plan")
            batch = next(
                b for b in plan.detail["batches"]
                if f"{plan.call_key}:{b['batch_id']}" == batch_key
            )
            return {
                "execution_process": self._execution_process_token(),
                "deadline_at": _iso(now + timedelta(milliseconds=batch["max_duration_ms"])),
                "model_call_limit": batch["model_call_limit"], "token_limit": batch["token_limit"],
                "calls_before": snapshot.model_calls_used,
                "tokens_before": snapshot.input_tokens_used + snapshot.output_tokens_used,
            }

        return self._mutate(
            account_id, run_id, now, kind="batch_started", call_key=batch_key,
            counter_updates={}, gate=consume,
            extra_detail=limits,
        )

    def finish_batch(
        self, account_id: str, run_id: str, batch_key: str, *, now: datetime,
        detail: dict[str, Any] | None = None, failed: bool = False,
    ) -> bool:
        """提交本批脱敏完成引用或失败码；超时与停止后的迟到结果不能推进。"""
        def consume(_snapshot: RunBudgetSnapshot) -> bool:
            entries = [e for e in self.list_entries(account_id, run_id) if e.call_key == batch_key]
            if not entries or entries[-1].kind != "batch_started":
                return False
            started = entries[-1]
            deadline = _parse(started.detail["deadline_at"])
            return (
                started.detail.get("execution_process") == self._execution_process_token()
                and deadline is not None and now < deadline
            )

        return self._mutate(
            account_id, run_id, now,
            kind="batch_failed" if failed else "batch_completed", call_key=batch_key,
            counter_updates={}, gate=consume, extra_detail_values=detail,
        )
