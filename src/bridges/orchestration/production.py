"""生产复合执行体与编排服务（工单 37 聊天层接缝）。

- :class:`ModuleServiceStepRunner` 把一个模块服务的 ``defer_finalization``
  交付适配成复合步骤结果：模块仍在内核里提交真实产物，但不自行结束助手
  消息，最终由聊天父图在守卫事务内一次性提交；
- :class:`CompositeOrchestrationService` 执行计划、按目标组织综合并运行
  最终质量门；
- 终态提交、等待写入与迟到拒绝由聊天父图负责（与 GitHub 延迟交付同款）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from bridges.contracts.chat import ChatMessageStatus
from bridges.contracts.modules import ModuleDelivery
from bridges.orchestration.contracts import (
    CompositeOutcome,
    CompositePlan,
    CompositeStatus,
    CompositeStep,
    StepFailure,
    StepResult,
    StepState,
    SynthesisDraft,
    SynthesisGateResult,
)
from bridges.orchestration.executor import (
    CommitCheck,
    CompositeBudget,
    CompositeExecutor,
    StepRunContext,
    StepRunner,
)
from bridges.orchestration.planner import CompositePlanner
from bridges.orchestration.synthesis import FinalGate, Synthesizer

ResolveCall = Callable[[CompositeStep, StepRunContext], Mapping[str, Any]]
RunCall = Callable[
    [CompositeStep, StepRunContext, Mapping[str, Any]], Any
]


@dataclass(frozen=True, slots=True)
class CompositeTurnResult:
    """一次复合运行的综合结果（未写助手消息终态）。"""

    outcome: CompositeOutcome
    draft: SynthesisDraft
    gate: SynthesisGateResult


class ModuleServiceStepRunner:
    """一个模块服务的无终态步骤执行体。"""

    def __init__(self, *, resolve: ResolveCall, run: RunCall) -> None:
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
        del remaining_budget_ms
        outcome = self._run(step, context, resolved)
        delivery = getattr(outcome, "delivery", None)
        if step.module_id == "github":
            # GitHub 保留既有领域交付合同；只在组合边界转换，单模块路径不变。
            from bridges.github.service import GithubDelivery

            if isinstance(delivery, GithubDelivery):
                projection = delivery.projection
                delivery = ModuleDelivery(
                    module_id="github",
                    status=projection.status.value,
                    projection_field="github_projects",
                    projection=projection.model_dump(mode="json"),
                    content=delivery.content,
                    message_status=delivery.message_status.value,
                    error_node=delivery.error_node,
                    error_code=projection.error_code,
                    error_message=projection.error_message,
                    retryable=projection.retryable,
                    artifact_refs={
                        node: ref for node, ref in (
                            ("github.verify", delivery.verification_artifact_id),
                            ("github.present", delivery.present_artifact_id),
                        ) if ref is not None
                    },
                    lock=delivery.lock,
                    wait_reason=getattr(outcome, "wait_reason", None),
                )
        if not isinstance(delivery, ModuleDelivery):
            return StepResult(
                step_id=step.step_id,
                module_id=step.module_id,
                state=StepState.FAILED,
                blocked_reason="模块没有返回可核验的延迟交付。",
                failure=StepFailure(
                    code="module_delivery_missing",
                    message="模块没有返回可核验的延迟交付。",
                    retryable=True,
                ),
            )
        return step_result_from_delivery(step, delivery)


def step_result_from_delivery(step: CompositeStep, delivery: ModuleDelivery) -> StepResult:
    """模块交付 → 复合步骤结果：调度状态与可信状态分开。"""
    if delivery.message_status == ChatMessageStatus.ERROR.value:
        state = StepState.FAILED
    elif delivery.message_status == ChatMessageStatus.STOPPED.value:
        state = StepState.SKIPPED
    elif delivery.status == "clarification" or delivery.wait_reason:
        state = StepState.NEEDS_INPUT
    elif delivery.message_status == ChatMessageStatus.DONE.value:
        state = StepState.COMPLETED
    else:
        state = StepState.FAILED
    failure = None
    if state is StepState.FAILED:
        failure = StepFailure(
            code=delivery.error_code or "module_failed",
            message=delivery.error_message or "模块执行失败。",
            retryable=delivery.retryable,
        )
    return StepResult(
        step_id=step.step_id,
        module_id=step.module_id,
        state=state,
        trust_state="qualified" if state is StepState.COMPLETED else "draft",
        delivery=delivery,
        artifact_refs=dict(delivery.artifact_refs),
        evidence_refs=list(dict.fromkeys(delivery.artifact_refs.values())),
        read_scope="交付级",
        summary=delivery.content or "",
        blocked_reason=(
            failure.message
            if failure is not None
            else ("等待用户补充信息。" if state is StepState.NEEDS_INPUT else None)
        ),
        failure=failure,
    )


class CompositeOrchestrationService:
    """受控复合编排：执行 → 综合 → 最终门（不写助手消息）。"""

    def __init__(
        self,
        *,
        planner: CompositePlanner | None = None,
        synthesizer: Synthesizer | None = None,
        gate: FinalGate | None = None,
    ) -> None:
        self._planner = planner or CompositePlanner()
        self._synthesizer = synthesizer or Synthesizer()
        self._gate = gate or FinalGate()

    def run(
        self,
        *,
        plan: CompositePlan,
        runners: Mapping[str, StepRunner],
        context: StepRunContext,
        budget: CompositeBudget | None = None,
        commit_check: CommitCheck | None = None,
        prior_results: Mapping[str, StepResult] | None = None,
        changed_conditions: Sequence[str] = (),
    ) -> CompositeTurnResult:
        executor = CompositeExecutor(
            planner=self._planner, budget=budget, commit_check=commit_check
        )
        outcome = executor.execute(
            plan=plan,
            runners=runners,
            context=context,
            prior_results=prior_results,
            changed_conditions=changed_conditions,
        )
        draft = self._synthesizer.build(outcome)
        gate = self._gate.check(draft, outcome.steps)
        return CompositeTurnResult(outcome=outcome, draft=draft, gate=gate)


def render_final_content(draft: SynthesisDraft) -> str:
    """按一个目标组织正文：结论 → 各分支详情 → 限定与缺口。"""
    parts: list[str] = [draft.summary]
    parts.extend(section.body for section in draft.sections if section.body)
    if draft.limitations:
        parts.append("说明：" + "；".join(dict.fromkeys(draft.limitations)))
    return "\n\n".join(part for part in parts if part)


def projection_updates(outcome: CompositeOutcome) -> dict[str, dict[str, Any]]:
    """汇总各分支投影（一次 finalize 提交；失败分支保留错误投影）。"""
    updates: dict[str, dict[str, Any]] = {}
    for step in outcome.steps:
        delivery = step.delivery
        if delivery is None or not delivery.projection_field:
            continue
        if step.state in {StepState.COMPLETED, StepState.NEEDS_INPUT, StepState.FAILED}:
            updates[delivery.projection_field] = dict(delivery.projection)
    return updates


def message_status_for(outcome: CompositeOutcome) -> ChatMessageStatus:
    """整体状态 → 助手消息终态（可选失败仍以可用部分交付）。"""
    if outcome.status is CompositeStatus.STOPPED:
        return ChatMessageStatus.STOPPED
    if outcome.status is CompositeStatus.FAILED:
        return ChatMessageStatus.ERROR
    return ChatMessageStatus.DONE


def first_failure(outcome: CompositeOutcome) -> StepFailure | None:
    for step in outcome.steps:
        if step.failure is not None:
            return step.failure
    return None


def wait_reason_for(outcome: CompositeOutcome) -> str | None:
    for step in outcome.steps:
        if step.state is StepState.NEEDS_INPUT and step.delivery is not None:
            return step.delivery.wait_reason
    return None


__all__ = [
    "CompositeOrchestrationService",
    "CompositeTurnResult",
    "ModuleServiceStepRunner",
    "first_failure",
    "message_status_for",
    "projection_updates",
    "render_final_content",
    "step_result_from_delivery",
    "wait_reason_for",
]
