"""统一阶段时钟、预算控制器与脱敏阶段指标（Issue 06 纵向切片）。

一次前台生成 run 建立统一阶段时钟：所有技能路径共用同一套阶段事件、
截止时间、取消信号与脱敏指标；彼此独立的公开搜索可并行执行；非必要
重试受剩余总预算约束。指标只记录 ID、阶段、毫秒、结果码、模型/工具
类别与计数——绝不记录消息、文档、搜索结果或密钥正文。

预算语义：``RunBudget`` 持有本次 run 的总预算（默认前台 run 硬上限
120 秒）与阶段墙钟。每个阶段进入时检查剩余预算，超预算即停止后续
阶段（由编排层按既有终态策略降级）；公开搜索的阶段墙钟由
``EXTERNAL_TIMEOUT_SECONDS`` 集中管理（与既有客户端默认一致）。
技能可增加子阶段，但不能绕开总预算。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from bridges import model_call_budget as _model_call_budget
from bridges import public_search_budget as _public_search_budget

PUBLIC_SEARCH_STAGE_SECONDS = _public_search_budget.PUBLIC_SEARCH_STAGE_SECONDS
SEARCH_HANDOFF_RESERVE_SECONDS = _public_search_budget.SEARCH_HANDOFF_RESERVE_SECONDS
SEARCH_MIN_REQUEST_WINDOW_SECONDS = _public_search_budget.SEARCH_MIN_REQUEST_WINDOW_SECONDS
SEARCH_RETRY_BACKOFF_SECONDS = _public_search_budget.SEARCH_RETRY_BACKOFF_SECONDS

#: 结构化模型调用预算常量（单一来源：``bridges.model_call_budget``）。
MODEL_CALL_DEFAULT_TIMEOUT_SECONDS = _model_call_budget.MODEL_CALL_DEFAULT_TIMEOUT_SECONDS
MODEL_CALL_HANDOFF_RESERVE_SECONDS = _model_call_budget.MODEL_CALL_HANDOFF_RESERVE_SECONDS
MODEL_CALL_MIN_WINDOW_SECONDS = _model_call_budget.MODEL_CALL_MIN_WINDOW_SECONDS
MODEL_CALL_MIN_TIMEOUT_SECONDS = _model_call_budget.MODEL_CALL_MIN_TIMEOUT_SECONDS

#: 前台技能 run 硬上限（毫秒）：任一前台 run 必须在预算内进入终态。
TOTAL_BUDGET_MS = 120_000
#: 来源预算仍保留 arXiv 的独立默认值；包含 web 时 PUBLIC_SEARCH 使用上面的
#: 统一阶段预算。
EXTERNAL_TIMEOUT_SECONDS: dict[str, float] = {
    "web_search": PUBLIC_SEARCH_STAGE_SECONDS,
    # Issue 05：Windows 上 worker spawn + httpx 导入消耗数秒，HTTP 超时被
    # 压成剩余预算；10s→15s 后节流等待与真实请求都能落在阶段预算内。
    # Issue 04：预算内自动重试 + 节流重试等待需要余量，15s→20s；整轮
    # 120s 预算不变（ADR-0025 注记同步）。
    "arxiv_search": 20.0,
}
#: 来源名到预算常量的映射；只把本轮实际启动的来源纳入计算。
_SEARCH_SOURCE_TIMEOUT_KEYS: dict[str, str] = {
    "web": "web_search",
    "arxiv": "arxiv_search",
}
#: 阶段结果码常量。
RESULT_OK = "ok"
RESULT_SKIPPED = "skipped"
RESULT_TIMEOUT = "timeout"
RESULT_FAILED = "failed"


@dataclass(frozen=True, slots=True)
class PublicSearchDeadlines:
    """一次 PUBLIC_SEARCH 阶段的阶段与搜索提供方子预算。"""

    stage_deadline: float
    provider_deadline: float
    handoff_reserve_seconds: float


def public_search_deadlines(
    stage_started: float,
    *,
    run_deadline: float | None = None,
    scale: float = 1.0,
) -> PublicSearchDeadlines:
    """从同一预算源派生阶段截止和 provider 截止。

    ``scale`` 只用于确定性测试缩放整组预算；生产默认值始终是 8 秒、
    750ms 和 200ms。``run_deadline`` 用于让 run 级总预算优先收紧阶段。
    """

    factor = max(0.0, scale)
    # 保留 EXTERNAL_TIMEOUT_SECONDS 作为测试注入点；默认值仍唯一来自
    # PUBLIC_SEARCH_STAGE_SECONDS。
    stage_seconds = EXTERNAL_TIMEOUT_SECONDS["web_search"] * factor
    handoff_seconds = SEARCH_HANDOFF_RESERVE_SECONDS * factor
    stage_deadline = stage_started + stage_seconds
    if run_deadline is not None:
        stage_deadline = min(stage_deadline, run_deadline)
    provider_deadline = max(stage_started, stage_deadline - handoff_seconds)
    provider_deadline = min(provider_deadline, stage_deadline)
    return PublicSearchDeadlines(
        stage_deadline=stage_deadline,
        provider_deadline=provider_deadline,
        handoff_reserve_seconds=handoff_seconds,
    )


def source_aware_search_budget_seconds(
    active_sources: Iterable[str], *, remaining_ms: int | None = None
) -> float:
    """返回公开搜索的来源预算，并与剩余 run 预算取更严格者。

    单来源使用该来源的合同预算；多来源并行使用活跃来源中的最大预算，
    因而未启动的来源不会用更短预算提前截断本轮。空来源不应启动搜索，
    预算返回 0。未知来源不参与计算，避免把调用方的内部标签误当成公开
    搜索来源。
    """
    active = frozenset(
        source for source in active_sources if source in _SEARCH_SOURCE_TIMEOUT_KEYS
    )
    if not active:
        return 0.0
    source_budget = max(
        EXTERNAL_TIMEOUT_SECONDS[_SEARCH_SOURCE_TIMEOUT_KEYS[source]] for source in active
    )
    if remaining_ms is None:
        return source_budget
    return min(source_budget, max(0, remaining_ms) / 1000)


class RunStage(StrEnum):
    """统一 run 阶段（技能可增加子阶段，但不能绕开总预算）。"""

    QUEUED = "queued"
    LOCAL_RETRIEVAL = "local_retrieval"
    PUBLIC_SEARCH = "public_search"
    MODEL_GENERATION = "model_generation"
    QUALITY_CHECK = "quality_check"
    REPAIR = "repair"
    FINALIZING = "finalizing"


@dataclass(slots=True)
class StageMetric:
    """单阶段脱敏指标：仅 ID/阶段/毫秒/结果码/类别/计数。"""

    run_id: str
    stage: RunStage
    duration_ms: int
    result_code: str
    category: str | None = None
    count: int = 0
    #: 模型阶段的首个可见块耗时（毫秒，仅 model_generation 阶段记录）。
    first_token_ms: int | None = None

    def to_dict(self) -> dict[str, object]:
        """转脱敏字典（性能摘要与本地展示消费，不含任何正文内容）。"""
        return {
            "run_id": self.run_id,
            "stage": self.stage.value,
            "duration_ms": self.duration_ms,
            "result_code": self.result_code,
            "category": self.category,
            "count": self.count,
            "first_token_ms": self.first_token_ms,
        }


class RunBudgetLedgerBackend(Protocol):
    """持久化账本的内核写入协议（``RunBudgetLedgerRepository`` 结构化满足）。

    ``RunBudget`` 只经该协议写账本（工单 09：计数持久化并由内核唯一
    修改）；无账本构造（既有单测/直连路径）时全部钩子按「允许/无操作」
    处理，预算语义与 Issue 06 内存版完全一致。
    """

    def register_model_call(
        self,
        *,
        account_id: str,
        run_id: str,
        call_key: str,
        purpose: str | None = None,
        now: datetime,
    ) -> bool: ...

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
    ) -> None: ...

    def register_transient_retry(
        self,
        *,
        account_id: str,
        run_id: str,
        call_key: str,
        error_code: str | None = None,
        backoff_ms: int | None = None,
        now: datetime,
    ) -> bool: ...

    def register_external_call(
        self,
        *,
        account_id: str,
        run_id: str,
        call_key: str,
        purpose: str | None = None,
        now: datetime,
    ) -> bool: ...

    def record_external_call_result(
        self,
        *,
        account_id: str,
        run_id: str,
        call_key: str,
        outcome_code: str | None = None,
        now: datetime,
    ) -> None: ...

    def begin_adjustment(
        self,
        *,
        account_id: str,
        run_id: str,
        reason_code: str | None = None,
        now: datetime,
    ) -> bool: ...

    def end_adjustment(
        self,
        *,
        account_id: str,
        run_id: str,
        outcome_code: str | None = None,
        now: datetime,
        detail: dict[str, Any] | None = None,
    ) -> bool: ...

    def mark_exhausted(
        self, *, account_id: str, run_id: str, reason_code: str, now: datetime
    ) -> bool: ...

    def can_wait_until(self, account_id: str, run_id: str, until: datetime) -> bool: ...
