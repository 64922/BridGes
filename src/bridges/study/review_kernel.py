"""复盘计划的登记配方、私有产物与完成收据（工单 33）。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from bridges.contracts.study import StudyReview, StudyState
from bridges.kernel.contracts import (
    ArtifactTrust,
    KernelStatus,
    NodeArtifact,
    NodeExecution,
    NodeInvocation,
    NodeReceiptStatus,
    NodeSpec,
    QualityGateResult,
    QualityVerdict,
    RecipeDefinition,
    RecipeInputs,
)
from bridges.kernel.executor import NodeKernel
from bridges.kernel.guard import RunCommitGuard
from bridges.kernel.registry import RecipeRegistry
from bridges.kernel.repository import NodeKernelRepository
from bridges.study.kernel import StoppedError, SupersededError, _digest
from bridges.study.review import (
    REVIEW_CAPABILITY_VERSIONS,
    REVIEW_PROTOCOL_VERSION,
    ReviewPlanError,
    evaluate_calculation,
    plan_review,
)

NODE_REVIEW_PLAN = "study.plan_review"
GATE_REVIEW_PLAN = "study.review_preverified"
REVIEW_RECIPE_ID = "study-review-preverified"
REVIEW_RECIPE_VERSION = "study-review-recipe-v2"


def _plan_input_key(inputs: RecipeInputs) -> str:
    assert inputs.prior_digest is not None, "复盘计划缺少冻结输入指纹"
    return inputs.prior_digest


def _plan_gate(
    invocation: NodeInvocation, execution: NodeExecution,
) -> QualityGateResult:
    """只有通过私有题目合同门的计划才保存为可恢复产物。"""
    del invocation
    review = StudyReview.model_validate(execution.artifact.payload["review"])
    passed = all(
        question.asked or (
            not question.legacy
            and question.verification is not None
            and question.verification.status == "consistent"
            and question.verification.question_matches_knowledge
            and question.verification.rubric_supported
            and question.verification.answer_consistent
        )
        for question in review.questions
    )
    return QualityGateResult(
        gate=GATE_REVIEW_PLAN,
        verdict=QualityVerdict.PASS if passed else QualityVerdict.BLOCKED,
        code="" if passed else "study_review_verify_unverified",
        message="" if passed else "复盘计划未通过出题前核验，请重试。",
    )


class ReviewPlanKernel:
    """只持久化通过独立核验的完整计划；事件只发布节点状态。"""

    def __init__(
        self, service: Any, run: Any,
        invoke: Callable[[str, dict[str, Any]], dict[str, Any]],
        *, stop_event: Any,
        event_sink: Callable[[str, str, int | None], None],
    ) -> None:
        self._service = service
        self._run = run
        self._invoke = invoke
        self._stop_event = stop_event
        self._event_sink = event_sink
        self._recipe = RecipeDefinition(
            recipe_id=REVIEW_RECIPE_ID,
            recipe_version=REVIEW_RECIPE_VERSION,
            nodes=(NodeSpec(
                name=NODE_REVIEW_PLAN,
                capability=NODE_REVIEW_PLAN,
                capability_version=REVIEW_CAPABILITY_VERSIONS[NODE_REVIEW_PLAN],
                artifact_type="study.review_plan",
                input_key=_plan_input_key,
                required_gates=(GATE_REVIEW_PLAN,),
                description="冻结范围，生成并独立核验题干、私有评分依据及计算。",
            ),),
        )
        self._registry = RecipeRegistry(
            capabilities=REVIEW_CAPABILITY_VERSIONS, gates=(GATE_REVIEW_PLAN,),
        )
        self._registry.register(self._recipe)

    def plan(
        self, state: StudyState, *, repair: Mapping[str, Any] | None = None,
    ) -> StudyReview:
        from bridges.study.tutoring import page_sources

        scope_refs = set(state.scope.fragment_ids) if state.scope else set()
        sources = [
            source.model_dump() for source in page_sources(state, "")
            if source.source_id in scope_refs
        ]
        calculations: list[dict[str, Any]] = []

        def calculate(expression: str, variables: Mapping[str, float]) -> float:
            capability = "study.calculate"
            # 确定性工具也必须实际命中登记能力和版本，不接受模型改名扩权。
            if capability not in self._registry.capabilities:
                raise ValueError("复盘计算工具未登记。")
            version = REVIEW_CAPABILITY_VERSIONS[capability]
            tools = {"study-calculate-v1": evaluate_calculation}
            if version not in tools:
                raise ValueError("复盘计算工具版本不兼容。")
            value = tools[version](expression, variables)
            calculations.append({
                "capability": capability, "version": version,
                "expression": expression, "variables": dict(variables), "value": value,
            })
            return value

        def execute(invocation: NodeInvocation) -> NodeExecution:
            review = plan_review(
                self._service, self._run, state, self._invoke,
                repair=repair, calculate=calculate,
            )
            artifact = NodeArtifact.build(
                account_id=invocation.account_id,
                conversation_id=invocation.conversation_id,
                run_id=invocation.run_id, task_id=None, task_version=None,
                recipe_id=REVIEW_RECIPE_ID, recipe_version=REVIEW_RECIPE_VERSION,
                node=NODE_REVIEW_PLAN, artifact_type="study.review_plan",
                capability_version=invocation.spec.capability_version,
                trust_state=ArtifactTrust.QUALIFIED,
                input_key=_plan_input_key(invocation.inputs), input_deps=(),
                source_refs=tuple(sorted(scope_refs)), read_scope="本节已核验的冻结范围",
                requirement_coverage=(), unconfirmed=(), error=None,
                payload={"review": review.model_dump(), "calculations": calculations,
                         "protocol_version": REVIEW_PROTOCOL_VERSION,
                         "capability_versions": dict(REVIEW_CAPABILITY_VERSIONS)},
                now=datetime.now(UTC),
            )
            return NodeExecution(
                artifact=artifact, verdict=QualityVerdict.PASS,
                status=NodeReceiptStatus.COMPLETED,
            )

        run = self._run
        kernel = NodeKernel(
            registry=self._registry,
            repository=NodeKernelRepository(self._service._repo.database),
            guard=RunCommitGuard(
                self._service._repo, account_id=run.account_id, run_id=run.run_id,
                conversation_id=run.conversation_id,
                assistant_message_id=run.assistant_message_id, stop_event=self._stop_event,
            ),
            gates={GATE_REVIEW_PLAN: _plan_gate}, runner=execute,
        )
        result = kernel.execute(
            recipe=self._recipe,
            inputs=RecipeInputs(
                account_id=run.account_id, conversation_id=run.conversation_id,
                run_id=run.run_id, user_message_id=run.user_message_id,
                user_content="", task_id=None, task_version=None, wait_identity=None,
                artifacts={}, prior_digest=_digest({
                    "protocol": REVIEW_PROTOCOL_VERSION,
                    "capabilities": REVIEW_CAPABILITY_VERSIONS,
                    "scope": state.scope.model_dump() if state.scope else None,
                    "sources": sources,
                    "asked": [item.model_dump() for item in state.review.questions
                              if item.asked] if state.review else [],
                    "repair": dict(repair) if repair else None,
                    "policy": (run.config or {}).get("global_writing_policy"),
                }),
            ),
            event_sink=self._event_sink, stop_event=self._stop_event,
        )
        if result.status is KernelStatus.STOPPED:
            raise StoppedError(result.stopped_at or NODE_REVIEW_PLAN)
        if result.status is KernelStatus.REJECTED:
            raise SupersededError(result.rejection_code or "generation_superseded")
        if result.failure is not None:
            raise ReviewPlanError(result.failure.code, result.failure.message)
        if result.delivery is None:
            raise ReviewPlanError("study_review_verify_unverified", "复盘计划产物缺失，请重试。")
        return StudyReview.model_validate(result.delivery.payload["review"])
