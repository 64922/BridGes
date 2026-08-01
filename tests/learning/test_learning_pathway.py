"""Learning evidence, knowledge-state proposals and learning-path tests (T023).

The seam under test: after a user completes, corrects or only browses learning
materials, only the first two kinds of qualified evidence can propose knowledge-
state and path updates. Users confirm important changes; rejections cannot be
bypassed by the model. Different users form explainable different paths.
"""

from __future__ import annotations

import pytest

from bridges.contracts.learning import (
    AnswerEvaluatedState,
    DecideKnowledgeStateProposalRequest,
    HumanDecisionType,
    KnowledgeConfidence,
    KnowledgeStateProposalStatus,
    KnowledgeStateStatus,
    LearningActivityType,
    LearningMissionCreateRequest,
    LearningPathNodeStatus,
    LearningRecordCreateRequest,
    LearningRecordSource,
    LearningRecordType,
    ProposeKnowledgeStateUpdateRequest,
)
from bridges.learning import (
    InMemoryLearningRepository,
    LearningPathService,
    LearningService,
)


@pytest.fixture
def repository() -> InMemoryLearningRepository:
    return InMemoryLearningRepository()


@pytest.fixture
def learning_service(repository: InMemoryLearningRepository) -> LearningService:
    return LearningService(repository)


@pytest.fixture
def pathway_service(repository: InMemoryLearningRepository) -> LearningPathService:
    return LearningPathService(repository)


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
        scope_concepts=["光反应", "暗反应"],
        constraints=[],
        success_criteria=["能说明光反应产物如何驱动暗反应"],
    )


def _complete_diagnostic(
    learning_service: LearningService,
    account_id: str,
    mission_id: str,
    state: AnswerEvaluatedState,
) -> None:
    from bridges.contracts.learning import (
        DiagnosticAnswerCreateRequest,
        DiagnosticQuestionCreateRequest,
        DiagnosticQuestionType,
    )

    questions = [
        DiagnosticQuestionCreateRequest(
            concept_id="光反应",
            question_text="光反应发生在叶绿体的哪个部位？产物是什么？",
            question_type=DiagnosticQuestionType.EXPLANATION,
            evidence_refs=[],
        ),
    ]
    run = learning_service.create_diagnostic_run(
        account_id, mission_id, questions=questions
    )
    learning_service.record_answer(
        account_id,
        run.run_id,
        DiagnosticAnswerCreateRequest(
            question_id=run.questions[0].question_id,
            response_text="类囊体膜；产物是 ATP 和 NADPH。",
            evaluated_state=state,
            evaluator="rule",
            evaluation_reason="测试评估。",
        ),
    )
    learning_service.complete_diagnostic(account_id, run.run_id)


