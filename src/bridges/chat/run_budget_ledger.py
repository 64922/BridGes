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
import secrets
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from bridges.routing import CapabilityRoute, MainCapability, RouteStatus
from bridges.storage.database import BridgesDatabase

#: 账本合同版本；未来字段演进时递增，未知版本读回时闭锁而不是猜测语义。
RUN_BUDGET_CONTRACT_VERSION = "run-budget-v1"

# 数据目录单实例锁保证进程更替时旧进程已退出；同进程租约恢复保留真实占位。
_EXECUTION_PROCESS = secrets.token_hex(12)


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
#: 硬上限 120 秒且不设预留（普通闲聊/澄清等纯交流沿用已验证调用超时，
#: 「核心是少调用而非占满任务预算」）；normal/deep 的核验/交付预留分别
#: 为 15/30 秒——交付前的阶段截止按「总预算 − 预留」收紧，保证核验与
#: 交付空间不被吃掉。调用数上限为保守守门初值（正常流程真实调用 1–2 次
#: + 一轮共享修复的余量；普通交流含工具轮，与 normal 同为 6），由配方票
#: （10/12/37）按必要节点重算，40/42 校准。
RUN_BUDGET_INITIALS: dict[RunBudgetClass, RunBudgetInitials] = {
    RunBudgetClass.LIGHTWEIGHT: RunBudgetInitials(
        total_budget_ms=120_000,
        verify_deliver_reserve_ms=0,
        model_call_limit=6,
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


#: 创建时即确定走重材料/重流程路线的主能力（保留 120 秒/30 秒预留的
#: 深入信封）；论文深入分析在其中，图片/视频/职业规划同理保守处理。
_DEEP_MAIN_CAPABILITIES = frozenset(
    {
        MainCapability.PAPER_SEARCH,
        MainCapability.IMAGE,
        MainCapability.VIDEO,
        MainCapability.CAREER,
    }
)


def derive_run_budget_class(
    *,
    mode: str | None = None,
    route: CapabilityRoute | None = None,
    has_image: bool = False,
    has_video: bool = False,
) -> RunBudgetClass:
    """从创建时可见的信号派生预算类别（内核职责，模型不可指定）。

    - 书页处理（学习模式）、论文深入分析、图片/视频多模态与职业规划
      等重材料/重流程运行 → ``deep``（120 秒/30 秒预留，保住已验证信封）；
    - 其余（普通闲聊、澄清、润色等纯交流）→ ``lightweight``（沿用已验证
      前台硬上限 120 秒、无预留——普通交流不占任务预算）。
    ``normal``（普通检索运行 60 秒）留给配方驱动的检索编排运行，由
    票 10/12/37 接线后启用。
    """
    capability = route.main_capability if route is not None else None
    if (
        route is not None
        and route.status is RouteStatus.MATCHED
        and len(route.capability_list) > 1
    ):
        # 工单 37：已登记跨模块复合运行按普通检索预算（60 秒/15 秒预留），
        # 全部模块分支共享同一本账。
        return RunBudgetClass.NORMAL
    if mode == "study" or capability in _DEEP_MAIN_CAPABILITIES or has_image or has_video:
        return RunBudgetClass.DEEP
    return RunBudgetClass.LIGHTWEIGHT


def anchor_run_budget_deadline(
    created_at: datetime, budget_class: RunBudgetClass
) -> datetime:
    """冻结口径唯一锚定：截止 = 运行创建时刻 + 类别总预算。

    排队时间计入预算；冻结与兼容补齐共用本公式，绝不把截止向后延。
    """
    return created_at + timedelta(
        milliseconds=RUN_BUDGET_INITIALS[budget_class].total_budget_ms
    )


@dataclass(frozen=True, slots=True)
class RunBudgetRecipeCosts:
    """代码登记的配方成本；必要节点、候选读取与一轮修复分别计入。"""

    recipe_version: str
    necessary_model_calls: int
    candidate_read_calls: int
    adjustment_model_calls: int
    input_tokens_per_call: int
    output_tokens_per_call: int

    def limits(self, transient_retry_max: int) -> tuple[int, int]:
        values = (
            self.necessary_model_calls, self.candidate_read_calls,
            self.adjustment_model_calls, self.input_tokens_per_call,
            self.output_tokens_per_call,
        )
        if not self.recipe_version or any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in values
        ):
            raise ValueError("配方版本不能为空，成本必须是非负整数。")
        calls = sum(values[:3]) * (1 + transient_retry_max)
        return calls, calls * (self.input_tokens_per_call + self.output_tokens_per_call)


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
    recipe_costs: RunBudgetRecipeCosts | None = None


def derive_run_budget_plan(
    budget_class: RunBudgetClass,
    *,
    deadline_at: datetime,
    token_budget: int | None = None,
    recipe_costs: RunBudgetRecipeCosts | None = None,
) -> RunBudgetPlan:
    """按类别初值生成冻结计划。

    ``deadline_at`` 是冻结的绝对截止（UTC）；``token_budget`` 由调用方
    从运行额度快照派生（如 已验证最大输入 × 调用上限），缺省只计量。
    """
    initials = RUN_BUDGET_INITIALS[budget_class]
    call_limit = initials.model_call_limit
    if recipe_costs is not None:
        call_limit, token_budget = recipe_costs.limits(initials.transient_retry_max)
    return RunBudgetPlan(
        budget_class=budget_class,
        total_budget_ms=initials.total_budget_ms,
        deadline_at=deadline_at,
        verify_deliver_reserve_ms=initials.verify_deliver_reserve_ms,
        model_call_limit=call_limit,
        token_budget=token_budget,
        external_parallel_max=initials.external_parallel_max,
        candidate_screen_max=initials.candidate_screen_max,
        deep_read_max=initials.deep_read_max,
        adjustment_rounds_max=initials.adjustment_rounds_max,
        transient_retry_max=initials.transient_retry_max,
        recipe_costs=recipe_costs,
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
        return self.status == "active" and self.contract_version == RUN_BUDGET_CONTRACT_VERSION


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

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """持有连接锁，组合操作并入外层事务。"""
        with self._db.snapshot_lock():
            if self._db.connection.in_transaction:
                yield
            else:
                with self._db.transaction():
                    yield

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
                "execution_process": _EXECUTION_PROCESS,
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
                    if call.detail.get("execution_process") != _EXECUTION_PROCESS:
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
                and last.detail.get("execution_process") != _EXECUTION_PROCESS
            )

        def limits(snapshot: RunBudgetSnapshot) -> dict[str, Any]:
            previous = next((
                e for e in self.list_entries(account_id, run_id)
                if e.call_key == batch_key and e.kind == "batch_started"
            ), None)
            if previous is not None:
                return {**previous.detail, "execution_process": _EXECUTION_PROCESS}
            plan = next(e for e in self.list_entries(account_id, run_id) if e.kind == "batch_plan")
            batch = next(
                b for b in plan.detail["batches"]
                if f"{plan.call_key}:{b['batch_id']}" == batch_key
            )
            return {
                "execution_process": _EXECUTION_PROCESS,
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
                started.detail.get("execution_process") == _EXECUTION_PROCESS
                and deadline is not None and now < deadline
            )

        return self._mutate(
            account_id, run_id, now,
            kind="batch_failed" if failed else "batch_completed", call_key=batch_key,
            counter_updates={}, gate=consume, extra_detail_values=detail,
        )

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
        allow_closed: bool = False,
    ) -> bool:
        """在同一写事务中检查许可、更新版本和追加流水，避免取消竞争。"""
        with self.transaction():
            snapshot = self.load(account_id, run_id)
            if snapshot is None or snapshot.contract_version != RUN_BUDGET_CONTRACT_VERSION:
                return False
            if not snapshot.active and not allow_closed:
                return False
            if not allow_closed:
                work_deadline = snapshot.plan.deadline_at - timedelta(
                    milliseconds=snapshot.plan.verify_deliver_reserve_ms
                )
                if now >= work_deadline:
                    return False
                if kind in {"model_call", "transient_retry", "external_call"}:
                    batches = [
                        e for e in self.list_entries(account_id, run_id)
                        if e.kind in {"batch_started", "batch_completed", "batch_failed"}
                    ]
                    if batches and batches[-1].kind == "batch_started":
                        batch = batches[-1].detail
                        if batch.get("execution_process") != _EXECUTION_PROCESS:
                            return False
                        batch_deadline = _parse(batch["deadline_at"])
                        if batch_deadline is None or now >= batch_deadline:
                            return False
                        if kind != "external_call" and (
                            snapshot.model_calls_used - batch["calls_before"]
                            >= batch["model_call_limit"]
                            or snapshot.input_tokens_used + snapshot.output_tokens_used
                            - batch["tokens_before"] >= batch["token_limit"]
                        ):
                            return False
                run = self._db.scoped(account_id).execute(
                    "SELECT stop_requested, status FROM generation_runs"
                    " WHERE run_id = ? AND account_id = ?", (run_id, account_id),
                ).fetchone()
                if run is not None and (
                    run["stop_requested"] or run["status"] not in ("queued", "running")
                ):
                    return False
            if not gate(snapshot):
                return False
            detail: dict[str, Any] = {}
            if extra_detail is not None:
                detail.update(extra_detail(snapshot))
            if extra_detail_values:
                detail.update(extra_detail_values)
            assignments = [
                f"{column} = MAX(0, {column} + ?)" for column in counter_updates
            ]
            assignments.append("updated_at = ?")
            set_clause = ", ".join(assignments)
            params = [*counter_updates.values(), _iso(now), run_id, account_id, snapshot.version]
            updated = self._db.scoped(account_id).execute(
                f"UPDATE run_budget_ledger SET {set_clause}, version = version + 1"
                " WHERE run_id = ? AND account_id = ? AND version = ?", params,
            ).rowcount
            if not updated:
                return False
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


def _recipe_costs_dict(costs: RunBudgetRecipeCosts | None) -> dict[str, Any]:
    if costs is None:
        return {}
    from dataclasses import asdict

    return asdict(costs)


def _snapshot_from_row(row: sqlite3.Row) -> RunBudgetSnapshot:
    recipe_data = json.loads(row["recipe_costs_json"])
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
        recipe_costs=RunBudgetRecipeCosts(**recipe_data) if recipe_data else None,
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
