"""Learning mission and prerequisite-knowledge diagnosis domain service.

LearningService implements T021: it lets users define real learning missions,
runs evidence-backed diagnostics, computes knowledge states from observable
answers only, and supports user correction with a next validation task. It does
not treat browsing, completion rates or model guesses as mastery.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from bridges.contracts.learning import (
    AnswerEvaluatedState,
    DiagnosticAnswer,
    DiagnosticAnswerCreateRequest,
    DiagnosticQuestion,
    DiagnosticQuestionCreateRequest,
    DiagnosticQuestionType,
    DiagnosticResult,
    DiagnosticRun,
    DiagnosticRunStatus,
    EvidenceRef,
    KnowledgeConfidence,
    KnowledgeState,
    KnowledgeStateCorrection,
    KnowledgeStateStatus,
    LearningActivity,
    LearningActivityType,
    LearningMission,
    LearningMissionCreateRequest,
    TeachingPlan,
    TeachingPlanStatus,
)
from bridges.learning.adapters import LearningError
from bridges.learning.ports import LearningRepository


def _now() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return secrets.token_urlsafe(16)


_ZPD_STATUSES: frozenset[KnowledgeStateStatus] = frozenset(
    {KnowledgeStateStatus.EMERGING, KnowledgeStateStatus.SUPPORTED}
)
"""Statuses that place a concept in the zone of proximal development."""


class LearningService:
    """Application service for learning missions and evidence-backed diagnosis."""

    def __init__(self, repository: LearningRepository) -> None:
        self._repository = repository

    def create_mission(
        self, account_id: str, request: LearningMissionCreateRequest
    ) -> LearningMission:
        """Create a learning mission that anchors all downstream teaching records."""
        now = _now()
        mission = LearningMission(
            mission_id=_new_id(),
            owner_account_id=account_id,
            project_id=request.project_id,
            title=request.title,
            goal=request.goal,
            scope_concepts=list(request.scope_concepts),
            constraints=list(request.constraints),
            success_criteria=list(request.success_criteria),
            version=1,
            created_at=now,
            updated_at=now,
        )
        return self._repository.save_mission(mission)

    def get_mission(self, account_id: str, mission_id: str) -> LearningMission:
        """Return a mission if the account owns it."""
        return self._repository.get_mission(account_id, mission_id)

    def list_missions(
        self, account_id: str, project_id: str | None = None
    ) -> list[LearningMission]:
        """List missions for the account, optionally filtered by project."""
        return self._repository.list_missions(account_id, project_id)

    def _default_questions_for_mission(
        self, mission: LearningMission
    ) -> list[DiagnosticQuestion]:
        """Generate deterministic placeholder questions when none are supplied.

        Placeholder questions are not grounded in external scientific evidence;
        production flows should supply questions bound to claim-graph evidence.
        The generated questions still record concept scope and version.
        """
        return [
            DiagnosticQuestion(
                question_id=_new_id(),
                mission_id=mission.mission_id,
                concept_id=concept,
                question_text=f"请说明你对“{concept}”的理解，并尝试给出一个例子或应用。",
                question_type=DiagnosticQuestionType.EXPLANATION,
                expected_answer_hints=["定义", "例子", "适用条件"],
                evidence_refs=[],
            )
            for concept in mission.scope_concepts
        ]

    def create_diagnostic_run(
        self,
        account_id: str,
        mission_id: str,
        questions: list[DiagnosticQuestionCreateRequest] | None = None,
    ) -> DiagnosticRun:
        """Create a diagnostic run for a mission.

        Questions can be supplied by the caller with evidence refs; otherwise a
        deterministic placeholder set is generated from the mission scope.
        """
        mission = self._repository.get_mission(account_id, mission_id)
        now = _now()

        question_requests = questions or []
        if not question_requests:
            diagnostic_questions = self._default_questions_for_mission(mission)
        else:
            diagnostic_questions = [
                DiagnosticQuestion(
                    question_id=_new_id(),
                    mission_id=mission.mission_id,
                    concept_id=q.concept_id,
                    question_text=q.question_text,
                    question_type=q.question_type,
                    expected_answer_hints=list(q.expected_answer_hints),
                    evidence_refs=list(q.evidence_refs),
                )
                for q in question_requests
            ]

        run = DiagnosticRun(
            run_id=_new_id(),
            mission_id=mission.mission_id,
            owner_account_id=account_id,
            status=DiagnosticRunStatus.DRAFT,
            questions=diagnostic_questions,
            answers=[],
            version=1,
            created_at=now,
        )
        return self._repository.save_diagnostic_run(run)

    def get_diagnostic_run(self, account_id: str, run_id: str) -> DiagnosticRun:
        """Return a diagnostic run if the account owns it."""
        return self._repository.get_diagnostic_run(account_id, run_id)

    def record_answer(
        self,
        account_id: str,
        run_id: str,
        request: DiagnosticAnswerCreateRequest,
    ) -> DiagnosticAnswer:
        """Record a diagnostic answer bound to evidence and evaluation.

        The answer is stored as learning evidence. Knowledge states are not
        updated until the diagnostic run is completed, so partial answers never
        leave the state in an inconsistent intermediate form.
        """
        run = self._repository.get_diagnostic_run(account_id, run_id)
        if run.status == DiagnosticRunStatus.COMPLETED:
            raise LearningError("诊断运行已完成，不能继续作答。")
        if run.status == DiagnosticRunStatus.CANCELLED:
            raise LearningError("诊断运行已取消。")

        question_ids = {q.question_id for q in run.questions}
        if request.question_id not in question_ids:
            raise LearningError("问题不属于当前诊断运行。")

        if run.status == DiagnosticRunStatus.DRAFT:
            run.status = DiagnosticRunStatus.RUNNING

        answer = DiagnosticAnswer(
            answer_id=_new_id(),
            run_id=run.run_id,
            question_id=request.question_id,
            response_text=request.response_text,
            evidence_refs=list(request.evidence_refs),
            evaluated_state=request.evaluated_state,
            evaluator=request.evaluator,
            evaluation_reason=request.evaluation_reason,
            created_at=_now(),
        )
        run.answers.append(answer)
        self._repository.save_diagnostic_run(run)
        return answer

    def _answers_for_concept(
        self, run: DiagnosticRun, concept_id: str
    ) -> list[DiagnosticAnswer]:
        question_ids = {
            q.question_id for q in run.questions if q.concept_id == concept_id
        }
        return [a for a in run.answers if a.question_id in question_ids]

    def _question_for_answer(
        self, run: DiagnosticRun, answer: DiagnosticAnswer
    ) -> DiagnosticQuestion:
        for question in run.questions:
            if question.question_id == answer.question_id:
                return question
        raise LearningError("答案对应的问题不存在。")

    def _evidence_refs_for_concept(
        self, run: DiagnosticRun, concept_id: str
    ) -> list[EvidenceRef]:
        refs: list[EvidenceRef] = []
        seen: set[str] = set()
        for question in run.questions:
            if question.concept_id != concept_id:
                continue
            for ref in question.evidence_refs:
                key = f"{ref.claim_id}:{ref.evidence_id}:{ref.source_id}:{ref.document_id}"
                if key not in seen:
                    seen.add(key)
                    refs.append(ref)
        for answer in self._answers_for_concept(run, concept_id):
            for ref in answer.evidence_refs:
                key = f"{ref.claim_id}:{ref.evidence_id}:{ref.source_id}:{ref.document_id}"
                if key not in seen:
                    seen.add(key)
                    refs.append(ref)
        return refs

    def _compute_state_for_concept(
        self,
        account_id: str,
        run: DiagnosticRun,
        concept_id: str,
    ) -> KnowledgeState:
        """Compute a knowledge state from observable diagnostic answers only."""
        answers = self._answers_for_concept(run, concept_id)
        now = _now()

        if not answers:
            return KnowledgeState(
                state_id=_new_id(),
                mission_id=run.mission_id,
                owner_account_id=account_id,
                concept_id=concept_id,
                status=KnowledgeStateStatus.UNKNOWN,
                confidence=KnowledgeConfidence.LOW,
                supporting_record_ids=[],
                refuting_record_ids=[],
                uncertainty_reason="缺少学习证据；未作答或评估结果不可用。",
                scope=f"mission:{run.mission_id}",
                next_validation_task=f"完成关于“{concept_id}”的诊断问题。",
                version=1,
                created_at=now,
                updated_at=now,
            )

        states = [a.evaluated_state for a in answers]
        partial_count = states.count(AnswerEvaluatedState.PARTIAL)
        incorrect_count = states.count(AnswerEvaluatedState.INCORRECT)
        has_application = any(
            self._question_for_answer(run, a).question_type
            in {DiagnosticQuestionType.APPLICATION, DiagnosticQuestionType.COMPUTATION}
            for a in answers
        )

        supporting_ids = [
            a.answer_id for a in answers if a.evaluated_state == AnswerEvaluatedState.CORRECT
        ]
        refuting_ids = [
            a.answer_id
            for a in answers
            if a.evaluated_state
            in {AnswerEvaluatedState.INCORRECT, AnswerEvaluatedState.NEEDS_REVIEW}
        ]

        if incorrect_count > 0 or partial_count > 0:
            status = KnowledgeStateStatus.EMERGING
            confidence = KnowledgeConfidence.LOW
            uncertainty_reason = "存在错误或不完善回答，需进一步验证。"
            next_task = f"针对“{concept_id}”的薄弱点进行检索练习并纠正误区。"
        else:
            if has_application:
                status = KnowledgeStateStatus.ROBUST
                confidence = KnowledgeConfidence.HIGH
                uncertainty_reason = None
                next_task = f"在“{concept_id}”的迁移情境中完成一次延迟复测。"
            else:
                status = KnowledgeStateStatus.SUPPORTED
                confidence = KnowledgeConfidence.MODERATE
                uncertainty_reason = "有正确回答，但缺少迁移或延迟证据。"
                next_task = f"在“{concept_id}”的新情境中应用所学。"

        return KnowledgeState(
            state_id=_new_id(),
            mission_id=run.mission_id,
            owner_account_id=account_id,
            concept_id=concept_id,
            status=status,
            confidence=confidence,
            supporting_record_ids=supporting_ids,
            refuting_record_ids=refuting_ids,
            uncertainty_reason=uncertainty_reason,
            scope=f"mission:{run.mission_id}",
            next_validation_task=next_task,
            version=1,
            created_at=now,
            updated_at=now,
        )

    def complete_diagnostic(
        self, account_id: str, run_id: str
    ) -> DiagnosticResult:
        """Complete a diagnostic run and compute evidence-based knowledge states."""
        run = self._repository.get_diagnostic_run(account_id, run_id)
        if run.status == DiagnosticRunStatus.COMPLETED:
            raise LearningError("诊断运行已经完成。")
        if run.status == DiagnosticRunStatus.CANCELLED:
            raise LearningError("诊断运行已取消，无法完成。")

        now = _now()
        run.status = DiagnosticRunStatus.COMPLETED
        run.completed_at = now
        self._repository.save_diagnostic_run(run)

        mission = self._repository.get_mission(account_id, run.mission_id)
        concepts = set(mission.scope_concepts)
        for question in run.questions:
            concepts.add(question.concept_id)

        states: list[KnowledgeState] = []
        for concept in sorted(concepts):
            state = self._compute_state_for_concept(account_id, run, concept)
            self._repository.save_knowledge_state(state)
            states.append(state)

        zpd_concepts = [
            s.concept_id
            for s in states
            if s.status in _ZPD_STATUSES
        ]
        ready_concepts = [
            s.concept_id for s in states if s.status == KnowledgeStateStatus.ROBUST
        ]
        unknown_concepts = [
            s.concept_id for s in states if s.status == KnowledgeStateStatus.UNKNOWN
        ]

        evidence_count = len(run.answers)
        explanation_parts = [
            f"诊断包含 {evidence_count} 条带评估的回答。",
            f"已掌握/可迁移：{len(ready_concepts)} 个概念；",
            f"最近发展区：{len(zpd_concepts)} 个概念；",
            f"尚需证据：{len(unknown_concepts)} 个概念。",
        ]
        if zpd_concepts:
            explanation_parts.append(
                "下一步优先在 " + "、".join(zpd_concepts) + " 上安排短课与检索练习。"
            )
        else:
            explanation_parts.append("下一步可通过延迟复测巩固已掌握概念。")

        result = DiagnosticResult(
            result_id=_new_id(),
            run_id=run.run_id,
            mission_id=mission.mission_id,
            owner_account_id=account_id,
            knowledge_states=states,
            zpd_concepts=zpd_concepts,
            ready_concepts=ready_concepts,
            unknown_concepts=unknown_concepts,
            evidence_based_explanation=" ".join(explanation_parts),
            next_recommended_task=(
                f"针对最近发展区概念 {zpd_concepts[0]} 开始短课与检索练习。"
                if zpd_concepts
                else "安排一次跨概念延迟复测。"
            ),
            created_at=now,
        )
        return self._repository.save_diagnostic_result(result)

    def get_diagnostic_result(self, account_id: str, result_id: str) -> DiagnosticResult:
        """Return a diagnostic result if the account owns it."""
        return self._repository.get_diagnostic_result(account_id, result_id)

    def get_knowledge_state(
        self, account_id: str, mission_id: str, concept_id: str
    ) -> KnowledgeState:
        """Return the latest active knowledge state for a concept."""
        mission = self._repository.get_mission(account_id, mission_id)
        state = self._repository.get_knowledge_state(account_id, mission.mission_id, concept_id)
        if state is None:
            raise LearningError("该概念尚无知识状态。")
        return state

    def list_knowledge_states(
        self, account_id: str, mission_id: str
    ) -> list[KnowledgeState]:
        """List latest active knowledge states for a mission."""
        return self._repository.list_knowledge_states_for_mission(account_id, mission_id)

    def correct_knowledge_state(
        self,
        account_id: str,
        mission_id: str,
        correction: KnowledgeStateCorrection,
    ) -> KnowledgeState:
        """Record a user correction and produce a new version with a validation task."""
        mission = self._repository.get_mission(account_id, mission_id)
        current = self._repository.get_knowledge_state(
            account_id, mission.mission_id, correction.concept_id
        )
        if current is None:
            raise LearningError("只能纠正已存在的知识状态。")

        now = _now()
        target_state = correction.corrected_state.value
        next_task = (
            f"通过新的诊断或练习验证“{correction.concept_id}”的 {target_state} 状态。"
        )
        new_state = KnowledgeState(
            state_id=_new_id(),
            mission_id=mission.mission_id,
            owner_account_id=account_id,
            concept_id=correction.concept_id,
            status=correction.corrected_state,
            confidence=KnowledgeConfidence.MODERATE,
            supporting_record_ids=list(current.supporting_record_ids),
            refuting_record_ids=list(current.refuting_record_ids),
            uncertainty_reason=f"用户纠正：{correction.reason}",
            scope=current.scope,
            next_validation_task=next_task,
            version=current.version + 1,
            created_at=current.created_at,
            updated_at=now,
        )
        self._repository.save_knowledge_state(new_state)
        return new_state

    def record_activity(
        self,
        account_id: str,
        mission_id: str,
        activity_type: LearningActivityType,
        concept_id: str | None = None,
        details: dict[str, object] | None = None,
    ) -> LearningActivity:
        """Record a learning activity without updating knowledge state.

        Browsing, completion and model guesses are explicitly not treated as
        mastery evidence. They may be stored for scheduling or auditing, but the
        knowledge state for the concept remains unchanged.
        """
        mission = self._repository.get_mission(account_id, mission_id)
        activity = LearningActivity(
            activity_id=_new_id(),
            mission_id=mission.mission_id,
            owner_account_id=account_id,
            activity_type=activity_type,
            concept_id=concept_id,
            details=dict(details or {}),
            created_at=_now(),
        )
        return self._repository.save_activity(activity)

    def compile_teaching_plan(
        self, account_id: str, mission_id: str
    ) -> TeachingPlan:
        """Compile a minimal teaching plan from the mission and current states."""
        mission = self._repository.get_mission(account_id, mission_id)
        states = self._repository.list_knowledge_states_for_mission(
            account_id, mission.mission_id
        )

        zpd_states = [
            s
            for s in states
            if s.status in _ZPD_STATUSES
        ]
        unknown_states = [s for s in states if s.status == KnowledgeStateStatus.UNKNOWN]
        ready_states = [s for s in states if s.status == KnowledgeStateStatus.ROBUST]

        target_concepts = [s.concept_id for s in zpd_states] or [
            s.concept_id for s in unknown_states
        ]
        prerequisites = [s.concept_id for s in ready_states]

        evidence_refs: list[EvidenceRef] = []
        for state in zpd_states:
            evidence_refs.extend(
                EvidenceRef(reason=f"知识状态 {state.status.value}，需要教学支持。")
                for _ in state.supporting_record_ids
            )

        if not target_concepts:
            target_concepts = list(mission.scope_concepts)[:1]
            objective = mission.goal
        else:
            objective = f"掌握“{target_concepts[0]}”并能在新情境中应用。"

        plan = TeachingPlan(
            plan_id=_new_id(),
            mission_id=mission.mission_id,
            owner_account_id=account_id,
            title=f"{mission.title} 教学计划",
            learning_objective=objective,
            target_concepts=target_concepts,
            prerequisites=prerequisites,
            validation_task=f"完成关于“{target_concepts[0]}”的检索练习并解释其适用条件。",
            evidence_refs=evidence_refs,
            status=TeachingPlanStatus.DRAFT,
            created_at=_now(),
        )
        return self._repository.save_teaching_plan(plan)

    def get_teaching_plan(self, account_id: str, plan_id: str) -> TeachingPlan:
        """Return a teaching plan if the account owns it."""
        return self._repository.get_teaching_plan(account_id, plan_id)