class TestLearningRecordEvidence:
    def test_learning_record_references_concrete_attempt_and_evidence(
        self,
        pathway_service: LearningPathService,
        learning_service: LearningService,
        alice_id: str,
    ) -> None:
        mission = learning_service.create_mission(alice_id, _mission_request())
        record = pathway_service.record_learning_record(
            alice_id,
            mission.mission_id,
            LearningRecordCreateRequest(
                concept_id="光反应",
                record_type=LearningRecordType.EXERCISE_ATTEMPT,
                source_type=LearningRecordSource.EXERCISE_ATTEMPT,
                source_id="attempt-123",
                response_text="光反应在类囊体膜上进行。",
                evaluated_state=AnswerEvaluatedState.CORRECT,
                record_reason="用户正确完成检索练习，覆盖关键要点。",
            ),
        )

        assert record.concept_id == "光反应"
        assert record.source_id == "attempt-123"
        assert record.record_type == LearningRecordType.EXERCISE_ATTEMPT
        assert record.evaluated_state == AnswerEvaluatedState.CORRECT
        assert record.record_reason

    def test_browsing_and_completion_do_not_create_learning_records(
        self,
        pathway_service: LearningPathService,
        learning_service: LearningService,
        alice_id: str,
    ) -> None:
        mission = learning_service.create_mission(alice_id, _mission_request())
        learning_service.record_activity(
            alice_id,
            mission.mission_id,
            LearningActivityType.BROWSING,
            concept_id="光反应",
            details={"duration_seconds": 120},
        )
        learning_service.record_activity(
            alice_id,
            mission.mission_id,
            LearningActivityType.COMPLETION,
            concept_id="光反应",
            details={"lesson_id": "lesson-1"},
        )

        records = pathway_service.list_learning_records(
            alice_id, mission.mission_id, "光反应"
        )
        assert records == []

    def test_misconception_correction_creates_qualified_learning_record(
        self,
        pathway_service: LearningPathService,
        learning_service: LearningService,
        alice_id: str,
    ) -> None:
        """纠正误区是合格证据，能创建学习记录并用于知识状态提议。

        测试接缝：用户完成、纠错或仅浏览三种学习行为中，
        纠错是合格证据，可与练习完成一样提出知识状态更新。
        """
        mission = learning_service.create_mission(alice_id, _mission_request())
        record = pathway_service.record_learning_record(
            alice_id,
            mission.mission_id,
            LearningRecordCreateRequest(
                concept_id="光反应",
                record_type=LearningRecordType.MISCONCEPTION_CORRECTION,
                source_type=LearningRecordSource.USER_CORRECTION,
                source_id="correction-1",
                response_text="光反应确实发生在类囊体膜，我之前的理解有误。",
                evaluated_state=AnswerEvaluatedState.CORRECT,
                misconception_corrected="误以为光反应在叶绿体基质中进行",
                record_reason="用户纠正了关于光反应位置的误解，提供了正确理解。",
            ),
        )

        assert record.record_type == LearningRecordType.MISCONCEPTION_CORRECTION
        assert record.misconception_corrected is not None
        assert record.evaluated_state == AnswerEvaluatedState.CORRECT

        # 纠错记录也应能提出知识状态提议
        proposal = pathway_service.propose_knowledge_state_update(
            alice_id,
            mission.mission_id,
            ProposeKnowledgeStateUpdateRequest(concept_id="光反应"),
        )
        assert proposal.proposed_status in {
            KnowledgeStateStatus.SUPPORTED,
            KnowledgeStateStatus.ROBUST,
        }
        assert proposal.status == KnowledgeStateProposalStatus.PENDING


class TestKnowledgeStateProposal:
    def test_exercise_attempt_proposes_supported_state(
        self,
        pathway_service: LearningPathService,
        learning_service: LearningService,
        alice_id: str,
    ) -> None:
        mission = learning_service.create_mission(alice_id, _mission_request())
        _complete_diagnostic(
            learning_service, alice_id, mission.mission_id, AnswerEvaluatedState.CORRECT
        )

        record = pathway_service.record_learning_record(
            alice_id,
            mission.mission_id,
            LearningRecordCreateRequest(
                concept_id="光反应",
                record_type=LearningRecordType.EXERCISE_ATTEMPT,
                source_type=LearningRecordSource.EXERCISE_ATTEMPT,
                source_id="attempt-1",
                response_text="类囊体膜；产物是 ATP 和 NADPH。",
                evaluated_state=AnswerEvaluatedState.CORRECT,
                record_reason="正确完成检索练习。",
            ),
        )

        proposal = pathway_service.propose_knowledge_state_update(
            alice_id,
            mission.mission_id,
            ProposeKnowledgeStateUpdateRequest(concept_id="光反应"),
        )

        assert proposal.concept_id == "光反应"
        assert proposal.proposed_status == KnowledgeStateStatus.SUPPORTED
        assert proposal.status == KnowledgeStateProposalStatus.PENDING
        assert record.record_id in proposal.supporting_record_ids
        assert proposal.uncertainty_reason
        assert proposal.next_validation_task

    def test_incorrect_attempt_keeps_state_emerging(
        self,
        pathway_service: LearningPathService,
        learning_service: LearningService,
        alice_id: str,
    ) -> None:
        mission = learning_service.create_mission(alice_id, _mission_request())
        _complete_diagnostic(
            learning_service, alice_id, mission.mission_id, AnswerEvaluatedState.CORRECT
        )

        pathway_service.record_learning_record(
            alice_id,
            mission.mission_id,
            LearningRecordCreateRequest(
                concept_id="光反应",
                record_type=LearningRecordType.EXERCISE_ATTEMPT,
                source_type=LearningRecordSource.EXERCISE_ATTEMPT,
                source_id="attempt-bad",
                response_text="细胞质基质。",
                evaluated_state=AnswerEvaluatedState.INCORRECT,
                record_reason="检索练习回答错误。",
            ),
        )

        proposal = pathway_service.propose_knowledge_state_update(
            alice_id,
            mission.mission_id,
            ProposeKnowledgeStateUpdateRequest(concept_id="光反应"),
        )

        assert proposal.proposed_status == KnowledgeStateStatus.EMERGING

    def test_transfer_evidence_proposes_robust_state(
        self,
        pathway_service: LearningPathService,
        learning_service: LearningService,
        alice_id: str,
    ) -> None:
        mission = learning_service.create_mission(alice_id, _mission_request())
        _complete_diagnostic(
            learning_service, alice_id, mission.mission_id, AnswerEvaluatedState.CORRECT
        )

        pathway_service.record_learning_record(
            alice_id,
            mission.mission_id,
            LearningRecordCreateRequest(
                concept_id="光反应",
                record_type=LearningRecordType.TRANSFER_TASK,
                source_type=LearningRecordSource.EXERCISE_ATTEMPT,
                source_id="transfer-1",
                response_text="在新情境中正确应用了光反应产物。",
                evaluated_state=AnswerEvaluatedState.CORRECT,
                record_reason="迁移任务正确，支持 robust 状态。",
            ),
        )

        proposal = pathway_service.propose_knowledge_state_update(
            alice_id,
            mission.mission_id,
            ProposeKnowledgeStateUpdateRequest(concept_id="光反应"),
        )

        assert proposal.proposed_status == KnowledgeStateStatus.ROBUST

    def test_cannot_propose_without_learning_records(
        self,
        pathway_service: LearningPathService,
        learning_service: LearningService,
        alice_id: str,
    ) -> None:
        mission = learning_service.create_mission(alice_id, _mission_request())

        from bridges.learning import LearningError

        with pytest.raises(LearningError):
            pathway_service.propose_knowledge_state_update(
                alice_id,
                mission.mission_id,
                ProposeKnowledgeStateUpdateRequest(concept_id="光反应"),
            )


