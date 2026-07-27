"""Learning mission and diagnosis service tests.

The seam under test: an authenticated user can define a learning mission, run
an evidence-backed diagnostic, receive explainable knowledge states and ZPD,
and correct the diagnosis without silent model overrides. Browsing and
completion never count as mastery evidence.
"""

from __future__ import annotations

import pytest

from science_companion.contracts.learning import (
    AnswerEvaluatedState,
    DiagnosticAnswerCreateRequest,
    DiagnosticQuestionCreateRequest,
    DiagnosticQuestionType,
    EvidenceRef,
    KnowledgeStateCorrection,
    KnowledgeStateStatus,
    LearningActivityType,
    LearningMissionCreateRequest,
)
from science_companion.learning import InMemoryLearningRepository, LearningService


@pytest.fixture
def repository() -> InMemoryLearningRepository:
    return InMemoryLearningRepository()


@pytest.fixture
def service(repository: InMemoryLearningRepository) -> LearningService:
    return LearningService(repository)


@pytest.fixture
def alice_id() -> str:
    return "account-alice"


@pytest.fixture
def bob_id() -> str:
    return "account-bob"


def _mission_request() -> LearningMissionCreateRequest:
    return LearningMissionCreateRequest(
        title="理解光合作用",
        goal="能解释光合作用的核心步骤，并将其应用于不同情境。",
        scope_concepts=["光反应", "暗反应", "叶绿素作用", "能量转换"],
        constraints=["只讨论植物细胞", "不涉及C4路径细节"],
        success_criteria=[
            "能画出光反应与暗反应的输入输出",
            "能解释为什么缺光会抑制暗反应",
        ],
    )


def _question(
    concept: str, text: str, evidence: list[EvidenceRef]
) -> DiagnosticQuestionCreateRequest:
    return DiagnosticQuestionCreateRequest(
        concept_id=concept,
        question_text=text,
        question_type=DiagnosticQuestionType.EXPLANATION,
        evidence_refs=evidence,
    )


def _answer(
    question_id: str, text: str, state: AnswerEvaluatedState, reason: str
) -> DiagnosticAnswerCreateRequest:
    return DiagnosticAnswerCreateRequest(
        question_id=question_id,
        response_text=text,
        evaluated_state=state,
        evaluator="rule",
        evaluation_reason=reason,
    )


class TestLearningMission:
    def test_mission_records_goal_scope_constraints_and_success_criteria(
        self, service: LearningService, alice_id: str
    ) -> None:
        request = _mission_request()
        mission = service.create_mission(alice_id, request)

        assert mission.title == request.title
        assert mission.goal == request.goal
        assert mission.scope_concepts == request.scope_concepts
        assert mission.constraints == request.constraints
        assert mission.success_criteria == request.success_criteria
        assert mission.owner_account_id == alice_id
        assert mission.version == 1

        fetched = service.get_mission(alice_id, mission.mission_id)
        assert fetched.mission_id == mission.mission_id


