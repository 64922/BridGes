"""Short-lesson and retrieval-practice service tests for T022.

The seam under test: an authenticated user can generate a short lesson from a
TeachingPlan, complete retrieval-type exercises, and receive immediate feedback
bound to evidence and fact locks. Browsing-only activities are rejected,
evidence-insufficient lessons are blocked, and high-risk topics require a human
gate.
"""

from __future__ import annotations

import pytest

from science_companion.contracts.learning import (
    AnswerEvaluatedState,
    DiagnosticAnswerCreateRequest,
    DiagnosticQuestionCreateRequest,
    DiagnosticQuestionType,
    EvidenceRef,
    GenerateLessonRequest,
    LearningMissionCreateRequest,
    LessonEvidenceBundle,
    LessonStatus,
    RetrievalExerciseType,
    TeachingQualityGateCheck,
)
from science_companion.contracts.science import FactLock, FactLockType, WordingStrength
from science_companion.learning import InMemoryLearningRepository, LearningError, LearningService, TeachingService


@pytest.fixture
def repository() -> InMemoryLearningRepository:
    return InMemoryLearningRepository()


@pytest.fixture
def learning_service(repository: InMemoryLearningRepository) -> LearningService:
    return LearningService(repository)


@pytest.fixture
def teaching_service(repository: InMemoryLearningRepository) -> TeachingService:
    return TeachingService(repository)


@pytest.fixture
def alice_id() -> str:
    return "account-alice"


@pytest.fixture
def bob_id() -> str:
    return "account-bob"


def _mission_request(title: str = "理解光合作用") -> LearningMissionCreateRequest:
    return LearningMissionCreateRequest(
        title=title,
        goal="能解释光合作用的核心步骤，并将其应用于不同情境。",
        scope_concepts=["光反应", "暗反应", "叶绿素作用", "能量转换"],
        constraints=["只讨论植物细胞", "不涉及C4路径细节"],
        success_criteria=[
            "能画出光反应与暗反应的输入输出",
            "能解释为什么缺光会抑制暗反应",
        ],
    )


def _evidence_bundle(
    evidence_refs: list[EvidenceRef] | None = None,
    fact_locks: list[FactLock] | None = None,
) -> LessonEvidenceBundle:
    return LessonEvidenceBundle(
        graph_id="graph-photosynthesis-1",
        evidence_refs=evidence_refs or [],
        fact_locks=fact_locks or [],
    )


def _sample_fact_lock(lock_id: str, claim_id: str = "claim-1") -> FactLock:
    return FactLock(
        lock_id=lock_id,
        claim_id=claim_id,
        lock_type=FactLockType.TERM_FORMULA,
        canonical_value="光合作用 = 光反应 + 暗反应",
        allowed_variants=[],
        forbidden_transformations=["不得改变定义对象或内涵"],
        required_qualifiers=[],
        evidence_ids=["ev-1"],
        citation_ids=["cite-1"],
        wording_strength_ceiling=WordingStrength.HIGH,
        verification_method="rule",
    )


def _create_plan(
    learning_service: LearningService,
    account_id: str,
) -> tuple[str, str]:
    mission = learning_service.create_mission(account_id, _mission_request())
    questions = [
        DiagnosticQuestionCreateRequest(
            concept_id="光反应",
            question_text="光反应发生在叶绿体的哪个部位？产物是什么？",
            question_type=DiagnosticQuestionType.EXPLANATION,
            evidence_refs=[],
        ),
    ]
    run = learning_service.create_diagnostic_run(
        account_id, mission.mission_id, questions=questions
    )
    learning_service.record_answer(
        account_id,
        run.run_id,
        DiagnosticAnswerCreateRequest(
            question_id=run.questions[0].question_id,
            response_text="类囊体膜；产物是 ATP 和 NADPH。",
            evaluated_state=AnswerEvaluatedState.CORRECT,
            evaluator="rule",
            evaluation_reason="回答包含关键位置与产物。",
        ),
    )
    learning_service.complete_diagnostic(account_id, run.run_id)
    plan = learning_service.compile_teaching_plan(account_id, mission.mission_id)
    return mission.mission_id, plan.plan_id


