"""持久化整次运行预算账本（改进工单 09）。

一次运行的截止时间、调用/token 上限、核验/交付预留与已耗计数在运行
创建时冻结落库；全部计数只由执行内核（执行器与编排代码）经本仓库的
方法修改，模型输出不能延时、扩容或重置任何额度。并行与串行消耗、
结构修复与供应商重试都记在同一个账本行上；租约恢复重新加载同一行，
恢复不产生新预算。

初值方案（docs/workflow/orchestration.md §10 已确认，待 40/42 实测校准，
不构成性能承诺）：

- 普通检索运行（normal）：总预算 60 秒，核验/交付预留 15 秒；
- 明确深入/书页/多模态运行（deep）：总预算最多 120 秒，预留 30 秒；
- 普通轻量交流（lightweight）：沿用已验证前台硬上限与调用超时；
- 外部独立并行初期最多 2，初筛最多 20 个候选，普通重点深读 3、深入 5；
- 自动补证/调整整次运行最多一轮（结构与内容修复共享）；每个登记的
  临时传输失败最多重试 1 次（须符合错误类型、冷却与剩余额度）。

流水（``run_budget_entries``）只记录标识、结果码、计数与毫秒——绝不
记录消息正文、提示词、搜索结果或凭据；调用目的分别计量。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from bridges.storage.database import BridgesDatabase

#: 账本合同版本；未来字段演进时递增，未知版本读回时闭锁而不是猜测语义。
RUN_BUDGET_CONTRACT_VERSION = "run-budget-v1"


class RunBudgetClass(StrEnum):
    """运行预算类别（创建时由内核派生，一经冻结不可更改）。"""

    LIGHTWEIGHT = "lightweight"
    NORMAL = "normal"
    DEEP = "deep"


@dataclass(frozen=True, slots=True)
class RunBudgetInitials:
    """一个类别的冻结初值方案（待校准初值，不是性能承诺）。"""

    total_budget_ms: int
    verify_deliver_reserve_ms: int
    model_call_limit: int
    external_parallel_max: int
    candidate_screen_max: int
    deep_read_max: int
    adjustment_rounds_max: int
    transient_retry_max: int


#: 已确认初值方案（orchestration.md §10）。lightweight 沿用已验证前台
#: 硬上限 120 秒且不设预留（普通闲聊只有一次生成调用与收尾，沿用已验证
#: 调用超时）；normal/deep 的核验/交付预留分别为 15/30 秒——交付前的
#: 阶段截止按「总预算 − 预留」收紧，保证核验与交付空间不被吃掉。
#: 调用数上限为保守初值（每日编排真实调用 1–2 次 + 一轮共享修复的余量），
#: 由配方票（10/12/37）按必要节点重算，40/42 校准。
RUN_BUDGET_INITIALS: dict[RunBudgetClass, RunBudgetInitials] = {
    RunBudgetClass.LIGHTWEIGHT: RunBudgetInitials(
        total_budget_ms=120_000,
        verify_deliver_reserve_ms=0,
        model_call_limit=3,
        external_parallel_max=2,
        candidate_screen_max=20,
        deep_read_max=3,
        adjustment_rounds_max=1,
        transient_retry_max=1,
    ),
    RunBudgetClass.NORMAL: RunBudgetInitials(
        total_budget_ms=60_000,
        verify_deliver_reserve_ms=15_000,
        model_call_limit=6,
        external_parallel_max=2,
        candidate_screen_max=20,
        deep_read_max=3,
        adjustment_rounds_max=1,
        transient_retry_max=1,
    ),
    RunBudgetClass.DEEP: RunBudgetInitials(
        total_budget_ms=120_000,
        verify_deliver_reserve_ms=30_000,
        model_call_limit=8,
        external_parallel_max=2,
        candidate_screen_max=20,
        deep_read_max=5,
        adjustment_rounds_max=1,
        transient_retry_max=1,
    ),
}


def derive_run_budget_class(
    *,
    mode: str | None = None,
    route_is_paper_search: bool = False,
    has_image: bool = False,
    has_video: bool = False,
) -> RunBudgetClass:
    """从创建时可见的信号派生预算类别（内核职责，模型不可指定）。

    - 书页处理（学习模式）、论文深入分析、图片/视频多模态处理是重材料
      运行 → ``deep``（120 秒/30 秒预留，保住这些路径已验证的信封）；
    - 其余（普通文本交流、知识库检索、公开搜索）→ ``normal``（60 秒）。
    ``lightweight`` 留给后台摘要/提取等独立有界运行（各自票内接线）。
    """
    if mode == "study" or route_is_paper_search or has_image or has_video:
        return RunBudgetClass.DEEP
    return RunBudgetClass.NORMAL


@dataclass(frozen=True, slots=True)
class RunBudgetPlan:
    """运行创建时冻结的预算计划（token_budget 为 None 时只计量不强制）。"""

    budget_class: RunBudgetClass
    total_budget_ms: int
    deadline_at: datetime
    verify_deliver_reserve_ms: int
    model_call_limit: int
    token_budget: int | None
    external_parallel_max: int
    candidate_screen_max: int
    deep_read_max: int
    adjustment_rounds_max: int
    transient_retry_max: int


def derive_run_budget_plan(
    budget_class: RunBudgetClass,
    *,
    deadline_at: datetime,
    token_budget: int | None = None,
) -> RunBudgetPlan:
    """按类别初值生成冻结计划。

    ``deadline_at`` 是冻结的绝对截止（UTC）；``token_budget`` 由调用方
    从运行额度快照派生（如 已验证最大输入 × 调用上限），缺省只计量。
    """
    initials = RUN_BUDGET_INITIALS[budget_class]
    return RunBudgetPlan(
        budget_class=budget_class,
        total_budget_ms=initials.total_budget_ms,
        deadline_at=deadline_at,
        verify_deliver_reserve_ms=initials.verify_deliver_reserve_ms,
        model_call_limit=initials.model_call_limit,
        token_budget=token_budget,
        external_parallel_max=initials.external_parallel_max,
        candidate_screen_max=initials.candidate_screen_max,
        deep_read_max=initials.deep_read_max,
        adjustment_rounds_max=initials.adjustment_rounds_max,
        transient_retry_max=initials.transient_retry_max,
    )


@dataclass(frozen=True, slots=True)
class RunBudgetSnapshot:
    """账本行快照：冻结计划 + 已耗计数 + 生命周期与乐观版本。"""

    run_id: str
    account_id: str
    conversation_id: str
    contract_version: str
    plan: RunBudgetPlan
    model_calls_used: int
    external_calls_used: int
    external_calls_active: int
    transient_retries_used: int
    adjustment_rounds_used: int
    input_tokens_used: int
    output_tokens_used: int
    status: str
    exhausted_reason: str | None
    version: int
    created_at: datetime
    updated_at: datetime

    @property
    def active(self) -> bool:
        return self.status == "active"


@dataclass(frozen=True, slots=True)
class RunBudgetEntry:
    """一条脱敏计量流水（标识/码/计数，无正文无凭据）。"""

    run_id: str
    seq: int
    account_id: str
    kind: str
    purpose: str | None
    call_key: str | None
    attempt: int | None
    outcome_code: str | None
    duration_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    detail: dict[str, Any]
    created_at: datetime


class RunBudgetLedgerRepository:
    """运行预算账本的 SQLite 写模型；账本的全部内核修改入口。

    所有方法限定账户作用域（``scoped``），写入走显式事务；每次计数变更
    以乐观版本守卫（``version`` 列 CAS）提交，并追加一条流水。账本行
    缺失或已离开 ``active`` 时拒绝消耗——调用方按预算拒绝降级交付。
    """

    def __init__(self, database: BridgesDatabase) -> None:
        self._db = database

    # ------------------------------------------------------------------
    # 冻结与读取
    # ------------------------------------------------------------------

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
        existing = self.load(account_id, run_id)
        if existing is not None:
            return existing
        with self._db.transaction():
            self._db.scoped(account_id).execute(
                """
                INSERT INTO run_budget_ledger (
                    run_id, account_id, conversation_id, contract_version,
                    budget_class, total_budget_ms, deadline_at,
                    verify_deliver_reserve_ms, model_call_limit, token_budget,
                    external_parallel_max, candidate_screen_max, deep_read_max,
                    adjustment_rounds_max, transient_retry_max,
                    status, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
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
        plan: RunBudgetPlan,
        run_created_at: datetime,
        now: datetime,
    ) -> RunBudgetSnapshot:
        """确保账本存在；缺失时以运行创建时刻锚定补建（先持久化再使用）。

        兼容路径（票 03 同一纪律）：执行器/编排拿到运行时若账本行缺失
        （创建事务与冻结之间的崩溃、旧数据），必须先补齐持久状态再继续，
        且截止时间锚定运行创建时刻——兼容补齐绝不把截止时间向后延。
        """
        existing = self.load(account_id, run_id)
        if existing is not None:
            return existing
        # 锚定运行创建时刻：截止 = 创建时刻 + 冻结总预算（排队时间计入
        # 预算），兼容补齐绝不把截止向后延。
        anchored = replace(
            plan,
            deadline_at=run_created_at + timedelta(milliseconds=plan.total_budget_ms),
        )
        snapshot = self.freeze_for_run(
            account_id=account_id,
            run_id=run_id,
            conversation_id=conversation_id,
            plan=anchored,
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

    # ------------------------------------------------------------------
    # 内核消耗入口（全部：校验 → 乐观版本 CAS 更新 → 追加流水）
    # ------------------------------------------------------------------

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
            return not (
                snapshot.plan.token_budget is not None
                and snapshot.input_tokens_used >= snapshot.plan.token_budget
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
        self._mutate(
            account_id,
            run_id,
            now,
            kind="model_call_result",
            call_key=call_key,
            attempt=attempt,
            outcome_code=outcome_code,
            duration_ms=duration_ms,
            counter_updates={
                "input_tokens_used": max(0, input_tokens or 0),
                "output_tokens_used": max(0, output_tokens or 0),
            },
            gate=lambda _snapshot: True,
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

        幂等守卫：同一 (run_id, call_key, attempt) 的重复登记按幂等成功
        处理且不重复计数（部分唯一索引 ``idx_run_budget_entries_retry_once``）。
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
            return int(row["count"]) < snapshot.plan.transient_retry_max

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
            counter_updates={"transient_retries_used": 1},
            gate=consume,
            extra_detail=extra_fields,
            extra_detail_values={"backoff_ms": backoff_ms} if backoff_ms else None,
            idempotent_unique=True,
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

    def record_external_call_result(
        self,
        *,
        account_id: str,
        run_id: str,
        call_key: str,
        outcome_code: str | None = None,
        now: datetime,
    ) -> None:
        """释放一次外部调用的并发占位（结果只记结果码；重复释放安全）。"""

        def consume(snapshot: RunBudgetSnapshot) -> bool:
            return True

        def extra_fields(snapshot: RunBudgetSnapshot) -> dict[str, Any]:
            return {"external_calls_active": max(0, snapshot.external_calls_active - 1)}

        self._mutate(
            account_id,
            run_id,
            now,
            kind="external_call_result",
            call_key=call_key,
            outcome_code=outcome_code,
            counter_updates={"external_calls_active": -1},
            gate=consume,
            extra_detail=extra_fields,
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

    def mark_exhausted(
        self,
        *,
        account_id: str,
        run_id: str,
        reason_code: str,
        now: datetime,
    ) -> bool:
        """标记预算耗尽（终态收敛前的明确终止原因；幂等）。"""
        snapshot = self.load(account_id, run_id)
        if snapshot is None or snapshot.status != "active":
            return False
        with self._db.transaction():
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
        snapshot = self.load(account_id, run_id)
        if snapshot is None or snapshot.status == "closed":
            return False
        with self._db.transaction():
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
        return until <= snapshot.plan.deadline_at

    def list_entries(self, account_id: str, run_id: str) -> list[RunBudgetEntry]:
        rows = self._db.scoped(account_id).execute(
            "SELECT * FROM run_budget_entries WHERE run_id = ? AND account_id = ?"
            " ORDER BY seq",
            (run_id, account_id),
        ).fetchall()
        return [_entry_from_row(row) for row in rows]

    # ------------------------------------------------------------------
    # 内部：统一「校验 → 版本 CAS → 流水」变更核心
    # ------------------------------------------------------------------

    def _mutate(
        self,
        account_id: str,
        run_id: str,
        now: datetime,
        *,
        kind: str,
        counter_updates: dict[str, int],
        gate: Any,
        call_key: str | None = None,
        purpose: str | None = None,
        attempt: int | None = None,
        outcome_code: str | None = None,
        duration_ms: int | None = None,
        extra_detail: Any = None,
        extra_detail_values: dict[str, Any] | None = None,
        allow_closed: bool = False,
        idempotent_unique: bool = False,
    ) -> bool:
        """账本变更核心：读取快照 → 门校验 → 乐观版本 CAS → 追加流水。

        版本冲突（并发写）时重读快照重试至多重试上限；``idempotent_unique``
        时唯一索引冲突按幂等成功处理（不重复计数）。
        """
        for _ in range(3):
            snapshot = self.load(account_id, run_id)
            if snapshot is None:
                return False
            if snapshot.status != "active" and not (allow_closed and snapshot.status == "closed"):
                return False
            if not gate(snapshot):
                return False
            detail: dict[str, Any] = {}
            if extra_detail is not None:
                detail.update(extra_detail(snapshot))
            if extra_detail_values:
                detail.update(extra_detail_values)
            set_clause = ", ".join(
                f"{column} = MAX(0, {column} + ?)" for column in counter_updates
            )
            params = [*counter_updates.values(), _iso(now), run_id, account_id, snapshot.version]
            try:
                with self._db.transaction():
                    updated = self._db.scoped(account_id).execute(
                        f"""
                        UPDATE run_budget_ledger
                        SET {set_clause}, updated_at = ?, version = version + 1
                        WHERE run_id = ? AND account_id = ? AND version = ?
                        """,
                        params,
                    ).rowcount
                    if not updated:
                        raise _VersionConflictError()
                    self._append_entry(
                        account_id,
                        run_id,
                        kind=kind,
                        purpose=purpose,
                        call_key=call_key,
                        attempt=attempt,
                        outcome_code=outcome_code,
                        duration_ms=duration_ms,
                        detail=detail or None,
                        now=now,
                    )
                return True
            except sqlite3.IntegrityError:
                # 重复登记同一重试：幂等成功（计数不增）；其余唯一冲突拒绝。
                return idempotent_unique
            except _VersionConflictError:
                continue
        return False

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
        detail: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> None:
        """追加一条流水（seq 单调；在调用方事务内执行）。"""
        moment = _iso(now) if now is not None else _iso(datetime.now(UTC))
        self._db.scoped(account_id).execute(
            """
            INSERT INTO run_budget_entries (
                run_id, seq, account_id, kind, purpose, call_key, attempt,
                outcome_code, duration_ms, detail_json, created_at
            )
            VALUES (
                ?,
                COALESCE((SELECT MAX(seq) FROM run_budget_entries
                          WHERE run_id = ?), 0) + 1,
                ?, ?, ?, ?, ?, ?, ?, ?, ?
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
                json.dumps(detail, ensure_ascii=False) if detail else "{}",
                moment,
            ),
        )


class _VersionConflictError(Exception):
    """乐观版本守卫冲突（内部信号；并发写时重读重试）。"""


def _iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat()


def _parse(moment: str | None) -> datetime | None:
    if moment is None:
        return None
    parsed = datetime.fromisoformat(moment)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _snapshot_from_row(row: sqlite3.Row) -> RunBudgetSnapshot:
    plan = RunBudgetPlan(
        budget_class=RunBudgetClass(row["budget_class"]),
        total_budget_ms=int(row["total_budget_ms"]),
        deadline_at=_parse(row["deadline_at"]) or datetime.now(UTC),
        verify_deliver_reserve_ms=int(row["verify_deliver_reserve_ms"]),
        model_call_limit=int(row["model_call_limit"]),
        token_budget=(
            int(row["token_budget"]) if row["token_budget"] is not None else None
        ),
        external_parallel_max=int(row["external_parallel_max"]),
        candidate_screen_max=int(row["candidate_screen_max"]),
        deep_read_max=int(row["deep_read_max"]),
        adjustment_rounds_max=int(row["adjustment_rounds_max"]),
        transient_retry_max=int(row["transient_retry_max"]),
    )
    return RunBudgetSnapshot(
        run_id=str(row["run_id"]),
        account_id=str(row["account_id"]),
        conversation_id=str(row["conversation_id"]),
        contract_version=str(row["contract_version"]),
        plan=plan,
        model_calls_used=int(row["model_calls_used"]),
        external_calls_used=int(row["external_calls_used"]),
        external_calls_active=int(row["external_calls_active"]),
        transient_retries_used=int(row["transient_retries_used"]),
        adjustment_rounds_used=int(row["adjustment_rounds_used"]),
        input_tokens_used=int(row["input_tokens_used"]),
        output_tokens_used=int(row["output_tokens_used"]),
        status=str(row["status"]),
        exhausted_reason=row["exhausted_reason"],
        version=int(row["version"]),
        created_at=_parse(row["created_at"]) or datetime.now(UTC),
        updated_at=_parse(row["updated_at"]) or datetime.now(UTC),
    )


def _entry_from_row(row: sqlite3.Row) -> RunBudgetEntry:
    try:
        detail = json.loads(row["detail_json"] or "{}")
    except (TypeError, ValueError):
        detail = {}
    return RunBudgetEntry(
        run_id=str(row["run_id"]),
        seq=int(row["seq"]),
        account_id=str(row["account_id"]),
        kind=str(row["kind"]),
        purpose=row["purpose"],
        call_key=row["call_key"],
        attempt=int(row["attempt"]) if row["attempt"] is not None else None,
        outcome_code=row["outcome_code"],
        duration_ms=int(row["duration_ms"]) if row["duration_ms"] is not None else None,
        input_tokens=(
            int(row["input_tokens"]) if row["input_tokens"] is not None else None
        ),
        output_tokens=(
            int(row["output_tokens"]) if row["output_tokens"] is not None else None
        ),
        detail=detail if isinstance(detail, dict) else {},
        created_at=_parse(row["created_at"]) or datetime.now(UTC),
    )