class TestEvidenceBackedDiagnosis:
    def test_diagnostic_questions_and_results_bind_to_evidence_and_answers(
        self, service: LearningService, alice_id: str
    ) -> None:
        mission = service.create_mission(alice_id, _mission_request())
        evidence = EvidenceRef(
            claim_id="claim-photosynthesis-1",
            evidence_id="ev-1",
            source_id="source-bio-textbook",
            document_id="doc-v1",
            reason="教材中关于光反应位置与产物的 Claim。",
        )
        questions = [
            _question("光反应", "光反应发生在叶绿体的哪个部位？产物是什么？", [evidence]),
        ]
        run = service.create_diagnostic_run(
            alice_id, mission.mission_id, questions=questions
        )

        assert run.questions[0].evidence_refs == [evidence]

        answer = service.record_answer(
            alice_id,
            run.run_id,
            _answer(
                run.questions[0].question_id,
                "类囊体膜；产物是 ATP 和 NADPH。",
                AnswerEvaluatedState.CORRECT,
                "回答包含关键位置与产物，与证据一致。",
            ),
        )
        assert answer.evidence_refs == []
        assert answer.evaluated_state == AnswerEvaluatedState.CORRECT

        result = service.complete_diagnostic(alice_id, run.run_id)
        assert result.mission_id == mission.mission_id
        assert len(result.knowledge_states) == len(mission.scope_concepts)
        state = next(s for s in result.knowledge_states if s.concept_id == "光反应")
        assert state.status == KnowledgeStateStatus.SUPPORTED
        assert answer.answer_id in state.supporting_record_ids
        explanation = result.evidence_based_explanation
        assert "证据" in explanation or "回答" in explanation

    def test_application_answer_produces_robust_state(
        self, service: LearningService, alice_id: str
    ) -> None:
        mission = service.create_mission(alice_id, _mission_request())
        questions = [
            DiagnosticQuestionCreateRequest(
                concept_id="光反应",
                question_text="如果连续阴天，暗反应会怎样变化？请用光反应产物解释。",
                question_type=DiagnosticQuestionType.APPLICATION,
                evidence_refs=[],
            ),
        ]
        run = service.create_diagnostic_run(
            alice_id, mission.mission_id, questions=questions
        )
        service.record_answer(
            alice_id,
            run.run_id,
            _answer(
                run.questions[0].question_id,
                "ATP 和 NADPH 减少，暗反应中 C3 还原减慢。",
                AnswerEvaluatedState.CORRECT,
                "正确迁移了光反应产物与暗反应的关系。",
            ),
        )

        result = service.complete_diagnostic(alice_id, run.run_id)
        state = next(s for s in result.knowledge_states if s.concept_id == "光反应")
        assert state.status == KnowledgeStateStatus.ROBUST

    def test_incorrect_answer_limits_state_to_emerging(
        self, service: LearningService, alice_id: str
    ) -> None:
        mission = service.create_mission(alice_id, _mission_request())
        questions = [
            _question("光反应", "光反应发生在叶绿体的哪个部位？", []),
        ]
        run = service.create_diagnostic_run(
            alice_id, mission.mission_id, questions=questions
        )
        service.record_answer(
            alice_id,
            run.run_id,
            _answer(
                run.questions[0].question_id,
                "细胞质基质",
                AnswerEvaluatedState.INCORRECT,
                "位置错误。",
            ),
        )

        result = service.complete_diagnostic(alice_id, run.run_id)
        state = next(s for s in result.knowledge_states if s.concept_id == "光反应")
        assert state.status == KnowledgeStateStatus.EMERGING


class TestNoMasteryWithoutEvidence:
    def test_unanswered_concepts_remain_unknown(
        self, service: LearningService, alice_id: str
    ) -> None:
        mission = service.create_mission(alice_id, _mission_request())
        run = service.create_diagnostic_run(alice_id, mission.mission_id)

        result = service.complete_diagnostic(alice_id, run.run_id)
        assert all(s.status == KnowledgeStateStatus.UNKNOWN for s in result.knowledge_states)

    def test_browsing_and_completion_do_not_update_knowledge_state(
        self, service: LearningService, alice_id: str
    ) -> None:
        mission = service.create_mission(alice_id, _mission_request())
        service.record_activity(
            alice_id,
            mission.mission_id,
            LearningActivityType.BROWSING,
            concept_id="光反应",
            details={"duration_seconds": 120},
        )
        service.record_activity(
            alice_id,
            mission.mission_id,
            LearningActivityType.COMPLETION,
            concept_id="光反应",
            details={"lesson_id": "lesson-1"},
        )

        run = service.create_diagnostic_run(alice_id, mission.mission_id)
        result = service.complete_diagnostic(alice_id, run.run_id)
        state = next(s for s in result.knowledge_states if s.concept_id == "光反应")
        assert state.status == KnowledgeStateStatus.UNKNOWN


