"""统一前台运行预算控制器。"""

from __future__ import annotations

import time
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from bridges import model_call_budget as _model_call_budget
from bridges.chat.budget_policy import (
    RESULT_OK,
    RESULT_SKIPPED,
    PublicSearchDeadlines,
    RunBudgetLedgerBackend,
    RunStage,
    StageMetric,
    public_search_deadlines,
    source_aware_search_budget_seconds,
)


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
        deadline_utc: datetime | None = None,
        reserve_ms: int = 0,
        ledger: RunBudgetLedgerBackend | None = None,
        account_id: str | None = None,
    ) -> None:
        self._run_id = run_id
        # 运行时读取模块常量（测试可 monkeypatch 注入小预算验证降级路径）
        if total_ms is None:
            from bridges.chat.budget import TOTAL_BUDGET_MS

            total_ms = TOTAL_BUDGET_MS
        self._total_ms = total_ms
        self._started = time.monotonic()
        # 工单 09：账本冻结的绝对截止优先于 total_ms 推导——截止时间在
        # 运行创建时冻结，租约恢复重载同一截止，恢复不产生新预算。
        if deadline_utc is not None:
            now_utc = datetime.now(UTC)
            frozen_remaining = max(0.0, (deadline_utc - now_utc).total_seconds())
            self._deadline = time.monotonic() + frozen_remaining
            # elapsed 按冻结截止与冻结总预算反推：恢复后已耗时间不归零。
            self._started = self._deadline - max(0, self._total_ms) / 1000
        else:
            self._deadline = self._started + max(0, self._total_ms) / 1000
        #: 核验/交付预留（毫秒）：交付前阶段的截止按「总预算 − 预留」收紧，
        #: 保证核验与交付空间不被检索/生成/修复吃掉（工单 09 初值 15/30 秒）。
        self._reserve_ms = max(0, reserve_ms)
        self._current: RunStage | None = None
        self._current_started: float | None = None
        self._metrics: list[StageMetric] = []
        #: 预算已耗尽（编排层应停止进入新阶段）。
        self._exhausted = False
        #: 持久化账本协作对象（无账本时全部钩子按允许/无操作处理）。
        self._ledger = ledger
        self._account_id = account_id

    @classmethod
    def from_ledger_snapshot(
        cls,
        run_id: str,
        *,
        total_budget_ms: int,
        deadline_utc: datetime,
        reserve_ms: int,
        ledger: RunBudgetLedgerBackend,
        account_id: str,
        active: bool = True,
    ) -> RunBudget:
        """从账本冻结快照构造：截止与总预算用冻结值，已耗计数留在账本。"""
        budget = cls(
            run_id,
            total_ms=total_budget_ms,
            deadline_utc=deadline_utc,
            reserve_ms=reserve_ms,
            ledger=ledger,
            account_id=account_id,
        )
        if not active:
            budget.mark_exhausted()
        return budget

    # ------------------------------------------------------------------
    # 预算查询
    # ------------------------------------------------------------------

    def remaining_ms(self) -> int:
        """剩余总预算（毫秒）；耗尽时为 0。"""
        return max(0, int((self._deadline - time.monotonic()) * 1000))

    def work_remaining_ms(self) -> int:
        """交付前阶段可用的剩余预算（总剩余 − 核验/交付预留；毫秒）。

        检索/搜索/生成/修复都只能消耗本口径；预留空间只留给核验与
        交付阶段（工单 09：「仍保留必要核验/交付空间」）。
        """
        return max(0, self.remaining_ms() - self._reserve_ms)

    def absolute_deadline(self) -> float:
        """本次 run 的绝对截止单调时刻。"""
        return self._deadline

    def work_deadline(self) -> float:
        """交付前阶段的截止（绝对截止 − 核验/交付预留；单调时刻）。"""
        return self._deadline - self._reserve_ms / 1000

    def exclude_wait(self, wait_ms: int) -> None:
        """将明确登记的人工等待从本地运行截止中扣除，不重置已用预算。"""
        if wait_ms > 0:
            self._deadline += wait_ms / 1000

    def public_search_deadlines(
        self, active_sources: Iterable[str], *, scale: float = 1.0
    ) -> PublicSearchDeadlines:
        """返回 PUBLIC_SEARCH 阶段截止及搜索提供方子截止。

        包含 Tavily 时，阶段预算统一为 8 秒；arXiv 单独运行时继续
        使用原有来源预算，以免把未参与的 web 约束施加到论文路径。
        公开搜索是交付前阶段：截止同时不得越过核验/交付预留线
        （工单 09）。
        """

        active = frozenset(active_sources)
        started = self._current_started or time.monotonic()
        if "web" in active:
            return public_search_deadlines(
                started,
                run_deadline=self.work_deadline(),
                scale=scale,
            )
        stage_deadline = min(
            self.work_deadline(),
            started
            + source_aware_search_budget_seconds(
                active, remaining_ms=self.work_remaining_ms()
            ),
        )
        return PublicSearchDeadlines(
            stage_deadline=stage_deadline,
            provider_deadline=stage_deadline,
            handoff_reserve_seconds=0.0,
        )

    def elapsed_ms(self) -> int:
        """本次 run 已耗用毫秒。"""
        return int((time.monotonic() - self._started) * 1000)

    def expired(self) -> bool:
        """总预算是否已耗尽（或主动标记耗尽）。"""
        return self._exhausted or self.remaining_ms() <= 0

    def model_call_timeout_ms(self) -> int:
        """按剩余预算截断的单次模型调用超时（毫秒；Issue 06 第七轮）。

        算术与常量集中在 ``bridges.model_call_budget``：默认 60 秒、
        交接预留 1 秒、正下限 50ms；单次调用不再可能吃光整轮预算。
        账本冻结了核验/交付预留时（工单 09），截断基线改为「交付前
        剩余」——单次调用同样吃不到预留空间。已主动标记耗尽
        （``mark_exhausted``）时同样只给正下限窗口。
        """
        if self.expired():
            return _model_call_budget.model_call_timeout_ms(0)
        return _model_call_budget.model_call_timeout_ms(self.work_remaining_ms())

    def can_retry_model_call(self, backoff_ms: int = 0) -> bool:
        """模型调用重试门（Issue 06 第七轮）：剩余预算放不下「退避 + 一次
        最小调用窗口 + 交接预留」时不重试，直接以真实错误终态收尾。

        网关重试循环与生涯/人味化修复门共用同一接缝（去重）；已标记
        耗尽（``mark_exhausted``）时同样不放行。重试窗口按交付前剩余
        计算——重试同样不能吃掉核验/交付预留。
        """
        if self.expired():
            return False
        if self.has_ledger:
            assert self._ledger is not None and self._account_id is not None
            if not self._ledger.can_wait_until(self._account_id, self._run_id, self._now()):
                return False
        return _model_call_budget.model_call_can_retry(
            self.work_remaining_ms(), backoff_ms=backoff_ms
        )

    def can_start_model_call(self) -> bool:
        """供应商入口再次检查停止与截止；首次调用不套用冷却重试预留。"""
        if self.expired() or self.work_remaining_ms() <= 0:
            return False
        if self.has_ledger:
            assert self._ledger is not None and self._account_id is not None
            return self._ledger.can_wait_until(self._account_id, self._run_id, self._now())
        return True

    # ------------------------------------------------------------------
    # 持久化账本协作（工单 09；无账本时按允许/无操作处理）
    # ------------------------------------------------------------------

    @property
    def has_ledger(self) -> bool:
        """是否挂接持久化账本（测试据此区分内存版与账本版）。"""
        return self._ledger is not None and self._account_id is not None

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def register_model_call(self, call_key: str, *, purpose: str | None = None) -> bool:
        """登记一次模型调用（超额调用被代码拒绝；无账本时允许）。"""
        if not self.has_ledger:
            return True
        assert self._ledger is not None and self._account_id is not None
        return self._ledger.register_model_call(
            account_id=self._account_id,
            run_id=self._run_id,
            call_key=call_key,
            purpose=purpose,
            now=self._now(),
        )

    def record_model_call_result(
        self,
        call_key: str,
        *,
        attempt: int = 1,
        outcome_code: str | None = None,
        duration_ms: int | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        """补记一次模型调用的脱敏结果（按调用目的计量；无账本时无操作）。"""
        if not self.has_ledger:
            return
        assert self._ledger is not None and self._account_id is not None
        self._ledger.record_model_call_result(
            account_id=self._account_id,
            run_id=self._run_id,
            call_key=call_key,
            attempt=attempt,
            outcome_code=outcome_code,
            duration_ms=duration_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            now=self._now(),
        )

    def register_transient_retry(
        self,
        call_key: str,
        *,
        error_code: str | None = None,
        backoff_ms: int | None = None,
    ) -> bool:
        """登记一次临时传输失败重试（每登记调用最多 1 次；无账本时允许）。

        错误类型（仅 RateLimit/Transient 可重试）、冷却与剩余额度检查由
        调用方完成；本方法持久化重试计数并执行「最多 1 次」守卫。
        """
        if not self.has_ledger:
            return True
        assert self._ledger is not None and self._account_id is not None
        return self._ledger.register_transient_retry(
            account_id=self._account_id,
            run_id=self._run_id,
            call_key=call_key,
            error_code=error_code,
            backoff_ms=backoff_ms,
            now=self._now(),
        )

    def register_external_call(self, call_key: str, *, purpose: str | None = None) -> bool:
        """登记一次外部调用分支（并行超过上限时拒绝；无账本时允许）。

        并行与串行消耗同一账本：并发占位 ``external_calls_active`` 由
        ``register_external_call``/``release_external_call`` 共同维护。
        """
        if not self.has_ledger:
            return True
        assert self._ledger is not None and self._account_id is not None
        return self._ledger.register_external_call(
            account_id=self._account_id,
            run_id=self._run_id,
            call_key=call_key,
            purpose=purpose,
            now=self._now(),
        )

    def release_external_call(self, call_key: str, *, outcome_code: str | None = None) -> None:
        """释放一次外部调用的并发占位（幂等；无账本时无操作）。"""
        if not self.has_ledger:
            return
        assert self._ledger is not None and self._account_id is not None
        self._ledger.record_external_call_result(
            account_id=self._account_id,
            run_id=self._run_id,
            call_key=call_key,
            outcome_code=outcome_code,
            now=self._now(),
        )

    def begin_adjustment(self, *, reason_code: str | None = None) -> bool:
        """开始一轮自动补证/调整（整次运行最多一轮；无账本时允许）。

        结构修复与内容修复共享该轮；第二次调用被代码拒绝，模型不能追加。
        """
        if not self.has_ledger:
            return True
        assert self._ledger is not None and self._account_id is not None
        return self._ledger.begin_adjustment(
            account_id=self._account_id,
            run_id=self._run_id,
            reason_code=reason_code,
            now=self._now(),
        )

    def end_adjustment(
        self, *, outcome_code: str | None = None, detail: dict[str, Any] | None = None
    ) -> bool:
        """补记本轮自动补证/调整的结果（无账本时按已记处理）。"""
        if not self.has_ledger:
            return True
        assert self._ledger is not None and self._account_id is not None
        return self._ledger.end_adjustment(
            account_id=self._account_id,
            run_id=self._run_id,
            outcome_code=outcome_code,
            detail=detail,
            now=self._now(),
        )

    def can_wait_until(self, until_utc: datetime) -> bool:
        """限流/冷却等待是否放得下：超过冻结剩余预算即拒绝（工单 09）。

        无账本时按内存截止判断。
        """
        if self.has_ledger:
            assert self._ledger is not None and self._account_id is not None
            return self._ledger.can_wait_until(
                self._account_id, self._run_id, until_utc
            )
        now_utc = datetime.now(UTC)
        remaining_seconds = (until_utc - now_utc).total_seconds()
        return remaining_seconds * 1000 <= self.remaining_ms()

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

    def mark_exhausted(self, *, reason_code: str | None = None) -> None:
        """主动标记预算耗尽（编排层判定不可继续时调用）。

        挂接账本时把耗尽原因持久化（明确终止条件；工单 09）——未指明
        原因时按 ``budget_exceeded`` 落库，耗尽终态必须留痕，绝不止内存
        标记。
        """
        self._exhausted = True
        if self.has_ledger:
            assert self._ledger is not None and self._account_id is not None
            self._ledger.mark_exhausted(
                account_id=self._account_id,
                run_id=self._run_id,
                reason_code=reason_code or "budget_exceeded",
                now=self._now(),
            )

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
