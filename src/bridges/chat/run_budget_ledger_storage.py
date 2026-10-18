from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from bridges.chat.run_budget_ledger_types import (
    _EXECUTION_PROCESS,
    RUN_BUDGET_CONTRACT_VERSION,
    RunBudgetClass,
    RunBudgetEntry,
    RunBudgetPlan,
    RunBudgetSnapshot,
    _entry_from_row,
    _iso,
    _parse,
    _recipe_costs_dict,
    _snapshot_from_row,
    anchor_run_budget_deadline,
    derive_run_budget_plan,
)
from bridges.storage.database import BridgesDatabase


class _RunBudgetLedgerStorage:
    """内部账本操作实现。"""
    def __init__(self, database: BridgesDatabase) -> None:
        self._db = database

    def _execution_process_token(self) -> str:
        return _EXECUTION_PROCESS

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """持有连接锁，组合操作并入外层事务。"""
        with self._db.snapshot_lock():
            if self._db.connection.in_transaction:
                yield
            else:
                with self._db.transaction():
                    yield

    def freeze_for_run(
        self,
        *,
        account_id: str,
        run_id: str,
        conversation_id: str,
        plan: RunBudgetPlan,
        now: datetime,
    ) -> RunBudgetSnapshot:
        """运行创建时冻结账本（幂等：已有账本行时原样返回，不覆盖）。

        冻结的截止时间取 ``plan.deadline_at``；重复调用（创建竞争/重试）
        读回既有行——冻结值一旦落库即不可改写。
        """
        with self.transaction():
            existing = self.load(account_id, run_id)
            if existing is not None:
                return existing
            self._db.scoped(account_id).execute(
                """
                INSERT INTO run_budget_ledger (
                    run_id, account_id, conversation_id, contract_version,
                    budget_class, total_budget_ms, deadline_at,
                    verify_deliver_reserve_ms, model_call_limit, token_budget,
                    external_parallel_max, candidate_screen_max, deep_read_max,
                    adjustment_rounds_max, transient_retry_max, recipe_costs_json,
                    status, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          'active', 1, ?, ?)
                """,
                (
                    run_id,
                    account_id,
                    conversation_id,
                    RUN_BUDGET_CONTRACT_VERSION,
                    plan.budget_class.value,
                    plan.total_budget_ms,
                    _iso(plan.deadline_at),
                    plan.verify_deliver_reserve_ms,
                    plan.model_call_limit,
                    plan.token_budget,
                    plan.external_parallel_max,
                    plan.candidate_screen_max,
                    plan.deep_read_max,
                    plan.adjustment_rounds_max,
                    plan.transient_retry_max,
                    json.dumps(_recipe_costs_dict(plan.recipe_costs), ensure_ascii=False),
                    _iso(now),
                    _iso(now),
                ),
            )
        snapshot = self.load(account_id, run_id)
        assert snapshot is not None
        return snapshot

    def ensure_for_run(
        self,
        *,
        account_id: str,
        run_id: str,
        conversation_id: str,
        budget_class: RunBudgetClass,
        run_created_at: datetime,
        now: datetime,
        token_budget: int | None = None,
    ) -> RunBudgetSnapshot:
        """确保账本存在；缺失时以运行创建时刻锚定补建（先持久化再使用）。

        兼容路径（票 03 同一纪律）：执行器/编排拿到运行时若账本行缺失
        （创建事务与冻结之间的崩溃、旧数据），必须先补齐持久状态再继续，
        且截止时间锚定运行创建时刻——兼容补齐绝不把截止向后延。计划由
        类别初值在本模块内生成（冻结口径与创建路径同源）。
        """
        with self.transaction():
            existing = self.load(account_id, run_id)
            if existing is not None:
                return existing
            plan = derive_run_budget_plan(
                budget_class,
                deadline_at=anchor_run_budget_deadline(run_created_at, budget_class),
                token_budget=token_budget,
            )
            snapshot = self.freeze_for_run(
                account_id=account_id,
                run_id=run_id,
                conversation_id=conversation_id,
                plan=plan,
                now=now,
            )
            self._append_entry(
                account_id,
                run_id,
                kind="compat_created",
                outcome_code="run_budget_compat_anchored_to_run_created_at",
                now=now,
            )
            return snapshot

    def load(self, account_id: str, run_id: str) -> RunBudgetSnapshot | None:
        row = self._db.scoped(account_id).execute(
            "SELECT * FROM run_budget_ledger WHERE run_id = ? AND account_id = ?",
            (run_id, account_id),
        ).fetchone()
        return _snapshot_from_row(row) if row is not None else None

    def delete_for_conversation(self, account_id: str, conversation_id: str) -> None:
        """会话删除时清理同账户预算账本及其计量流水。"""
        with self.transaction():
            self._db.scoped(account_id).execute(
                "DELETE FROM run_budget_entries WHERE account_id = ? AND run_id IN"
                " (SELECT run_id FROM run_budget_ledger"
                " WHERE account_id = ? AND conversation_id = ?)",
                (account_id, account_id, conversation_id),
            )
            self._db.scoped(account_id).execute(
                "DELETE FROM run_budget_ledger WHERE account_id = ? AND conversation_id = ?",
                (account_id, conversation_id),
            )

    def delete_for_run(self, account_id: str, run_id: str) -> None:
        """运维删除运行时同时清理其预算。"""
        with self.transaction():
            for table in ("run_budget_entries", "run_budget_ledger"):
                self._db.scoped(account_id).execute(
                    f"DELETE FROM {table} WHERE account_id = ? AND run_id = ?",
                    (account_id, run_id),
                )

    def list_entries(self, account_id: str, run_id: str) -> list[RunBudgetEntry]:
        rows = self._db.scoped(account_id).execute(
            "SELECT * FROM run_budget_entries WHERE run_id = ? AND account_id = ?"
            " ORDER BY seq",
            (run_id, account_id),
        ).fetchall()
        return [_entry_from_row(row) for row in rows]

    def _mutate(
        self,
        account_id: str,
        run_id: str,
        now: datetime,
        *,
        kind: str,
        counter_updates: dict[str, int],
        gate: Callable[[RunBudgetSnapshot], bool],
        call_key: str | None = None,
        purpose: str | None = None,
        attempt: int | None = None,
        outcome_code: str | None = None,
        duration_ms: int | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        extra_detail: Callable[[RunBudgetSnapshot], dict[str, Any]] | None = None,
        extra_detail_values: dict[str, Any] | None = None,
        deadline_extension_ms: int = 0,
        allow_closed: bool = False,
        reason_out: list[str] | None = None,
        snapshot_out: list[RunBudgetSnapshot] | None = None,
        denial_reason: str = "denied",
    ) -> bool:
        """在同一写事务中检查许可、更新版本和追加流水，避免取消竞争。

        ``reason_out``/``snapshot_out`` 只供需要区分许可原因的调用方使用；
        其余调用方仍只看布尔结果，账本行为不变。
        """

        def deny(reason: str) -> bool:
            if reason_out is not None:
                reason_out.append(reason)
            return False

        with self.transaction():
            snapshot = self.load(account_id, run_id)
            if snapshot is not None and snapshot_out is not None:
                snapshot_out.append(snapshot)
            if snapshot is None or snapshot.contract_version != RUN_BUDGET_CONTRACT_VERSION:
                return deny("run_invalidated")
            if not snapshot.active and not allow_closed:
                return deny("run_invalidated")
            if not allow_closed:
                work_deadline = snapshot.plan.deadline_at - timedelta(
                    milliseconds=snapshot.plan.verify_deliver_reserve_ms
                )
                if now >= work_deadline:
                    return deny("work_time_exhausted")
                if kind in {"model_call", "transient_retry", "external_call"}:
                    batches = [
                        e for e in self.list_entries(account_id, run_id)
                        if e.kind in {"batch_started", "batch_completed", "batch_failed"}
                    ]
                    if batches and batches[-1].kind == "batch_started":
                        batch = batches[-1].detail
                        if batch.get("execution_process") != self._execution_process_token():
                            return deny("run_invalidated")
                        batch_deadline = _parse(batch["deadline_at"])
                        if batch_deadline is None or now >= batch_deadline:
                            return deny("work_time_exhausted")
                        if kind != "external_call" and (
                            snapshot.model_calls_used - batch["calls_before"]
                            >= batch["model_call_limit"]
                            or snapshot.input_tokens_used + snapshot.output_tokens_used
                            - batch["tokens_before"] >= batch["token_limit"]
                        ):
                            return deny("work_time_exhausted")
                run = self._db.scoped(account_id).execute(
                    "SELECT stop_requested, status FROM generation_runs"
                    " WHERE run_id = ? AND account_id = ?", (run_id, account_id),
                ).fetchone()
                if run is not None:
                    if run["stop_requested"]:
                        return deny("cancelled")
                    if run["status"] not in ("queued", "running"):
                        return deny("run_invalidated")
            if not gate(snapshot):
                return deny(denial_reason)
            detail: dict[str, Any] = {}
            if extra_detail is not None:
                detail.update(extra_detail(snapshot))
            if extra_detail_values:
                detail.update(extra_detail_values)
            assignments = [
                f"{column} = MAX(0, {column} + ?)" for column in counter_updates
            ]
            if deadline_extension_ms:
                assignments.append("deadline_at = ?")
            assignments.append("updated_at = ?")
            set_clause = ", ".join(assignments)
            params: list[int | str] = [*counter_updates.values()]
            if deadline_extension_ms:
                deadline = snapshot.plan.deadline_at + timedelta(
                    milliseconds=deadline_extension_ms
                )
                params.append(_iso(deadline))
            params.extend((_iso(now), run_id, account_id, snapshot.version))
            updated = self._db.scoped(account_id).execute(
                f"UPDATE run_budget_ledger SET {set_clause}, version = version + 1"
                " WHERE run_id = ? AND account_id = ? AND version = ?", params,
            ).rowcount
            if not updated:
                return deny("contended")
            self._append_entry(
                account_id, run_id, kind=kind, purpose=purpose, call_key=call_key,
                attempt=attempt, outcome_code=outcome_code, duration_ms=duration_ms,
                input_tokens=input_tokens, output_tokens=output_tokens,
                detail=detail or None, now=now,
            )
        return True

    def _append_entry(
        self,
        account_id: str,
        run_id: str,
        *,
        kind: str,
        purpose: str | None = None,
        call_key: str | None = None,
        attempt: int | None = None,
        outcome_code: str | None = None,
        duration_ms: int | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        detail: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> None:
        """追加一条流水（seq 单调；在调用方事务内执行）。"""
        moment = _iso(now) if now is not None else _iso(datetime.now(UTC))
        self._db.scoped(account_id).execute(
            """
            INSERT INTO run_budget_entries (
                run_id, seq, account_id, kind, purpose, call_key, attempt,
                outcome_code, duration_ms, input_tokens, output_tokens, detail_json, created_at
            )
            VALUES (
                ?,
                COALESCE((SELECT MAX(seq) FROM run_budget_entries
                          WHERE run_id = ?), 0) + 1,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                run_id,
                run_id,
                account_id,
                kind,
                purpose,
                call_key,
                attempt,
                outcome_code,
                duration_ms,
                input_tokens,
                output_tokens,
                json.dumps(detail, ensure_ascii=False) if detail else "{}",
                moment,
            ),
        )
