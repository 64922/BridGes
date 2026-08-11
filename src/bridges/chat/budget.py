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

import time
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

#: 前台技能 run 硬上限（毫秒）：任一前台 run 必须在预算内进入终态。
TOTAL_BUDGET_MS = 120_000
#: 公开搜索阶段墙钟（秒）：与既有客户端默认超时一致（DuckDuckGo 8s、
#: arXiv worker 往返 10s），作为并行搜索等待上限的单一事实源；其余
#: 外部调用（模型流式 60s 等）由各自客户端超时承担，不在前台并行
#: 等待路径内。
EXTERNAL_TIMEOUT_SECONDS: dict[str, float] = {
    "web_search": 8.0,
    "arxiv_search": 10.0,
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


class RunBudget:
    """一次前台 run 的预算控制器：总预算 + 阶段墙钟 + 重试门。

    编排层在阶段进入/结束时调用 ``enter``/``exit`` 并自行发射阶段事件；
    ``enter`` 返回 ``False`` 表示预算耗尽（编排层应跳过该阶段并降级）。
    """

    def __init__(
        self,
        run_id: str,
        *,
        total_ms: int | None = None,
    ) -> None:
        self._run_id = run_id
        # 运行时读取模块常量（测试可 monkeypatch 注入小预算验证降级路径）
        self._total_ms = total_ms if total_ms is not None else TOTAL_BUDGET_MS
        self._started = time.monotonic()
        self._deadline = self._started + max(0, self._total_ms) / 1000
        self._current: RunStage | None = None
        self._current_started: float | None = None
        self._metrics: list[StageMetric] = []
        #: 预算已耗尽（编排层应停止进入新阶段）。
        self._exhausted = False

    # ------------------------------------------------------------------
    # 预算查询
    # ------------------------------------------------------------------

    def remaining_ms(self) -> int:
        """剩余总预算（毫秒）；耗尽时为 0。"""
        return max(0, int((self._deadline - time.monotonic()) * 1000))

    def absolute_deadline(self) -> float:
        """本次 run 的绝对截止单调时刻。"""
        return self._deadline

    def search_deadline(self, active_sources: Iterable[str]) -> float:
        """为活跃公开来源派生与 run 预算共享的绝对截止时刻。"""
        source_budget = source_aware_search_budget_seconds(
            active_sources, remaining_ms=self.remaining_ms()
        )
        return min(self._deadline, time.monotonic() + source_budget)

    def elapsed_ms(self) -> int:
        """本次 run 已耗用毫秒。"""
        return int((time.monotonic() - self._started) * 1000)

    def expired(self) -> bool:
        """总预算是否已耗尽（或主动标记耗尽）。"""
        return self._exhausted or self.remaining_ms() <= 0

    def can_retry(self, estimated_ms: int = 5_000) -> bool:
        """重试门：仅当错误可重试且剩余预算足够估算成本时放行。"""
        return not self.expired() and self.remaining_ms() >= estimated_ms

    # ------------------------------------------------------------------
    # 阶段时钟
    # ------------------------------------------------------------------

    def enter(self, stage: RunStage) -> bool:
        """进入阶段：结束上一阶段并开始计时；预算耗尽时返回 False。

        返回 False 表示编排层应跳过该阶段（超预算降级）；此时阶段不
        计时、不发事件。
        """
        if self._current is not None:
            self._close_current(RESULT_OK)
        if self.expired():
            self._exhausted = True
            return False
        self._current = stage
        self._current_started = time.monotonic()
        return True

    def _close_current(self, result: str) -> None:
        """结束进行中阶段并记录脱敏指标（enter 衔接调用）。"""
        assert self._current is not None and self._current_started is not None
        duration_ms = max(0, int((time.monotonic() - self._current_started) * 1000))
        stage = self._current
        self._metrics.append(
            StageMetric(
                run_id=self._run_id,
                stage=stage,
                duration_ms=duration_ms,
                result_code=result,
            )
        )
        self._current = None
        self._current_started = None

    def exit(
        self,
        stage: RunStage,
        *,
        result: str = RESULT_OK,
        category: str | None = None,
        count: int = 0,
        first_token_ms: int | None = None,
    ) -> None:
        """结束阶段并记录脱敏指标。

        幂等语义：阶段已由 ``enter`` 衔接关闭（已在指标中）时直接返回，
        不重复记录；从未进入的阶段（预算耗尽跳过）记 skipped。
        """
        if self._current == stage and self._current_started is not None:
            duration_ms = max(0, int((time.monotonic() - self._current_started) * 1000))
            self._current = None
            self._current_started = None
        elif any(metric.stage == stage for metric in self._metrics):
            # 已关闭（enter 衔接关闭或已 exit）：幂等返回，不双计
            return
        else:
            duration_ms = 0
            if result == RESULT_OK:
                result = RESULT_SKIPPED
        self._metrics.append(
            StageMetric(
                run_id=self._run_id,
                stage=stage,
                duration_ms=duration_ms,
                result_code=result,
                category=category,
                count=count,
                first_token_ms=first_token_ms,
            )
        )

    def mark_exhausted(self) -> None:
        """主动标记预算耗尽（编排层判定不可继续时调用）。"""
        self._exhausted = True

    def metrics(self) -> list[StageMetric]:
        """已结束阶段的脱敏指标（进行中阶段不计）。"""
        return list(self._metrics)

    def first_token_ms(self) -> int | None:
        """本次 run 的模型首 token 耗时（毫秒，无模型阶段时 None）。"""
        values = [
            metric.first_token_ms
            for metric in self._metrics
            if metric.first_token_ms is not None
        ]
        return min(values) if values else None
