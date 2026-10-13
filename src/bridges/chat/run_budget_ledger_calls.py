from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from bridges.chat.run_budget_ledger_storage import _RunBudgetLedgerStorage
from bridges.chat.run_budget_ledger_types import (
    RunBudgetEntry,
    RunBudgetSnapshot,
    _iso,
)


class _RunBudgetLedgerCalls(_RunBudgetLedgerStorage):
    """内部账本操作实现。"""
    def register_model_call(
        self,
        *,
        account_id: str,
        run_id: str,
        call_key: str,
        purpose: str | None = None,
        now: datetime,
    ) -> bool:
        """登记一次模型调用；超出调用数/token 上限或账本不活跃时拒绝。

        每次登记都是一次真实调用决策（恢复重跑再次登记是真实消耗，
        不去重）；调用结果经 :meth:`record_model_call_result` 补记。
        """

        def consume(snapshot: RunBudgetSnapshot) -> bool:
            if snapshot.model_calls_used >= snapshot.plan.model_call_limit:
                return False
            # token 上限按已耗输入+输出合计事前守门（输出也是真实消耗）；
            # 上限通常晚于调用数上限触发，作为溢出兜底存在。
            tokens_used = snapshot.input_tokens_used + snapshot.output_tokens_used
            return not (
                snapshot.plan.token_budget is not None
                and tokens_used >= snapshot.plan.token_budget
            )

        def extra_fields(snapshot: RunBudgetSnapshot) -> dict[str, Any]:
            return {
                "model_calls_used": snapshot.model_calls_used + 1,
                "model_call_limit": snapshot.plan.model_call_limit,
            }

        return self._mutate(
            account_id,
            run_id,
            now,
            kind="model_call",
            purpose=purpose,
            call_key=call_key,
            counter_updates={"model_calls_used": 1},
            gate=consume,
            extra_detail=extra_fields,
        )

    def record_model_call_result(
        self,
        *,
        account_id: str,
        run_id: str,
        call_key: str,
        attempt: int = 1,
        outcome_code: str | None = None,
        duration_ms: int | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        now: datetime,
    ) -> None:
        """补记一次模型调用的脱敏结果（用途/结果码/毫秒/token 计量）。"""
        def consume(_snapshot: RunBudgetSnapshot) -> bool:
            return self._db.scoped(account_id).execute(
                "SELECT 1 FROM run_budget_entries WHERE account_id = ? AND run_id = ?"
                " AND kind = 'model_call_result' AND call_key = ? AND attempt = ?",
                (account_id, run_id, call_key, attempt),
            ).fetchone() is None

        registered = self._db.scoped(account_id).execute(
            "SELECT purpose FROM run_budget_entries WHERE account_id = ? AND run_id = ?"
            " AND kind = 'model_call' AND call_key = ? ORDER BY seq DESC LIMIT 1",
            (account_id, run_id, call_key),
        ).fetchone()
        self._mutate(
            account_id,
            run_id,
            now,
            kind="model_call_result",
            call_key=call_key,
            purpose=registered["purpose"] if registered is not None else None,
            attempt=attempt,
            outcome_code=outcome_code,
            duration_ms=duration_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            counter_updates={
                "input_tokens_used": max(0, input_tokens or 0),
                "output_tokens_used": max(0, output_tokens or 0),
            },
            gate=consume,
            allow_closed=True,
        )

    def register_transient_retry(
        self,
        *,
        account_id: str,
        run_id: str,
        call_key: str,
        error_code: str | None = None,
        backoff_ms: int | None = None,
        now: datetime,
    ) -> bool:
        """登记一次临时传输失败重试；每登记调用最多 ``transient_retry_max`` 次。

        幂等守卫：同一 (run_id, call_key, attempt) 的重复登记拒绝放行
        且不重复计数（部分唯一索引 ``idx_run_budget_entries_retry_once``）。
        错误类型/冷却/剩余额度检查由调用方（网关预算接缝）在登记前完成。
        """

        def consume(snapshot: RunBudgetSnapshot) -> bool:
            # 「每个登记的临时传输失败最多重试 1 次」按 (run_id, call_key)
            # 计数——不同调用各自享有重试额度；全局计数只做计量。重复登记
            # 由部分唯一索引拦截，不重复计数。
            row = self._db.scoped(account_id).execute(
                "SELECT COUNT(*) AS count FROM run_budget_entries"
                " WHERE run_id = ? AND account_id = ? AND kind = 'transient_retry'"
                " AND call_key = ?",
                (run_id, account_id, call_key),
            ).fetchone()
            tokens_used = snapshot.input_tokens_used + snapshot.output_tokens_used
            return (
                int(row["count"]) < snapshot.plan.transient_retry_max
                and snapshot.model_calls_used < snapshot.plan.model_call_limit
                and (snapshot.plan.token_budget is None or tokens_used < snapshot.plan.token_budget)
            )

        def extra_fields(snapshot: RunBudgetSnapshot) -> dict[str, Any]:
            return {
                "transient_retries_used": snapshot.transient_retries_used + 1,
                "transient_retry_max": snapshot.plan.transient_retry_max,
            }

        return self._mutate(
            account_id,
            run_id,
            now,
            kind="transient_retry",
            call_key=call_key,
            attempt=1,
            outcome_code=error_code,
            counter_updates={"transient_retries_used": 1, "model_calls_used": 1},
            gate=consume,
            extra_detail=extra_fields,
            extra_detail_values={"backoff_ms": backoff_ms} if backoff_ms else None,
        )

    def register_external_call(
        self,
        *,
        account_id: str,
        run_id: str,
        call_key: str,
        purpose: str | None = None,
        now: datetime,
    ) -> bool:
        """登记一次外部调用（搜索/检索分支）；并发超过上限时拒绝。

        并行与串行消耗同一账本：``external_calls_active`` 是共享并发占位，
        全部分支领取与释放都经本方法族。
        """

        def consume(snapshot: RunBudgetSnapshot) -> bool:
            return snapshot.external_calls_active < snapshot.plan.external_parallel_max

        def extra_fields(snapshot: RunBudgetSnapshot) -> dict[str, Any]:
            return {
                "external_calls_active": snapshot.external_calls_active + 1,
                "external_parallel_max": snapshot.plan.external_parallel_max,
                "execution_process": self._execution_process_token(),
            }

        return self._mutate(
            account_id,
            run_id,
            now,
            kind="external_call",
            purpose=purpose,
            call_key=call_key,
            counter_updates={
                "external_calls_used": 1,
                "external_calls_active": 1,
            },
            gate=consume,
            extra_detail=extra_fields,
        )

    def recover_external_calls(self, account_id: str, run_id: str, *, now: datetime) -> None:
        """单实例进程重启后回收旧进程的占位；不重置历史调用消耗。"""
        with self.transaction():
            entries = self.list_entries(account_id, run_id)
            outstanding: dict[str, list[RunBudgetEntry]] = {}
            for entry in entries:
                if entry.call_key is None:
                    continue
                if entry.kind == "external_call":
                    outstanding.setdefault(entry.call_key, []).append(entry)
                elif entry.kind == "external_call_result" and outstanding.get(entry.call_key):
                    outstanding[entry.call_key].pop(0)
            for call_key, calls in outstanding.items():
                for call in calls:
                    if call.detail.get("execution_process") != self._execution_process_token():
                        self.record_external_call_result(
                            account_id=account_id, run_id=run_id, call_key=call_key,
                            outcome_code="process_restarted", now=now,
                        )

    def record_external_call_result(
        self,
        *,
        account_id: str,
        run_id: str,
        call_key: str,
        outcome_code: str | None = None,
        duration_ms: int | None = None,
        excluded_wait_ms: int = 0,
        now: datetime,
    ) -> None:
        """释放一次外部调用的并发占位（结果只记结果码；重复释放安全）。"""

        def consume(snapshot: RunBudgetSnapshot) -> bool:
            row = self._db.scoped(account_id).execute(
                "SELECT SUM(CASE WHEN kind = 'external_call' THEN 1 ELSE -1 END) AS active"
                " FROM run_budget_entries WHERE run_id = ? AND account_id = ?"
                " AND call_key = ? AND kind IN ('external_call', 'external_call_result')",
                (run_id, account_id, call_key),
            ).fetchone()
            return bool(row["active"] and row["active"] > 0)

        def extra_fields(snapshot: RunBudgetSnapshot) -> dict[str, Any]:
            return {
                "external_calls_active": max(0, snapshot.external_calls_active - 1),
                "excluded_wait_ms": max(0, excluded_wait_ms),
            }

        self._mutate(
            account_id,
            run_id,
            now,
            kind="external_call_result",
            call_key=call_key,
            outcome_code=outcome_code,
            duration_ms=duration_ms,
            counter_updates={"external_calls_active": -1},
            gate=consume,
            extra_detail=extra_fields,
            deadline_extension_ms=max(0, excluded_wait_ms),
            allow_closed=True,
        )

    def begin_adjustment(
        self,
        *,
        account_id: str,
        run_id: str,
        reason_code: str | None = None,
        now: datetime,
    ) -> bool:
        """开始一轮自动补证/调整；整次运行最多 ``adjustment_rounds_max`` 轮。

        结构修复与内容修复共享该轮：第二次调用一律拒绝（第二轮自动补证
        被代码拒绝，模型不能追加）。
        """

        def consume(snapshot: RunBudgetSnapshot) -> bool:
            return snapshot.adjustment_rounds_used < snapshot.plan.adjustment_rounds_max

        def extra_fields(snapshot: RunBudgetSnapshot) -> dict[str, Any]:
            return {
                "adjustment_rounds_used": snapshot.adjustment_rounds_used + 1,
                "adjustment_rounds_max": snapshot.plan.adjustment_rounds_max,
            }

        return self._mutate(
            account_id,
            run_id,
            now,
            kind="adjustment_begin",
            outcome_code=reason_code,
            counter_updates={"adjustment_rounds_used": 1},
            gate=consume,
            extra_detail=extra_fields,
        )

    def end_adjustment(
        self,
        *,
        account_id: str,
        run_id: str,
        outcome_code: str | None = None,
        now: datetime,
        detail: dict[str, Any] | None = None,
    ) -> bool:
        """补记本轮自动补证/调整的结果（初值、实测与调整结果分开记录）。

        只追加流水不增减计数；已关闭账本拒绝补记。执行方（票 10/12/37）
        在调整计划收敛后调用一次。
        """
        return self._mutate(
            account_id,
            run_id,
            now,
            kind="adjustment_end",
            outcome_code=outcome_code,
            counter_updates={},
            extra_detail_values=detail,
            gate=lambda _snapshot: True,
        )

    def mark_exhausted(
        self,
        *,
        account_id: str,
        run_id: str,
        reason_code: str,
        now: datetime,
    ) -> bool:
        """标记预算耗尽（终态收敛前的明确终止原因；幂等）。"""
        with self.transaction():
            snapshot = self.load(account_id, run_id)
            if snapshot is None or snapshot.status != "active":
                return False
            updated = self._db.scoped(account_id).execute(
                """
                UPDATE run_budget_ledger
                SET status = 'exhausted', exhausted_reason = ?, updated_at = ?,
                    version = version + 1
                WHERE run_id = ? AND account_id = ? AND status = 'active'
                  AND version = ?
                """,
                (reason_code, _iso(now), run_id, account_id, snapshot.version),
            ).rowcount
            if not updated:
                return False
            self._append_entry(
                account_id,
                run_id,
                kind="exhausted",
                outcome_code=reason_code,
                now=now,
            )
        return True

    def close(self, *, account_id: str, run_id: str, now: datetime) -> bool:
        """运行终态后关闭账本（只读封存；幂等，重复关闭返回 False）。"""
        with self.transaction():
            snapshot = self.load(account_id, run_id)
            if snapshot is None or snapshot.status == "closed":
                return False
            updated = self._db.scoped(account_id).execute(
                """
                UPDATE run_budget_ledger
                SET status = 'closed', updated_at = ?, version = version + 1
                WHERE run_id = ? AND account_id = ? AND status != 'closed'
                  AND version = ?
                """,
                (_iso(now), run_id, account_id, snapshot.version),
            ).rowcount
            if not updated:
                return False
            self._append_entry(account_id, run_id, kind="closed", now=now)
        return True

    def can_wait_until(
        self, account_id: str, run_id: str, until: datetime
    ) -> bool:
        """限流/冷却等待是否放得下：等待截止晚于冻结绝对截止即拒绝。

        限流等待超过剩余预算就结束运行；有真实恢复时刻时由调用方按用户
        时区（Asia/Shanghai）呈现。
        """
        snapshot = self.load(account_id, run_id)
        if snapshot is None or not snapshot.active:
            return False
        run = self._db.scoped(account_id).execute(
            "SELECT stop_requested, status FROM generation_runs"
            " WHERE run_id = ? AND account_id = ?", (run_id, account_id),
        ).fetchone()
        if run is not None and (
            run["stop_requested"] or run["status"] not in ("queued", "running")
        ):
            return False
        return until <= snapshot.plan.deadline_at - timedelta(
            milliseconds=snapshot.plan.verify_deliver_reserve_ms
        )