class TestHumanDecision:
    def test_accepted_proposal_updates_knowledge_state_and_path(
        self,
        pathway_service: LearningPathService,
        learning_service: LearningService,
        alice_id: str,
    ) -> None:
        mission = learning_service.create_mission(alice_id, _mission_request())
        _complete_diagnostic(
            learning_service, alice_id, mission.mission_id, AnswerEvaluatedState.CORRECT
        )
        initial = learning_service.get_knowledge_state(
            alice_id, mission.mission_id, "光反应"
        )

        pathway_service.record_learning_record(
            alice_id,
            mission.mission_id,
            LearningRecordCreateRequest(
                concept_id="光反应",
                record_type=LearningRecordType.EXERCISE_ATTEMPT,
                source_type=LearningRecordSource.EXERCISE_ATTEMPT,
                source_id="attempt-1",
                response_text="正确。",
                evaluated_state=AnswerEvaluatedState.CORRECT,
                record_reason="正确完成检索练习。",
            ),
        )
        proposal = pathway_service.propose_knowledge_state_update(
            alice_id,
            mission.mission_id,
            ProposeKnowledgeStateUpdateRequest(concept_id="光反应"),
        )

        decided = pathway_service.decide_knowledge_state_proposal(
            alice_id,
            proposal.proposal_id,
            DecideKnowledgeStateProposalRequest(
                decision=HumanDecisionType.ACCEPT,
                reason="我同意这个判断。",
            ),
        )

        assert decided.status == KnowledgeStateProposalStatus.ACCEPTED
        assert decided.decision is not None
        assert decided.decision.decision == HumanDecisionType.ACCEPT

        updated = learning_service.get_knowledge_state(
            alice_id, mission.mission_id, "光反应"
        )
        assert updated.state_id == proposal.proposal_id
        assert updated.status == KnowledgeStateStatus.SUPPORTED
        assert updated.version > initial.version

        path = pathway_service.get_learning_path(alice_id, mission.mission_id)
        assert path.mission_id == mission.mission_id
        assert any(n.concept_id == "光反应" for n in path.nodes)

    def test_rejected_proposal_is_not_bypassed(
        self,
        pathway_service: LearningPathService,
        learning_service: LearningService,
        alice_id: str,
    ) -> None:
        mission = learning_service.create_mission(alice_id, _mission_request())
        _complete_diagnostic(
            learning_service, alice_id, mission.mission_id, AnswerEvaluatedState.CORRECT
        )
        initial = learning_service.get_knowledge_state(
            alice_id, mission.mission_id, "光反应"
        )

        pathway_service.record_learning_record(
            alice_id,
            mission.mission_id,
            LearningRecordCreateRequest(
                concept_id="光反应",
                record_type=LearningRecordType.EXERCISE_ATTEMPT,
                source_type=LearningRecordSource.EXERCISE_ATTEMPT,
                source_id="attempt-1",
                response_text="正确。",
                evaluated_state=AnswerEvaluatedState.CORRECT,
                record_reason="正确完成检索练习。",
            ),
        )
        proposal = pathway_service.propose_knowledge_state_update(
            alice_id,
            mission.mission_id,
            ProposeKnowledgeStateUpdateRequest(concept_id="光反应"),
        )

        decided = pathway_service.decide_knowledge_state_proposal(
            alice_id,
            proposal.proposal_id,
            DecideKnowledgeStateProposalRequest(
                decision=HumanDecisionType.REJECT,
                reason="我觉得还需要更多证据。",
            ),
        )

        assert decided.status == KnowledgeStateProposalStatus.REJECTED

        current = learning_service.get_knowledge_state(
            alice_id, mission.mission_id, "光反应"
        )
        assert current.state_id == initial.state_id
        assert current.status == initial.status

    def test_modify_proposal_updates_to_user_status(
        self,
        pathway_service: LearningPathService,
        learning_service: LearningService,
        alice_id: str,
    ) -> None:
        mission = learning_service.create_mission(alice_id, _mission_request())
        _complete_diagnostic(
            learning_service, alice_id, mission.mission_id, AnswerEvaluatedState.CORRECT
        )

        pathway_service.record_learning_record(
            alice_id,
            mission.mission_id,
            LearningRecordCreateRequest(
                concept_id="光反应",
                record_type=LearningRecordType.EXERCISE_ATTEMPT,
                source_type=LearningRecordSource.EXERCISE_ATTEMPT,
                source_id="attempt-1",
                response_text="正确。",
                evaluated_state=AnswerEvaluatedState.CORRECT,
                record_reason="正确完成检索练习。",
            ),
        )
        proposal = pathway_service.propose_knowledge_state_update(
            alice_id,
            mission.mission_id,
            ProposeKnowledgeStateUpdateRequest(concept_id="光反应"),
        )

        decided = pathway_service.decide_knowledge_state_proposal(
            alice_id,
            proposal.proposal_id,
            DecideKnowledgeStateProposalRequest(
                decision=HumanDecisionType.MODIFY,
                reason="我认为目前只是 emerging。",
                modified_status=KnowledgeStateStatus.EMERGING,
                modified_confidence=KnowledgeConfidence.LOW,
            ),
        )

        assert decided.status == KnowledgeStateProposalStatus.ACCEPTED
        updated = learning_service.get_knowledge_state(
            alice_id, mission.mission_id, "光反应"
        )
        assert updated.status == KnowledgeStateStatus.EMERGING