class TestShortLessonGeneration:
    def test_short_lesson_focuses_on_single_learning_victory(
        self,
        learning_service: LearningService,
        teaching_service: TeachingService,
        alice_id: str,
    ) -> None:
        _mission_id, plan_id = _create_plan(learning_service, alice_id)
        bundle = _evidence_bundle(
            evidence_refs=[
                EvidenceRef(
                    claim_id="claim-light",
                    evidence_id="ev-light",
                    source_id="source-bio",
                    document_id="doc-v1",
                    reason="教材中关于光反应位置与产物的 Claim。",
                ),
            ],
            fact_locks=[_sample_fact_lock("lock-light")],
        )

        lesson = teaching_service.generate_short_lesson(
            alice_id, plan_id, request=GenerateLessonRequest(evidence_bundle=bundle)
        )

        assert len(lesson.target_concepts) == 1
        assert lesson.target_concepts[0] == "光反应"
        assert lesson.learning_objective
        assert all(e.concept_id == "光反应" for e in lesson.exercises)
        assert lesson.owner_account_id == alice_id

    def test_explanations_examples_and_exercises_bind_evidence_and_fact_locks(
        self,
        learning_service: LearningService,
        teaching_service: TeachingService,
        alice_id: str,
    ) -> None:
        _mission_id, plan_id = _create_plan(learning_service, alice_id)
        evidence_ref = EvidenceRef(
            claim_id="claim-light",
            evidence_id="ev-light",
            source_id="source-bio",
            document_id="doc-v1",
            reason="教材中关于光反应位置与产物的 Claim。",
        )
        fact_lock = _sample_fact_lock("lock-light")
        bundle = _evidence_bundle(
            evidence_refs=[evidence_ref],
            fact_locks=[fact_lock],
        )

        lesson = teaching_service.generate_short_lesson(
            alice_id, plan_id, request=GenerateLessonRequest(evidence_bundle=bundle)
        )

        assert lesson.fact_lock_set_id == bundle.graph_id
        assert fact_lock.lock_id in lesson.fact_lock_ids
        assert lesson.explanation
        assert any(evidence_ref.reason in ref.reason for ref in lesson.evidence_refs)

        example = lesson.examples[0]
        assert example.evidence_refs
        assert fact_lock.lock_id in example.fact_lock_ids

        for exercise in lesson.exercises:
            assert exercise.evidence_refs
            assert fact_lock.lock_id in exercise.fact_lock_ids

    def test_exercises_are_retrieval_not_browsing(
        self,
        learning_service: LearningService,
        teaching_service: TeachingService,
        alice_id: str,
    ) -> None:
        _mission_id, plan_id = _create_plan(learning_service, alice_id)
        bundle = _evidence_bundle(
            evidence_refs=[EvidenceRef(reason="证据绑定练习。")],
            fact_locks=[_sample_fact_lock("lock-1")],
        )

        lesson = teaching_service.generate_short_lesson(
            alice_id, plan_id, request=GenerateLessonRequest(evidence_bundle=bundle)
        )

        assert lesson.exercises
        allowed_types = set(RetrievalExerciseType)
        generated_types = {e.exercise_type for e in lesson.exercises}
        assert RetrievalExerciseType.RECALL in generated_types
        assert RetrievalExerciseType.EXPLANATION in generated_types
        assert RetrievalExerciseType.COMPUTATION in generated_types
        assert RetrievalExerciseType.COMPARISON in generated_types
        assert RetrievalExerciseType.APPLICATION in generated_types
        for exercise in lesson.exercises:
            assert exercise.exercise_type in allowed_types
            assert exercise.exercise_type.value not in ("browsing", "completion")

    def test_missing_evidence_blocks_lesson(
        self,
        learning_service: LearningService,
        teaching_service: TeachingService,
        alice_id: str,
    ) -> None:
        _mission_id, plan_id = _create_plan(learning_service, alice_id)
        bundle = _evidence_bundle(fact_locks=[_sample_fact_lock("lock-1")])

        lesson = teaching_service.generate_short_lesson(
            alice_id, plan_id, request=GenerateLessonRequest(evidence_bundle=bundle)
        )

        assert lesson.status == LessonStatus.BLOCKED
        assert not lesson.quality_gate.passed
        assert (
            TeachingQualityGateCheck.EVIDENCE_BOUND in lesson.quality_gate.failed_checks
        )

    def test_high_risk_topic_requires_human_gate(
        self,
        learning_service: LearningService,
        teaching_service: TeachingService,
        alice_id: str,
    ) -> None:
        mission = learning_service.create_mission(
            alice_id,
            LearningMissionCreateRequest(
                title="医学诊断相关学习",
                goal="理解临床诊断的基本流程和处方原则。",
                scope_concepts=["诊断流程", "处方"],
                constraints=[],
                success_criteria=["能说明诊断流程"],
            ),
        )
        questions = [
            DiagnosticQuestionCreateRequest(
                concept_id="诊断流程",
                question_text="请说明诊断流程。",
                question_type=DiagnosticQuestionType.EXPLANATION,
                evidence_refs=[],
            ),
        ]
        run = learning_service.create_diagnostic_run(
            alice_id, mission.mission_id, questions=questions
        )
        learning_service.record_answer(
            alice_id,
            run.run_id,
            DiagnosticAnswerCreateRequest(
                question_id=run.questions[0].question_id,
                response_text="回答",
                evaluated_state=AnswerEvaluatedState.CORRECT,
                evaluator="rule",
                evaluation_reason="通过",
            ),
        )
        learning_service.complete_diagnostic(alice_id, run.run_id)
        plan = learning_service.compile_teaching_plan(alice_id, mission.mission_id)

        bundle = _evidence_bundle(
            evidence_refs=[EvidenceRef(reason="医学教材证据。")],
            fact_locks=[_sample_fact_lock("lock-med")],
        )
        lesson = teaching_service.generate_short_lesson(
            alice_id,
            plan.plan_id,
            request=GenerateLessonRequest(evidence_bundle=bundle),
        )

        assert lesson.human_gate_required is True
        assert lesson.status == LessonStatus.DEGRADED
        assert (
            TeachingQualityGateCheck.NO_HIGH_RISK_WITHOUT_HUMAN_GATE
            in lesson.quality_gate.failed_checks
        )


