"""作答判定的登记配方、私有产物与完成收据（工单 34）。

判定、反馈产物与可重放事件在同一个受守卫的节点事务里提交；同一条用户
消息重试时按输入键回填已完成产物，不重新调用判定。领域状态由服务层在
守卫事务内按产物幂等应用，不复制判定逻辑。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from bridges.contracts.study import StudyReviewQuestion, StudyState
from bridges.kernel.contracts import (
    ArtifactTrust,
    KernelStatus,
    NodeArtifact,
    NodeExecution,
    NodeInvocation,
    NodeReceiptStatus,
    NodeSpec,
    PendingNodeEvent,
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
    ERROR_GRADE_INVALID,
    GRADE_PROTOCOL_VERSION,
    REVIEW_CAPABILITY_VERSIONS,
    GradeOutcome,
    ReviewGradeError,
    grade_question,
)

NODE_REVIEW_GRADE = "study.grade"
GATE_REVIEW_GRADE = "study.review_grade_committed"
GRADE_RECIPE_ID = "study-review-grading"
GRADE_RECIPE_VERSION = "study-review-grade-recipe-v1"


def _grade_input_key(inputs: RecipeInputs) -> str:
    assert inputs.prior_digest is not None, "判定缺少冻结输入指纹"
    return inputs.prior_digest


def _grade_gate(
    invocation: NodeInvocation, execution: NodeExecution,
) -> QualityGateResult:
    """只有逐项核对合格、标准答案不漂移的判定才保存为可恢复产物。"""
    payload = execution.artifact.payload
    try:
        outcome = GradeOutcome.model_validate(payload["outcome"])
    except Exception:
        return QualityGateResult(
            gate=GATE_REVIEW_GRADE,
            verdict=QualityVerdict.BLOCKED,
            code=ERROR_GRADE_INVALID,
            message="判定产物结构不完整，已保留当前题。",
        )
    canonical_ok = outcome.legacy or outcome.canonical_answer == payload.get(
        "frozen_answer", ""
    )
    recheck_ok = outcome.record.recheck_status not in {"conflict", "insufficient"}
    user_ok = outcome.user_message_id == invocation.inputs.user_message_id
    passed = bool(outcome.feedback) and canonical_ok and recheck_ok and user_ok
    return QualityGateResult(
        gate=GATE_REVIEW_GRADE,
        verdict=QualityVerdict.PASS if passed else QualityVerdict.BLOCKED,
        code="" if passed else ERROR_GRADE_INVALID,
        message="" if passed else "判定未通过提交门，已保留当前题。",
    )


class ReviewGradeKernel:
    """持久化判定与反馈产物；事件只发布节点状态与判定摘要。"""

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
            recipe_id=GRADE_RECIPE_ID,
            recipe_version=GRADE_RECIPE_VERSION,
            nodes=(NodeSpec(
                name=NODE_REVIEW_GRADE,
                capability=NODE_REVIEW_GRADE,
                capability_version=REVIEW_CAPABILITY_VERSIONS[NODE_REVIEW_GRADE],
                artifact_type="study.grade_result",
                input_key=_grade_input_key,
                required_gates=(GATE_REVIEW_GRADE,),
                description="逐项核对冻结评分要点、必要时独立复核并提交判定。",
            ),),
        )
        self._registry = RecipeRegistry(
            capabilities=REVIEW_CAPABILITY_VERSIONS, gates=(GATE_REVIEW_GRADE,),
        )
        self._registry.register(self._recipe)

    def question(self, state: StudyState) -> StudyReviewQuestion:
        """取合法当前题：只按激活题 ID 关联，不按标题或顺序猜测。"""
        review = state.review
        if review is None:
            raise ReviewGradeError(
                ERROR_GRADE_INVALID, "没有可判定的复盘题，已保留当前阶段。"
            )
        question = next(
            (
                item
                for item in review.questions
                if item.question_id == review.active_question_id
            ),
            None,
        )
        if question is None or question.judgement is not None:
            raise ReviewGradeError(
                ERROR_GRADE_INVALID, "当前题不存在或已经判定，已保留当前阶段。"
            )
        return question

    def grade(self, state: StudyState, answer: str) -> GradeOutcome:
        question = self.question(state)
        scope = state.scope
        top = state.review.scope_version_id if state.review else ""
        if (
            question.scope_version_id
            and top
            and question.scope_version_id != top
        ) or (
            question.scope_version_id
            and scope is not None
            and question.scope_version_id != scope.scope_version_id
        ):
            raise ReviewGradeError(
                "study_review_scope_changed",
                "题目版本与当前复盘范围不一致，请重新开始复盘。",
            )
        frozen_answer = question.canonical_answer or ""

        def execute(invocation: NodeInvocation) -> NodeExecution:
            outcome = grade_question(
                self._service, self._run, state, question, answer, self._invoke
            )
            artifact = NodeArtifact.build(
                account_id=invocation.account_id,
                conversation_id=invocation.conversation_id,
                run_id=invocation.run_id, task_id=None, task_version=None,
                recipe_id=GRADE_RECIPE_ID, recipe_version=GRADE_RECIPE_VERSION,
                node=NODE_REVIEW_GRADE, artifact_type="study.grade_result",
                capability_version=invocation.spec.capability_version,
                trust_state=ArtifactTrust.QUALIFIED,
                input_key=_grade_input_key(invocation.inputs), input_deps=(),
                source_refs=tuple(question.fragment_ids),
                read_scope="本节冻结题、评分要点与书页片段",
                requirement_coverage=tuple(
                    {
                        "point": item.point,
                        "status": item.status,
                        "fragment_ids": list(item.fragment_ids),
                    }
                    for item in outcome.record.point_checks
                ),
                unconfirmed=(), error=None,
                payload={
                    "outcome": outcome.model_dump(),
                    "frozen_answer": frozen_answer,
                    "protocol_version": GRADE_PROTOCOL_VERSION,
                    "capability_versions": dict(REVIEW_CAPABILITY_VERSIONS),
                },
                now=datetime.now(UTC),
            )
            return NodeExecution(
                artifact=artifact, verdict=QualityVerdict.PASS,
                status=NodeReceiptStatus.COMPLETED,
                events=(
                    PendingNodeEvent(
                        kind="study_grade_committed",
                        payload={
                            "question_id": outcome.question_id,
                            "judgement": outcome.judgement,
                            "recheck_status": outcome.record.recheck_status,
                            "legacy": outcome.legacy,
                        },
                    ),
                ),
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
            gates={GATE_REVIEW_GRADE: _grade_gate}, runner=execute,
        )
        result = kernel.execute(
            recipe=self._recipe,
            inputs=RecipeInputs(
                account_id=run.account_id, conversation_id=run.conversation_id,
                run_id=run.run_id, user_message_id=run.user_message_id,
                user_content=answer, task_id=None, task_version=None,
                wait_identity=None, artifacts={}, prior_digest=_digest({
                    "protocol": GRADE_PROTOCOL_VERSION,
                    "capabilities": REVIEW_CAPABILITY_VERSIONS,
                    "question": question.model_dump(),
                    "scope_version_id": question.scope_version_id,
                    "answer": answer,
                    "user_message_id": run.user_message_id,
                    "policy": (run.config or {}).get("global_writing_policy"),
                }),
            ),
            event_sink=self._event_sink, stop_event=self._stop_event,
        )
        if result.status is KernelStatus.STOPPED:
            raise StoppedError(result.stopped_at or NODE_REVIEW_GRADE)
        if result.status is KernelStatus.REJECTED:
            raise SupersededError(result.rejection_code or "generation_superseded")
        if result.failure is not None:
            raise ReviewGradeError(
                result.failure.code or ERROR_GRADE_INVALID,
                "判定未通过提交门，已保留当前题，请重试。",
            )
        if result.delivery is None:
            raise ReviewGradeError(
                ERROR_GRADE_INVALID, "判定产物缺失，已保留当前题，请重试。"
            )
        return GradeOutcome.model_validate(result.delivery.payload["outcome"])


__all__ = [
    "GATE_REVIEW_GRADE",
    "GRADE_RECIPE_ID",
    "GRADE_RECIPE_VERSION",
    "NODE_REVIEW_GRADE",
    "ReviewGradeKernel",
    "_grade_gate",
]