class TestLearningPath:
    def test_path_traces_nodes_to_confirmed_states_and_records(
        self,
        pathway_service: LearningPathService,
        learning_service: LearningService,
        alice_id: str,
    ) -> None:
        mission = learning_service.create_mission(alice_id, _mission_request())
        _complete_diagnostic(
            learning_service, alice_id, mission.mission_id, AnswerEvaluatedState.CORRECT
        )
        pathway_service.record_learning_record(
            alice_id,
            mission.mission_id,
            LearningRecordCreateRequest(
                concept_id="光反应",
                record_type=LearningRecordType.TRANSFER_TASK,
                source_type=LearningRecordSource.EXERCISE_ATTEMPT,
                source_id="transfer-1",
                response_text="在新情境中正确应用。",
                evaluated_state=AnswerEvaluatedState.CORRECT,
                record_reason="迁移任务正确。",
            ),
        )
        proposal = pathway_service.propose_knowledge_state_update(
            alice_id,
            mission.mission_id,
            ProposeKnowledgeStateUpdateRequest(concept_id="光反应"),
        )
        pathway_service.decide_knowledge_state_proposal(
            alice_id,
            proposal.proposal_id,
            DecideKnowledgeStateProposalRequest(
                decision=HumanDecisionType.ACCEPT,
                reason="同意。",
            ),
        )

        path = pathway_service.get_learning_path(alice_id, mission.mission_id)
        light_node = next(n for n in path.nodes if n.concept_id == "光反应")
        assert light_node.evidence_record_ids
        assert light_node.status == LearningPathNodeStatus.COMPLETED

        dark_node = next(n for n in path.nodes if n.concept_id == "暗反应")
        assert dark_node.status == LearningPathNodeStatus.PENDING

    def test_different_users_form_different_paths(
        self,
        pathway_service: LearningPathService,
        learning_service: LearningService,
        alice_id: str,
        bob_id: str,
    ) -> None:
        alice_mission = learning_service.create_mission(alice_id, _mission_request())
        bob_mission = learning_service.create_mission(bob_id, _mission_request())

        _complete_diagnostic(
            learning_service, alice_id, alice_mission.mission_id, AnswerEvaluatedState.CORRECT
        )
        _complete_diagnostic(
            learning_service, bob_id, bob_mission.mission_id, AnswerEvaluatedState.INCORRECT
        )

        # Alice supplies a transfer task -> robust -> completed node.
        pathway_service.record_learning_record(
            alice_id,
            alice_mission.mission_id,
            LearningRecordCreateRequest(
                concept_id="光反应",
                record_type=LearningRecordType.TRANSFER_TASK,
                source_type=LearningRecordSource.EXERCISE_ATTEMPT,
                source_id="transfer-alice",
                response_text="新情境中正确应用。",
                evaluated_state=AnswerEvaluatedState.CORRECT,
                record_reason="迁移任务正确。",
            ),
        )
        alice_proposal = pathway_service.propose_knowledge_state_update(
            alice_id,
            alice_mission.mission_id,
            ProposeKnowledgeStateUpdateRequest(concept_id="光反应"),
        )
        pathway_service.decide_knowledge_state_proposal(
            alice_id,
            alice_proposal.proposal_id,
            DecideKnowledgeStateProposalRequest(
                decision=HumanDecisionType.ACCEPT,
                reason="同意。",
            ),
        )

        # Bob supplies an incorrect exercise -> emerging -> pending node.
        pathway_service.record_learning_record(
            bob_id,
            bob_mission.mission_id,
            LearningRecordCreateRequest(
                concept_id="光反应",
                record_type=LearningRecordType.EXERCISE_ATTEMPT,
                source_type=LearningRecordSource.EXERCISE_ATTEMPT,
                source_id="attempt-bob",
                response_text="错误。",
                evaluated_state=AnswerEvaluatedState.INCORRECT,
                record_reason="练习回答错误。",
            ),
        )
        bob_proposal = pathway_service.propose_knowledge_state_update(
            bob_id,
            bob_mission.mission_id,
            ProposeKnowledgeStateUpdateRequest(concept_id="光反应"),
        )
        pathway_service.decide_knowledge_state_proposal(
            bob_id,
            bob_proposal.proposal_id,
            DecideKnowledgeStateProposalRequest(
                decision=HumanDecisionType.ACCEPT,
                reason="同意。",
            ),
        )

        alice_path = pathway_service.get_learning_path(alice_id, alice_mission.mission_id)
        bob_path = pathway_service.get_learning_path(bob_id, bob_mission.mission_id)

        alice_light = next(n for n in alice_path.nodes if n.concept_id == "光反应")
        bob_light = next(n for n in bob_path.nodes if n.concept_id == "光反应")
        assert alice_light.status == LearningPathNodeStatus.COMPLETED
        # Bob's diagnostic was incorrect and his exercise was wrong, so the
        # state remains emerging and the path shows a pending consolidation node.
        assert bob_light.status == LearningPathNodeStatus.PENDING


class TestIsolation:
    def test_user_cannot_see_another_users_records(
        self,
        pathway_service: LearningPathService,
        learning_service: LearningService,
        alice_id: str,
        bob_id: str,
    ) -> None:
        mission = learning_service.create_mission(alice_id, _mission_request())
        record = pathway_service.record_learning_record(
            alice_id,
            mission.mission_id,
            LearningRecordCreateRequest(
                concept_id="光反应",
                record_type=LearningRecordType.EXERCISE_ATTEMPT,
                source_type=LearningRecordSource.EXERCISE_ATTEMPT,
                source_id="attempt-1",
                response_text="正确。",
                evaluated_state=AnswerEvaluatedState.CORRECT,
                record_reason="正确完成检索练习。",
            ),
        )

        from bridges.learning import LearningError

        with pytest.raises(LearningError):
            pathway_service.list_learning_records(bob_id, mission.mission_id)

        with pytest.raises(LearningError):
            pathway_service._repository.get_learning_record(bob_id, record.record_id)