class TestUserCorrection:
    def test_user_can_correct_diagnosis_and_see_next_validation_task(
        self, service: LearningService, alice_id: str
    ) -> None:
        mission = service.create_mission(alice_id, _mission_request())
        questions = [_question("光反应", "光反应部位？", [])]
        run = service.create_diagnostic_run(
            alice_id, mission.mission_id, questions=questions
        )
        service.record_answer(
            alice_id,
            run.run_id,
            _answer(
                run.questions[0].question_id,
                "细胞质基质",
                AnswerEvaluatedState.INCORRECT,
                "错误",
            ),
        )
        service.complete_diagnostic(alice_id, run.run_id)

        corrected = service.correct_knowledge_state(
            alice_id,
            mission.mission_id,
            KnowledgeStateCorrection(
                concept_id="光反应",
                corrected_state=KnowledgeStateStatus.SUPPORTED,
                reason="我已经自学并理解了类囊体膜上的光反应。",
            ),
        )

        assert corrected.status == KnowledgeStateStatus.SUPPORTED
        assert corrected.version == 2
        assert "验证" in corrected.next_validation_task
        assert "用户纠正" in (corrected.uncertainty_reason or "")

        current = service.get_knowledge_state(alice_id, mission.mission_id, "光反应")
        assert current.state_id == corrected.state_id


class TestDifferentUsersDifferentZPD:
    def test_two_users_same_mission_get_different_zpd_explanations(
        self,
        service: LearningService,
        alice_id: str,
        bob_id: str,
    ) -> None:
        request = _mission_request()
        alice_mission = service.create_mission(alice_id, request)
        bob_mission = service.create_mission(bob_id, request)

        questions = [
            _question("光反应", "光反应部位？", []),
            _question("暗反应", "暗反应是否需要光？", []),
        ]
        alice_run = service.create_diagnostic_run(
            alice_id, alice_mission.mission_id, questions=questions
        )
        bob_run = service.create_diagnostic_run(
            bob_id, bob_mission.mission_id, questions=questions
        )

        # Alice answers both correctly.
        for run in (alice_run,):
            for q in run.questions:
                service.record_answer(
                    alice_id,
                    run.run_id,
                    _answer(q.question_id, "正确", AnswerEvaluatedState.CORRECT, "正确"),
                )
        # Bob answers both incorrectly.
        for q in bob_run.questions:
            service.record_answer(
                bob_id,
                bob_run.run_id,
                _answer(q.question_id, "错误", AnswerEvaluatedState.INCORRECT, "错误"),
            )

        alice_result = service.complete_diagnostic(alice_id, alice_run.run_id)
        bob_result = service.complete_diagnostic(bob_id, bob_run.run_id)

        # Both should have evidence-based explanations grounded in answers.
        assert "回答" in alice_result.evidence_based_explanation
        assert "回答" in bob_result.evidence_based_explanation

        # The same topic produces different evidence-based states for the two users.
        alice_light = next(
            s for s in alice_result.knowledge_states if s.concept_id == "光反应"
        )
        bob_light = next(
            s for s in bob_result.knowledge_states if s.concept_id == "光反应"
        )
        assert alice_light.status != bob_light.status
        assert alice_light.supporting_record_ids
        assert bob_light.refuting_record_ids


class TestTeachingPlan:
    def test_teaching_plan_traces_to_mission_and_zpd_states(
        self, service: LearningService, alice_id: str
    ) -> None:
        mission = service.create_mission(alice_id, _mission_request())
        questions = [_question("光反应", "光反应部位？", [])]
        run = service.create_diagnostic_run(
            alice_id, mission.mission_id, questions=questions
        )
        service.record_answer(
            alice_id,
            run.run_id,
            _answer(
                run.questions[0].question_id,
                "正确",
                AnswerEvaluatedState.CORRECT,
                "正确",
            ),
        )
        service.complete_diagnostic(alice_id, run.run_id)

        plan = service.compile_teaching_plan(alice_id, mission.mission_id)
        assert plan.mission_id == mission.mission_id
        assert plan.owner_account_id == alice_id
        assert "光反应" in plan.target_concepts
        assert plan.status.value == "draft"