class TestExerciseAttemptFeedback:
    def test_correct_response_gets_positive_feedback(
        self,
        learning_service: LearningService,
        teaching_service: TeachingService,
        alice_id: str,
    ) -> None:
        _mission_id, plan_id = _create_plan(learning_service, alice_id)
        bundle = _evidence_bundle(
            evidence_refs=[EvidenceRef(reason="证据。")],
            fact_locks=[_sample_fact_lock("lock-1")],
        )
        lesson = teaching_service.generate_short_lesson(
            alice_id, plan_id, request=GenerateLessonRequest(evidence_bundle=bundle)
        )
        exercise = lesson.exercises[0]

        attempt = teaching_service.submit_exercise_attempt(
            alice_id,
            lesson.lesson_id,
            exercise.exercise_id,
            response_text=(
                "光反应的定义是在类囊体膜上进行光合作用，"
                "核心内容是吸收光能并产生 ATP 和 NADPH。"
            ),
        )

        assert attempt.evaluated_state == AnswerEvaluatedState.CORRECT
        assert attempt.feedback.is_correct is True
        assert attempt.feedback.explanation
        assert attempt.feedback.evidence_refs

    def test_incorrect_response_gets_remedial_feedback(
        self,
        learning_service: LearningService,
        teaching_service: TeachingService,
        alice_id: str,
    ) -> None:
        _mission_id, plan_id = _create_plan(learning_service, alice_id)
        bundle = _evidence_bundle(
            evidence_refs=[EvidenceRef(reason="证据。")],
            fact_locks=[_sample_fact_lock("lock-1")],
        )
        lesson = teaching_service.generate_short_lesson(
            alice_id, plan_id, request=GenerateLessonRequest(evidence_bundle=bundle)
        )
        exercise = lesson.exercises[0]

        attempt = teaching_service.submit_exercise_attempt(
            alice_id,
            lesson.lesson_id,
            exercise.exercise_id,
            response_text="我不知道。",
        )

        assert attempt.evaluated_state == AnswerEvaluatedState.INCORRECT
        assert attempt.feedback.is_correct is False
        assert attempt.feedback.next_step


class TestIsolation:
    def test_user_cannot_see_another_users_lesson(
        self,
        learning_service: LearningService,
        teaching_service: TeachingService,
        alice_id: str,
        bob_id: str,
    ) -> None:
        _mission_id, plan_id = _create_plan(learning_service, alice_id)
        bundle = _evidence_bundle(
            evidence_refs=[EvidenceRef(reason="证据。")],
            fact_locks=[_sample_fact_lock("lock-1")],
        )
        lesson = teaching_service.generate_short_lesson(
            alice_id, plan_id, request=GenerateLessonRequest(evidence_bundle=bundle)
        )

        with pytest.raises(LearningError):
            teaching_service.get_lesson(bob_id, lesson.lesson_id)

    def test_user_cannot_attempt_another_users_exercise(
        self,
        learning_service: LearningService,
        teaching_service: TeachingService,
        alice_id: str,
        bob_id: str,
    ) -> None:
        _mission_id, plan_id = _create_plan(learning_service, alice_id)
        bundle = _evidence_bundle(
            evidence_refs=[EvidenceRef(reason="证据。")],
            fact_locks=[_sample_fact_lock("lock-1")],
        )
        lesson = teaching_service.generate_short_lesson(
            alice_id, plan_id, request=GenerateLessonRequest(evidence_bundle=bundle)
        )
        exercise = lesson.exercises[0]

        with pytest.raises(LearningError):
            teaching_service.submit_exercise_attempt(
                bob_id,
                lesson.lesson_id,
                exercise.exercise_id,
                response_text="回答",
            )
