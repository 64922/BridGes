"""持久化整次运行预算账本（改进工单 09）。

一次运行的截止时间、调用/token 上限、核验/交付预留与已耗计数在运行
创建时冻结落库；全部计数只由执行内核（执行器与编排代码）经本仓库的
方法修改，模型输出不能延时、扩容或重置任何额度。并行与串行消耗、
结构修复与供应商重试都记在同一个账本行上；租约恢复重新加载同一行，
恢复不产生新预算。

初值方案（docs/workflow/orchestration.md §10 已确认，待 40/42 实测校准，
不构成性能承诺）：

- 普通检索运行（normal）：总预算 60 秒，核验/交付预留 15 秒；
- 明确深入及其他 deep 运行：总预算 120 秒，预留 30 秒；带照片的学习
  批次由 ``derive_study_run_budget_plan`` 按页数分配专用预算，最多 15 分钟；
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
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from bridges.routing import CapabilityRoute, MainCapability, RouteStatus

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

# 学习书页批次每页预算 OCR 与视觉识别时间，随后还需映射、核验范围与
# 生成预习问题；另保留 deep 初值的核验/交付预留。该专用预算最多 15 分钟。
STUDY_PAGE_BUDGET_MS = 180_000
STUDY_FOLLOW_UP_BUDGET_MS = 210_000
STUDY_MAX_RUN_BUDGET_MS = 900_000
STUDY_FOLLOW_UP_CALLS = 3

# 贴吧采集/总结需要一个统一的任务截止，登录验证等待通过账本截止扩展扣除。
TIEBA_RUN_TOTAL_BUDGET_MS = 300_000
TIEBA_RUN_VERIFY_RESERVE_MS = 45_000


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

    - 学习模式、论文深入分析、图片/视频多模态与职业规划等重材料/重流程
      运行 → ``deep``；带照片的学习轮随后由 ``derive_study_run_budget_plan``
      按页数应用专用预算，其他 deep 运行使用 120 秒/30 秒预留；
    - 其余（普通闲聊、澄清、润色等纯交流）→ ``lightweight``（沿用已验证
      前台硬上限 120 秒、无预留——普通交流不占任务预算）。
    ``normal``（普通检索运行 60 秒）留给配方驱动的检索编排运行，由
    票 10/12/37 接线后启用。
    """
    capability = route.main_capability if route is not None else None
    if capability is MainCapability.TIEBA:
        return RunBudgetClass.DEEP
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


def derive_study_run_budget_plan(
    *, page_count: int, created_at: datetime, token_budget: int | None = None
) -> RunBudgetPlan:
    """按待识别书页数冻结专用预算；无书页时沿用 deep 初值。"""
    if page_count < 1:
        return derive_run_budget_plan(
            RunBudgetClass.DEEP,
            deadline_at=anchor_run_budget_deadline(created_at, RunBudgetClass.DEEP),
            token_budget=token_budget,
        )

    total_budget_ms = min(
        STUDY_MAX_RUN_BUDGET_MS,
        page_count * STUDY_PAGE_BUDGET_MS
        + STUDY_FOLLOW_UP_BUDGET_MS
        + RUN_BUDGET_INITIALS[RunBudgetClass.DEEP].verify_deliver_reserve_ms,
    )
    necessary_calls = page_count * 2 + STUDY_FOLLOW_UP_CALLS
    plan = derive_run_budget_plan(
        RunBudgetClass.DEEP,
        deadline_at=created_at + timedelta(milliseconds=total_budget_ms),
        token_budget=token_budget,
    )
    return replace(
        plan,
        total_budget_ms=total_budget_ms,
        model_call_limit=necessary_calls * (1 + plan.transient_retry_max),
    )


def derive_tieba_run_budget_plan(
    *, created_at: datetime, token_budget: int | None = None
) -> RunBudgetPlan:
    """为贴吧研究冻结五分钟任务预算，并为核验与交付保留时间。"""
    plan = derive_run_budget_plan(
        RunBudgetClass.DEEP,
        deadline_at=created_at + timedelta(milliseconds=TIEBA_RUN_TOTAL_BUDGET_MS),
        token_budget=token_budget,
    )
    return replace(
        plan,
        total_budget_ms=TIEBA_RUN_TOTAL_BUDGET_MS,
        verify_deliver_reserve_ms=TIEBA_RUN_VERIFY_RESERVE_MS,
        candidate_screen_max=40,
        deep_read_max=8,
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
