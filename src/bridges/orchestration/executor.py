"""复合计划的调度器：依赖排序、并行分支、共享预算与一轮受控调整（工单 37）。

调度语义（与 ``docs/workflow/orchestration.md`` 第 7/8/10 节一致）：

- 依赖就绪的独立步骤在同一波次并行执行，波次宽度受运行预算的
  ``external_parallel_max`` 约束；所有分支共用 09 的同一本预算账本；
- 每步先做**无外呼的参数解析**并计算输入指纹：指纹、配方/能力版本与
  权限范围兼容时复用已有步骤结果，任务条件变化只使输入依赖变化的步骤
  及其下游失效，无关节点不重跑；
- 必要步骤失败阻塞依赖它的结论并保留其它分支的有效部分；可选步骤失败
  只记缺口；
- 可重试失败触发代码侧的一轮受控调整（与账本 ``adjustment_rounds_max``
  同一判定），第二轮一律拒绝；
- 停止在波次边界生效；提交前守卫拒绝（旧租约/任务版本变化/终态）时
  整个复合结果以 ``rejected`` 收敛，不写交付。
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING, Any, Protocol

from bridges.kernel.guard import CommitDecision
from bridges.orchestration.contracts import (
    CompositeOutcome,
    CompositePlan,
    CompositeStatus,
    CompositeStep,
    StepFailure,
    StepResult,
    StepState,
)
from bridges.orchestration.planner import CompositePlanner

if TYPE_CHECKING:
    from bridges.chat.run_budget_ledger import (
        RunBudgetLedgerRepository,
        RunBudgetSnapshot,
    )


class CompositeBudget:
    """复合调度的共享预算端口（包装 09 账本，所有分支共用同一本账）。"""

    def __init__(
        self,
        ledger: RunBudgetLedgerRepository,
        *,
        account_id: str,
        run_id: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._ledger = ledger
        self._account_id = account_id
        self._run_id = run_id
        self._clock = clock or (lambda: datetime.now(UTC))

    def snapshot(self) -> RunBudgetSnapshot | None:
        return self._ledger.load(self._account_id, self._run_id)

    def remaining_work_ms(self) -> int | None:
        snapshot = self.snapshot()
        if snapshot is None or not snapshot.active:
            return None
        reserve = snapshot.plan.verify_deliver_reserve_ms
        remaining = int(
            (snapshot.plan.deadline_at - self._clock()).total_seconds() * 1000
        ) - reserve
        return max(0, remaining)

    def begin_adjustment(self, reason_code: str) -> bool:
        return self._ledger.begin_adjustment(
            account_id=self._account_id,
            run_id=self._run_id,
            reason_code=reason_code,
            now=self._clock(),
        )

    def end_adjustment(self, outcome_code: str, detail: dict[str, Any] | None = None) -> None:
        self._ledger.end_adjustment(
            account_id=self._account_id,
            run_id=self._run_id,
            outcome_code=outcome_code,
            detail=detail,
            now=self._clock(),
        )

    def external_parallel_max(self) -> int:
        snapshot = self.snapshot()
        if snapshot is None:
            return 2
        return snapshot.plan.external_parallel_max

    def adjustment_rounds_max(self) -> int:
        snapshot = self.snapshot()
        if snapshot is None:
            return 1
        return snapshot.plan.adjustment_rounds_max


@dataclass(frozen=True, slots=True)
class StepRunContext:
    """一个步骤的最小运行上下文（引用与上游结果，不含全量历史）。"""

    account_id: str
    conversation_id: str
    run_id: str
    user_message_id: str
    assistant_message_id: str
    goal: str
    run_context: Any = None
    upstream: Mapping[str, StepResult] = field(default_factory=dict)
    attempt: int = 0
    stop_event: threading.Event | None = None
    emit_node: Callable[[str, str, int | None], None] | None = None


class StepRunner(Protocol):
    """一个已登记模块的无终态执行体（生产实现委托模块服务的 defer 模式）。"""

    def resolve(self, step: CompositeStep, context: StepRunContext) -> Mapping[str, Any]:
        """无外呼地解析该步骤的绑定参数（结果只用于指纹与传递）。"""

    def run(
        self,
        step: CompositeStep,
        context: StepRunContext,
        resolved: Mapping[str, Any],
        remaining_budget_ms: int | None,
    ) -> StepResult:
        """执行步骤并返回结果；不写助手消息终态。"""


class FunctionStepRunner:
    """把两个可调用对象包装成步骤执行体（测试与轻量模块使用）。"""

    def __init__(
        self,
        *,
        resolve: Callable[[CompositeStep, StepRunContext], Mapping[str, Any]],
        run: Callable[
            [CompositeStep, StepRunContext, Mapping[str, Any], int | None], StepResult
        ],
    ) -> None:
        self._resolve = resolve
        self._run = run

    def resolve(self, step: CompositeStep, context: StepRunContext) -> Mapping[str, Any]:
        return self._resolve(step, context)

    def run(
        self,
        step: CompositeStep,
        context: StepRunContext,
        resolved: Mapping[str, Any],
        remaining_budget_ms: int | None,
    ) -> StepResult:
        return self._run(step, context, resolved, remaining_budget_ms)


#: 提交前守卫（复用内核 ``RunCommitGuard.verify``）；返回拒绝时整体收敛。
CommitCheck = Callable[[], CommitDecision]


class CompositeExecutor:
    """受控的复合计划执行器。"""

    def __init__(
        self,
        *,
        planner: CompositePlanner | None = None,
        budget: CompositeBudget | None = None,
        commit_check: CommitCheck | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._planner = planner or CompositePlanner()
        self._budget = budget
        self._commit_check = commit_check
        self._clock = clock or (lambda: datetime.now(UTC))

    def execute(
        self,
        *,
        plan: CompositePlan,
        runners: Mapping[str, StepRunner],
        context: StepRunContext,
        prior_results: Mapping[str, StepResult] | None = None,
        changed_conditions: Sequence[str] = (),
    ) -> CompositeOutcome:
        """执行计划；返回步骤结果与整体状态（不写助手消息）。"""
        validation = self._planner.validate(
            plan,
            external_parallel_max=(
                self._budget.external_parallel_max() if self._budget else None
            ),
            adjustment_rounds_max=(
                self._budget.adjustment_rounds_max() if self._budget else None
            ),
        )
        if not validation.ok:
            return self._terminal(
                CompositeStatus.REJECTED,
                plan=plan,
                steps=[],
                rejection_code=";".join(validation.codes()),
            )
        prior = dict(prior_results or {})
        invalidated = self._invalidated_steps(plan, changed_conditions)
        results: dict[str, StepResult] = {}
        executed: set[str] = set()
        self._run_waves(
            plan=plan,
            runners=runners,
            context=context,
            prior=prior,
            invalidated=invalidated,
            results=results,
            executed=executed,
        )
        status = self._status_for(plan, results)
        adjustments = 0
        if status in {CompositeStatus.PARTIAL, CompositeStatus.FAILED} and self._repairable(
            plan, results
        ):
            adjusted_plan, violation = self._planner.adjusted(
                plan, reason="retryable_failure", now=self._clock()
            )
            if adjusted_plan is not None and self._allow_adjustment():
                adjustments = 1
                retry_ids = {
                    step_id
                    for step_id, result in results.items()
                    if result.state
                    in {StepState.FAILED, StepState.BLOCKED, StepState.SKIPPED}
                }
                for step_id in retry_ids:
                    results.pop(step_id, None)
                retry_context = StepRunContext(
                    account_id=context.account_id,
                    conversation_id=context.conversation_id,
                    run_id=context.run_id,
                    user_message_id=context.user_message_id,
                    assistant_message_id=context.assistant_message_id,
                    goal=context.goal,
                    run_context=context.run_context,
                    attempt=1,
                    stop_event=context.stop_event,
                    emit_node=context.emit_node,
                )
                self._run_waves(
                    plan=adjusted_plan,
                    runners=runners,
                    context=retry_context,
                    prior={},
                    invalidated=retry_ids,
                    results=results,
                    executed=executed,
                )
                plan = adjusted_plan
                status = self._status_for(plan, results)
                if violation is not None:
                    status = CompositeStatus.PARTIAL
            elif violation is not None:
                results.setdefault(
                    violation.step_id or "plan",
                    StepResult(
                        step_id=violation.step_id or "plan",
                        module_id="",
                        state=StepState.BLOCKED,
                        blocked_reason=violation.message,
                    ),
                )
        if self._commit_check is not None:
            decision = self._commit_check()
            if not decision.ok:
                return self._terminal(
                    CompositeStatus.REJECTED,
                    plan=plan,
                    steps=list(results.values()),
                    adjustments_used=adjustments,
                    rejection_code=decision.code,
                )
        blocked = [
            f"{result.module_id or result.step_id}：{result.blocked_reason}"
            for result in results.values()
            if result.state in {StepState.FAILED, StepState.BLOCKED}
            and result.blocked_reason
        ]
        delivered = [
            result.step_id
            for result in results.values()
            if result.state is StepState.COMPLETED
        ]
        return CompositeOutcome(
            status=status,
            plan=plan,
            steps=list(results.values()),
            blocked_conclusions=blocked,
            delivered_steps=delivered,
            adjustments_used=adjustments,
            created_at=self._clock(),
        )

    # ------------------------------------------------------------------
    # 波次调度
    # ------------------------------------------------------------------

    def _run_waves(
        self,
        *,
        plan: CompositePlan,
        runners: Mapping[str, StepRunner],
        context: StepRunContext,
        prior: Mapping[str, StepResult],
        invalidated: set[str],
        results: dict[str, StepResult],
        executed: set[str],
    ) -> None:
        stop_event = context.stop_event
        while True:
            if stop_event is not None and stop_event.is_set():
                for step in plan.steps:
                    if step.step_id not in results:
                        results[step.step_id] = StepResult(
                            step_id=step.step_id,
                            module_id=step.module_id,
                            state=StepState.SKIPPED,
                            blocked_reason="用户已停止本轮生成。",
                        )
                return
            ready = [
                step
                for step in plan.steps
                if step.step_id not in results
                and all(
                    dependency in results
                    and results[dependency].state is StepState.COMPLETED
                    for dependency in step.depends_on
                )
            ]
            # 上游未完成（失败/阻塞/等待）的步骤：阻塞或跳过，不再执行。
            for step in plan.steps:
                if step.step_id in results:
                    continue
                failed_upstream = [
                    dependency
                    for dependency in step.depends_on
                    if dependency in results
                    and results[dependency].state
                    not in {StepState.COMPLETED, StepState.PENDING}
                ]
                if failed_upstream:
                    blocked_by = "、".join(failed_upstream)
                    results[step.step_id] = StepResult(
                        step_id=step.step_id,
                        module_id=step.module_id,
                        state=(
                            StepState.BLOCKED
                            if step.required
                            else StepState.SKIPPED
                        ),
                        blocked_reason=(
                            f"必要步骤 {blocked_by} 未完成，依赖它的结论被阻塞。"
                            if step.required
                            else f"上游 {blocked_by} 未完成，可选项跳过。"
                        ),
                    )
            ready = [
                step
                for step in plan.steps
                if step.step_id not in results
                and all(
                    dependency in results
                    and results[dependency].state is StepState.COMPLETED
                    for dependency in step.depends_on
                )
            ]
            if not ready:
                break
            prepared: list[
                tuple[
                    CompositeStep,
                    StepRunner,
                    Mapping[str, Any],
                    str,
                    StepRunContext,
                ]
            ] = []
            for step in ready:
                runner = runners.get(step.module_id)
                if runner is None:
                    results[step.step_id] = StepResult(
                        step_id=step.step_id,
                        module_id=step.module_id,
                        state=StepState.FAILED,
                        blocked_reason=f"模块 {step.module_id} 没有可用的复合执行体。",
                        failure=StepFailure(
                            code="step_runner_missing",
                            message=f"模块 {step.module_id} 没有可用的复合执行体。",
                        ),
                    )
                    continue
                step_context = StepRunContext(
                    account_id=context.account_id,
                    conversation_id=context.conversation_id,
                    run_id=context.run_id,
                    user_message_id=context.user_message_id,
                    assistant_message_id=context.assistant_message_id,
                    goal=context.goal,
                    run_context=context.run_context,
                    upstream={
                        dependency: results[dependency]
                        for dependency in step.depends_on
                    },
                    attempt=context.attempt,
                    stop_event=stop_event,
                    emit_node=context.emit_node,
                )
                resolved = runner.resolve(step, step_context)
                fingerprint = _fingerprint(step, resolved)
                previous = prior.get(step.step_id)
                if (
                    previous is not None
                    and previous.state is StepState.COMPLETED
                    and step.step_id not in invalidated
                    and previous.input_fingerprint == fingerprint
                ):
                    results[step.step_id] = previous.model_copy(
                        update={"reused": True}
                    )
                    continue
                prepared.append((step, runner, resolved, fingerprint, step_context))
            if not prepared:
                continue
            limit = (
                min(self._budget.external_parallel_max(), len(prepared))
                if self._budget
                else len(prepared)
            )
            limit = max(1, limit)
            if len(prepared) == 1 or limit <= 1:
                for step, runner, resolved, fingerprint, step_context in prepared:
                    results[step.step_id] = self._execute_step(
                        step, runner, step_context, resolved, fingerprint
                    )
            else:
                outcomes: dict[str, StepResult] = {}
                with ThreadPoolExecutor(
                    max_workers=min(limit, len(prepared)),
                    thread_name_prefix="bridges-composite",
                ) as pool:
                    futures = {}
                    for step, runner, resolved, fingerprint, step_context in prepared:
                        futures[
                            pool.submit(
                                self._execute_step,
                                step,
                                runner,
                                step_context,
                                resolved,
                                fingerprint,
                            )
                        ] = step.step_id
                    for future in as_completed(futures):
                        outcomes[futures[future]] = future.result()
                for step in ready:
                    if step.step_id in outcomes:
                        results[step.step_id] = outcomes[step.step_id]
            if any(
                result.state is StepState.NEEDS_INPUT for result in results.values()
            ):
                for step in plan.steps:
                    if step.step_id not in results:
                        results[step.step_id] = StepResult(
                            step_id=step.step_id,
                            module_id=step.module_id,
                            state=StepState.SKIPPED,
                            blocked_reason="有步骤等待用户输入，本轮停止派发。",
                        )
                return

    def _execute_step(
        self,
        step: CompositeStep,
        runner: StepRunner,
        context: StepRunContext,
        resolved: Mapping[str, Any],
        fingerprint: str,
    ) -> StepResult:
        started = time.monotonic()
        try:
            result = runner.run(
                step,
                context,
                resolved,
                self._budget.remaining_work_ms() if self._budget else None,
            )
        except Exception as error:  # noqa: BLE001 - 模块失败转为结构化结果
            result = StepResult(
                step_id=step.step_id,
                module_id=step.module_id,
                state=StepState.FAILED,
                blocked_reason=str(error),
                failure=StepFailure(
                    code="module_error", message=str(error), retryable=True
                ),
            )
        duration = max(1, int((time.monotonic() - started) * 1000))
        return result.model_copy(
            update={
                "input_fingerprint": fingerprint,
                "resolved_param_names": sorted(resolved),
                "duration_ms": result.duration_ms or duration,
            }
        )

    # ------------------------------------------------------------------
    # 失效与状态
    # ------------------------------------------------------------------

    def _invalidated_steps(
        self, plan: CompositePlan, changed_conditions: Sequence[str]
    ) -> set[str]:
        """条件键命中的步骤直接失效；下游按**绑定参数指纹**自然重算。

        依赖步骤不因上游被重算而一律重跑：下游解析后指纹未变的仍复用
        （例如「换杭州」只使城市相关的岗位/建议步骤失效，主题未变的公共
        资料保持有效）。指纹变化的下游在下一次解析时因不一致而重跑。
        """
        changed = set(changed_conditions)
        if not changed:
            return set()
        return {
            step.step_id
            for step in plan.steps
            if changed & set(step.condition_keys)
        }

    def _repairable(
        self, plan: CompositePlan, results: Mapping[str, StepResult]
    ) -> bool:
        return any(
            result.state is StepState.FAILED
            and result.failure is not None
            and result.failure.retryable
            for result in results.values()
        )

    def _allow_adjustment(self) -> bool:
        if self._budget is None:
            return True
        return self._budget.begin_adjustment("composite_adjustment")

    def _status_for(
        self, plan: CompositePlan, results: Mapping[str, StepResult]
    ) -> CompositeStatus:
        if not results:
            return CompositeStatus.FAILED
        if any(result.state is StepState.NEEDS_INPUT for result in results.values()):
            return CompositeStatus.NEEDS_INPUT
        if all(result.state is StepState.SKIPPED for result in results.values()):
            return CompositeStatus.STOPPED
        completed = [
            result for result in results.values() if result.state is StepState.COMPLETED
        ]
        if len(completed) == len(plan.steps):
            return CompositeStatus.COMPLETED
        if not completed:
            return CompositeStatus.FAILED
        return CompositeStatus.PARTIAL

    def _terminal(
        self,
        status: CompositeStatus,
        *,
        plan: CompositePlan,
        steps: Sequence[StepResult],
        adjustments_used: int = 0,
        rejection_code: str | None = None,
    ) -> CompositeOutcome:
        return CompositeOutcome(
            status=status,
            plan=plan,
            steps=list(steps),
            adjustments_used=adjustments_used,
            rejection_code=rejection_code,
            created_at=self._clock(),
        )


def _fingerprint(step: CompositeStep, resolved: Mapping[str, Any]) -> str:
    material = json.dumps(
        {
            "step": step.step_id,
            "recipe": f"{step.recipe_id}@{step.recipe_version}",
            "capability": f"{step.capability}@{step.capability_version}",
            "params": _canonical(resolved),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(material.encode("utf-8")).hexdigest()


def _canonical(value: Any) -> Any:
    """参数值的稳定表示：只参与指纹，不保存原文。"""
    if isinstance(value, Mapping):
        return {str(key): _canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


__all__ = [
    "CommitCheck",
    "CompositeBudget",
    "CompositeExecutor",
    "FunctionStepRunner",
    "StepRunContext",
    "StepRunner",
]
