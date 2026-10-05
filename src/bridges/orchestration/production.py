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
from dataclasses import dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING, Any

from bridges.contracts.chat import (
    ChatMessageStatus,
    ResultTrust,
    TurnOutcome,
    TurnResultBlock,
    TurnResultProjection,
)
from bridges.contracts.modules import ModuleDelivery
from bridges.contracts.understanding import HardCondition, HardConditionKind
from bridges.kernel.contracts import ArtifactTrust, InputDependency, NodeArtifact
from bridges.kernel.repository import NodeKernelRepository
from bridges.orchestration.contracts import (
    COMPOSITE_ARTIFACT_TYPE,
    COMPOSITE_CONTRACT_VERSION,
    COMPOSITE_RECIPE_ID,
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
from bridges.orchestration.synthesis import FinalGate, Synthesizer, synthesis_payload

if TYPE_CHECKING:
    from bridges.chat.repository import ConversationRepository

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

    def __init__(
        self,
        *,
        resolve: ResolveCall,
        run: RunCall,
        qualify: (
            Callable[[CompositeStep, StepRunContext, ModuleDelivery], StepResult]
            | None
        ) = None,
    ) -> None:
        self._resolve = resolve
        self._run = run
        self._qualify = qualify

    def can_reuse(
        self, step: CompositeStep, context: StepRunContext, previous: StepResult
    ) -> bool:
        if self._qualify is None or previous.delivery is None:
            return False
        current = self._qualify(step, context, previous.delivery)
        return current.trust_state == "qualified" and current.scope_key == previous.scope_key

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
        if self._qualify is not None:
            return self._qualify(step, context, delivery)
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
        trust_state="draft",
        delivery=delivery,
        artifact_refs=dict(delivery.artifact_refs),
        evidence_refs=[],
        read_scope="尚未核验",
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
        if not gate.passed and outcome.status not in {
            CompositeStatus.REJECTED,
            CompositeStatus.STOPPED,
        }:
            repaired = self._repair_gate_failure(
                outcome=outcome,
                draft=draft,
                gate=gate,
                runners=runners,
                context=context,
                budget=budget,
                commit_check=commit_check,
            )
            if repaired is not None:
                outcome, draft, gate = repaired
        return CompositeTurnResult(outcome=outcome, draft=draft, gate=gate)

    def _repair_gate_failure(
        self,
        *,
        outcome: CompositeOutcome,
        draft: SynthesisDraft,
        gate: SynthesisGateResult,
        runners: Mapping[str, StepRunner],
        context: StepRunContext,
        budget: CompositeBudget | None,
        commit_check: CommitCheck | None,
    ) -> tuple[CompositeOutcome, SynthesisDraft, SynthesisGateResult] | None:
        """最终门失败后的一轮受控补证/结构修复；与步骤失败共享同一轮预算。

        第二轮由 ``CompositePlanner.adjusted`` 与账本 ``begin_adjustment``
        同时拒绝：账本已用于步骤失败调整时，这里不再新增一轮。
        """
        adjusted, violation = self._planner.adjusted(
            outcome.plan, reason="gate_repair"
        )
        if adjusted is None or violation is not None:
            return None
        if budget is not None and not budget.begin_adjustment("composite_gate_repair"):
            return None
        repair_ids = _gate_repair_step_ids(gate, outcome, draft)
        prior = {
            step.step_id: step
            for step in outcome.steps
            if step.step_id not in repair_ids
        }
        executor = CompositeExecutor(
            planner=self._planner, budget=budget, commit_check=commit_check
        )
        repaired = executor.execute(
            plan=adjusted,
            runners=runners,
            context=replace(context, attempt=1),
            prior_results=prior,
        )
        if budget is not None:
            budget.end_adjustment(
                repaired.status.value,
                {"gate_repair_steps": sorted(repair_ids)},
            )
        repaired_draft = self._synthesizer.build(repaired)
        repaired_gate = self._gate.check(repaired_draft, repaired.steps)
        return repaired, repaired_draft, repaired_gate


def render_final_content(draft: SynthesisDraft) -> str:
    """按一个目标组织正文：结论 → 各分支详情 → 限定与缺口。"""
    parts: list[str] = [draft.summary]
    parts.extend(section.body for section in draft.sections if section.body)
    if draft.limitations:
        parts.append("说明：" + "；".join(dict.fromkeys(draft.limitations)))
    return "\n\n".join(part for part in parts if part)


def hydrate_career_projection(
    repository: NodeKernelRepository,
    *,
    account_id: str,
    outcome: CompositeOutcome,
) -> CompositeOutcome:
    """从内核产物回填 career 结构化投影。

    私人背景/简历正文只存在内核产物中；父图检查点不携带 career 投影，
    提交时按引用回读，避免同一私文在检查点里重复持久化。
    """
    steps: list[StepResult] = []
    changed = False
    for step in outcome.steps:
        delivery = step.delivery
        if (
            delivery is not None
            and step.module_id == "career"
            and not delivery.projection
        ):
            ref = step.artifact_refs.get("career.verify") or next(
                iter(step.artifact_refs.values()), None
            )
            artifact = (
                repository.get_artifact(account_id, ref) if ref is not None else None
            )
            payload = artifact.payload.get("projection") if artifact is not None else None
            if isinstance(payload, dict):
                delivery = delivery.model_copy(
                    update={"projection": dict(payload)}
                )
                step = step.model_copy(update={"delivery": delivery})
                changed = True
        steps.append(step)
    if not changed:
        return outcome
    return outcome.model_copy(update={"steps": steps})


def projection_updates(outcome: CompositeOutcome) -> dict[str, dict[str, Any]]:
    """汇总各分支投影（一次 finalize 提交；未核验完成不提交投影）。"""
    updates: dict[str, dict[str, Any]] = {}
    for step in outcome.steps:
        delivery = step.delivery
        if delivery is None or not delivery.projection_field:
            continue
        if step.state is StepState.COMPLETED and step.trust_state != "qualified":
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


#: 复合调度状态 → 公开交付分类（工单 38）。
_COMPOSITE_TURN_OUTCOMES: dict[CompositeStatus, TurnOutcome] = {
    CompositeStatus.COMPLETED: TurnOutcome.COMPLETE,
    CompositeStatus.PARTIAL: TurnOutcome.PARTIAL,
    CompositeStatus.NEEDS_INPUT: TurnOutcome.NEEDS_INPUT,
    CompositeStatus.BLOCKED: TurnOutcome.BLOCKED,
    CompositeStatus.FAILED: TurnOutcome.FAILED,
    CompositeStatus.STOPPED: TurnOutcome.CANCELLED,
    CompositeStatus.REJECTED: TurnOutcome.BLOCKED,
}


def turn_result_for_outcome(
    outcome: CompositeOutcome,
    *,
    task_id: str | None = None,
    task_version: int | None = None,
) -> TurnResultProjection:
    """复合结果的公开回合投影：只含通过门的交付块与真实阻塞/恢复。

    待核验草稿不进入：只有 ``COMPLETED 且 trust=qualified`` 的步骤成为
    已交付块；未合格完成、失败、阻塞与失效步骤只作为缺口与真实原因列出。
    路由能力字段由读取投影从消息路由快照补齐。
    """
    from bridges.chat.turn_result import (  # noqa: PLC0415
        TURN_RESULT_VERSION,
        outcome_label,
        recovery_for_error,
        trust_label,
    )
    from bridges.state_copy import render_state_copy  # noqa: PLC0415
    from bridges.state_copy.catalog import COMPOSITE_MODULE_LABELS  # noqa: PLC0415

    delivered: list[TurnResultBlock] = []
    blocked: list[TurnResultBlock] = []
    for step in outcome.steps:
        label = COMPOSITE_MODULE_LABELS.get(step.module_id, step.module_id)
        if step.state is StepState.COMPLETED and step.trust_state == "qualified":
            delivered.append(
                TurnResultBlock(
                    module_id=step.module_id,
                    label=label,
                    state=step.state.value,
                    trust=ResultTrust.QUALIFIED,
                    detail=step.summary if step.summary else "",
                )
            )
        elif step.state is StepState.COMPLETED:
            blocked.append(
                TurnResultBlock(
                    module_id=step.module_id,
                    label=label,
                    state=step.state.value,
                    trust=ResultTrust.EVIDENCE_BOUND,
                    detail=render_state_copy("composite.result.unqualified"),
                )
            )
        elif step.state is StepState.NEEDS_INPUT:
            continue
        elif step.state is not StepState.SKIPPED and step.state is not StepState.PENDING:
            detail = (
                step.failure.message
                if step.failure is not None
                else (
                    step.blocked_reason
                    or render_state_copy("chat.result.blocked_detail")
                )
            )
            blocked.append(
                TurnResultBlock(
                    module_id=step.module_id,
                    label=label,
                    state=step.state.value,
                    trust=ResultTrust.EVIDENCE_BOUND,
                    detail=detail,
                )
            )
    result_outcome = _COMPOSITE_TURN_OUTCOMES.get(
        outcome.status, TurnOutcome.BLOCKED
    )
    if result_outcome is TurnOutcome.COMPLETE and blocked:
        result_outcome = TurnOutcome.PARTIAL
    overall_trust: ResultTrust | None = None
    if delivered:
        overall_trust = (
            ResultTrust.QUALIFIED if not blocked else ResultTrust.EVIDENCE_BOUND
        )
    failure = first_failure(outcome)
    gaps = list(
        dict.fromkeys(
            [
                *outcome.blocked_conclusions,
                *(item.detail for item in blocked if item.detail),
            ]
        )
    )
    return TurnResultProjection(
        version=TURN_RESULT_VERSION,
        outcome=result_outcome,
        outcome_label=outcome_label(result_outcome),
        trust=overall_trust,
        trust_label=trust_label(overall_trust) if overall_trust is not None else None,
        delivered=delivered,
        blocked=blocked,
        gaps=gaps,
        recovery=(
            recovery_for_error(failure.code)
            if result_outcome is TurnOutcome.FAILED and failure is not None
            else None
        ),
        wait_reason=(
            wait_reason_for(outcome)
            if result_outcome is TurnOutcome.NEEDS_INPUT
            else None
        ),
        task_id=task_id,
        task_version=task_version,
    )


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


#: 硬条件类别 → 模块条件键；条件变化沿输入依赖失效。
_CONDITION_KEY_BY_KIND: dict[HardConditionKind, str] = {
    HardConditionKind.CITY: "city",
    HardConditionKind.YEAR_RANGE: "year_range",
    HardConditionKind.SOURCE_RESTRICTION: "source",
    HardConditionKind.NO_NETWORK: "network",
    HardConditionKind.LOCAL_ONLY: "network",
}


def condition_snapshot(conditions: Sequence[HardCondition]) -> list[dict[str, str]]:
    """当前硬条件的脱敏快照（只含类别与原词，用于跨轮差异）。"""
    return [{"kind": item.kind.value, "text": item.text} for item in conditions]


def changed_condition_keys(
    prior: Mapping[str, Any] | None, conditions: Sequence[HardCondition]
) -> list[str]:
    """比较上一轮与当前硬条件，只返回命中的模块条件键。"""
    if not prior or not isinstance(prior.get("conditions"), list):
        return []
    previous = {
        (str(item.get("kind")), str(item.get("text")))
        for item in prior["conditions"]
        if isinstance(item, dict)
    }
    current = {(item.kind.value, item.text) for item in conditions}
    keys: set[str] = set()
    for value in {kind for kind, _ in previous ^ current}:
        try:
            kind = HardConditionKind(value)
        except ValueError:
            continue
        keys.add(_CONDITION_KEY_BY_KIND.get(kind, value))
    return sorted(keys)


def load_prior_composite(
    conversations: ConversationRepository, *, account_id: str, conversation_id: str
) -> dict[str, Any] | None:
    """读取本会话最近一次综合产物的脱敏载荷；不存在或版本不符返回 ``None``。"""
    repository = NodeKernelRepository(conversations.database)
    candidates = [
        artifact
        for artifact in repository.list_artifacts(account_id, conversation_id)
        if artifact.artifact_type == COMPOSITE_ARTIFACT_TYPE and artifact.error is None
    ]
    if not candidates:
        return None
    latest = max(
        candidates, key=lambda artifact: (artifact.created_at, artifact.artifact_id)
    )
    payload = dict(latest.payload)
    if payload.get("contract_version") != COMPOSITE_CONTRACT_VERSION:
        return None
    return payload


def persist_synthesis_artifact(
    repository: NodeKernelRepository,
    *,
    account_id: str,
    conversation_id: str,
    run_id: str,
    task_id: str | None,
    task_version: int | None,
    outcome: CompositeOutcome,
    gate: SynthesisGateResult,
    draft: SynthesisDraft,
    conditions: Sequence[Mapping[str, Any]],
    now: datetime,
) -> NodeArtifact:
    """在守卫事务内保存脱敏综合产物：计划/步骤/证据引用、任务版本与门裁决。

    不保存模块正文、投影或私人原文：复用需要的证据由步骤引用回链重建。
    """
    payload: dict[str, Any] = dict(synthesis_payload(outcome, gate, draft=draft))
    payload["plan"] = {
        "plan_id": outcome.plan.plan_id,
        "revision": outcome.plan.revision,
        "user_message_id": outcome.plan.user_message_id,
        "mode": outcome.plan.mode,
        "steps": [
            {
                "step_id": step.step_id,
                "module_id": step.module_id,
                "recipe": f"{step.recipe_id}@{step.recipe_version}",
                "capability": f"{step.capability}@{step.capability_version}",
                "depends_on": list(step.depends_on),
                "required": step.required,
                "condition_keys": list(step.condition_keys),
            }
            for step in outcome.plan.steps
        ],
    }
    payload["task"] = {"task_id": task_id, "task_version": task_version}
    payload["conditions"] = [dict(item) for item in conditions]
    payload["steps"] = [_step_record(step) for step in outcome.steps]
    completed = [step for step in outcome.steps if step.state is StepState.COMPLETED]
    trust_state = (
        ArtifactTrust.QUALIFIED
        if completed and all(step.trust_state == "qualified" for step in completed)
        else ArtifactTrust.EVIDENCE_BOUND
    )
    deps: list[InputDependency] = []
    for step in outcome.steps:
        ref = _terminal_ref(step)
        if ref is None:
            continue
        artifact = repository.get_artifact(account_id, ref)
        if artifact is None:
            continue
        deps.append(
            InputDependency(
                node=artifact.node,
                artifact_id=artifact.artifact_id,
                content_hash=artifact.content_hash,
            )
        )
    artifact = NodeArtifact.build(
        account_id=account_id,
        conversation_id=conversation_id,
        run_id=run_id,
        task_id=task_id,
        task_version=task_version,
        recipe_id=COMPOSITE_RECIPE_ID,
        recipe_version=COMPOSITE_CONTRACT_VERSION,
        node="orchestration.synthesis",
        artifact_type=COMPOSITE_ARTIFACT_TYPE,
        capability_version=COMPOSITE_CONTRACT_VERSION,
        trust_state=trust_state,
        input_key=f"{outcome.plan.plan_id}:{outcome.plan.revision}:{run_id}",
        input_deps=tuple(deps),
        source_refs=(),
        read_scope="按目标组织的综合结论与步骤引用",
        requirement_coverage=(),
        unconfirmed=tuple(dict.fromkeys(draft.limitations)),
        error=None,
        payload=payload,
        now=now,
    )
    repository.save_artifact(artifact)
    return artifact


def previous_composite_modules(payload: Mapping[str, Any] | None) -> list[str]:
    """上一轮综合产物记录的计划模块顺序（用于修订轮重建复合计划）。"""
    if not payload:
        return []
    plan = payload.get("plan")
    steps = plan.get("steps") if isinstance(plan, dict) else None
    modules: list[str] = []
    for step in steps or []:
        if isinstance(step, dict) and step.get("module_id"):
            module = str(step["module_id"])
            if module not in modules:
                modules.append(module)
    return modules


def _step_record(step: StepResult) -> dict[str, Any]:
    """步骤的脱敏引用记录：不保存正文、投影或私人证据原文。"""
    return {
        "step_id": step.step_id,
        "module_id": step.module_id,
        "state": step.state.value,
        "trust_state": step.trust_state,
        "reused": step.reused,
        "artifact_refs": dict(step.artifact_refs),
        "input_fingerprint": step.input_fingerprint,
        "resolved_param_names": list(step.resolved_param_names),
        "scope_key": step.scope_key,
    }


def _terminal_ref(step: StepResult) -> str | None:
    terminal = (
        "github.present" if step.module_id == "github" else f"{step.module_id}.verify"
    )
    return step.artifact_refs.get(terminal) or next(
        iter(step.artifact_refs.values()), None
    )


def _gate_repair_step_ids(
    gate: SynthesisGateResult,
    outcome: CompositeOutcome,
    draft: SynthesisDraft,
) -> set[str]:
    """最终门失败指向的步骤；无法定位时保守地重跑全部已完成步骤。"""
    ids: set[str] = set()
    unsupported = set(gate.unsupported_claims)
    missing = set(gate.missing_qualifications)
    new_facts = set(gate.new_facts)
    for step in outcome.steps:
        if unsupported & {claim.text for claim in step.claims}:
            ids.add(step.step_id)
        if missing & set(step.unconfirmed):
            ids.add(step.step_id)
        if any(
            ref.split(":", 1)[0] == step.step_id for ref in gate.mislinked_refs
        ):
            ids.add(step.step_id)
    for section in draft.sections:
        if section.title in new_facts:
            ids.update(section.step_ids)
    if not ids:
        ids = {
            step.step_id
            for step in outcome.steps
            if step.state is StepState.COMPLETED
        }
    return ids


__all__ = [
    "CompositeOrchestrationService",
    "CompositeTurnResult",
    "ModuleServiceStepRunner",
    "changed_condition_keys",
    "condition_snapshot",
    "first_failure",
    "hydrate_career_projection",
    "load_prior_composite",
    "message_status_for",
    "persist_synthesis_artifact",
    "previous_composite_modules",
    "projection_updates",
    "render_final_content",
    "step_result_from_delivery",
    "turn_result_for_outcome",
    "wait_reason_for",
]
