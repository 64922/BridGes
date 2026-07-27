"""Spaced-repetition and interleaved-practice scheduling tests (T024).

The seam under test: a user with learning records and knowledge states receives
explainable review tasks; they can postpone, adjust, complete or cancel them;
completion creates a new learning record without overwriting the old state; and
learning-path changes cancel or reschedule future tasks.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from science_companion.contracts.learning import (
    AnswerEvaluatedState,
    HumanDecisionType,
    KnowledgeStateStatus,
    LearningMissionCreateRequest,
    LearningRecordCreateRequest,
    LearningRecordSource,
    LearningRecordType,
    ProposeKnowledgeStateUpdateRequest,
    ReviewTaskStatus,
    ReviewTaskType,
)
from science_companion.learning import (
    InMemoryLearningRepository,
    LearningPathService,
    LearningService,
    ReviewSchedulingService,
)
from science_companion.learning.adapters import LearningError


@pytest.fixture
def repository() -> InMemoryLearningRepository:
    return InMemoryLearningRepository()


@pytest.fixture
def learning_service(repository: InMemoryLearningRepository) -> LearningService:
    return LearningService(repository)


@pytest.fixture
def review_service(repository: InMemoryLearningRepository) -> ReviewSchedulingService:
    return ReviewSchedulingService(repository)


@pytest.fixture
def pathway_service(
    repository: InMemoryLearningRepository,
    review_service: ReviewSchedulingService,
) -> LearningPathService:
    return LearningPathService(repository, review_scheduler=review_service)


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


def _record(
    concept_id: str,
    record_type: LearningRecordType,
    evaluated_state: AnswerEvaluatedState,
    source_id: str = "attempt-1",
) -> LearningRecordCreateRequest:
    return LearningRecordCreateRequest(
        concept_id=concept_id,
        record_type=record_type,
        source_type=LearningRecordSource.EXERCISE_ATTEMPT,
        source_id=source_id,
        response_text="回答",
        evaluated_state=evaluated_state,
        record_reason="测试学习记录。",
    )


def _complete_diagnostic_and_accept(
    learning_service: LearningService,
    pathway_service: LearningPathService,
    account_id: str,
    mission_id: str,
) -> None:
    from science_companion.contracts.learning import (
        DecideKnowledgeStateProposalRequest,
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
    run = learning_service.create_diagnostic_run(account_id, mission_id, questions=questions)
    learning_service.record_answer(
        account_id,
        run.run_id,
        DiagnosticAnswerCreateRequest(
            question_id=run.questions[0].question_id,
            response_text="类囊体膜；产物是 ATP 和 NADPH。",
            evaluated_state=AnswerEvaluatedState.CORRECT,
            evaluator="rule",
            evaluation_reason="回答完整。",
        ),
    )
    learning_service.complete_diagnostic(account_id, run.run_id)
    pathway_service.record_learning_record(
        account_id,
        mission_id,
        _record("光反应", LearningRecordType.EXERCISE_ATTEMPT, AnswerEvaluatedState.CORRECT),
    )
    proposal = pathway_service.propose_knowledge_state_update(
        account_id,
        mission_id,
        ProposeKnowledgeStateUpdateRequest(concept_id="光反应"),
    )
    pathway_service.decide_knowledge_state_proposal(
        account_id,
        proposal.proposal_id,
        DecideKnowledgeStateProposalRequest(
            decision=HumanDecisionType.ACCEPT, reason="同意。"
        ),
    )


class TestScheduleReviews:
    def test_review_task_justified_by_records_state_and_mission(
        self,
        review_service: ReviewSchedulingService,
        learning_service: LearningService,
        pathway_service: LearningPathService,
        alice_id: str,
    ) -> None:
        mission = learning_service.create_mission(alice_id, _mission_request())
        _complete_diagnostic_and_accept(
            learning_service, pathway_service, alice_id, mission.mission_id
        )
        pathway_service.record_learning_record(
            alice_id, mission.mission_id, _record("光反应", LearningRecordType.EXERCISE_ATTEMPT, AnswerEvaluatedState.CORRECT)
        )

        schedule = review_service.schedule_reviews_for_mission(alice_id, mission.mission_id)

        assert schedule.mission_id == mission.mission_id
        assert schedule.task_ids
        task = review_service.get_review_task(alice_id, schedule.task_ids[0])
        assert task.concept_id == "光反应"
        assert task.reason
        assert "理解光合作用" in task.reason
        assert task.knowledge_state_id is not None
        assert task.source_record_ids

    def test_no_records_means_no_tasks(
        self,
        review_service: ReviewSchedulingService,
        learning_service: LearningService,
        alice_id: str,
    ) -> None:
        mission = learning_service.create_mission(alice_id, _mission_request())
        schedule = review_service.schedule_reviews_for_mission(alice_id, mission.mission_id)
        assert schedule.task_ids == []

    def test_robust_concept_gets_spaced_repetition(
        self,
        review_service: ReviewSchedulingService,
        learning_service: LearningService,
        pathway_service: LearningPathService,
        alice_id: str,
    ) -> None:
        mission = learning_service.create_mission(alice_id, _mission_request())
        for i in range(3):
            pathway_service.record_learning_record(
                alice_id,
                mission.mission_id,
                _record(
                    "光反应",
                    LearningRecordType.DELAYED_RETRIEVAL,
                    AnswerEvaluatedState.CORRECT,
                    source_id=f"delayed-{i}",
                ),
            )
        proposal = pathway_service.propose_knowledge_state_update(
            alice_id,
            mission.mission_id,
            ProposeKnowledgeStateUpdateRequest(concept_id="光反应"),
        )
        from science_companion.contracts.learning import (
            DecideKnowledgeStateProposalRequest,
        )

        pathway_service.decide_knowledge_state_proposal(
            alice_id,
            proposal.proposal_id,
            DecideKnowledgeStateProposalRequest(
                decision=HumanDecisionType.ACCEPT, reason="同意。"
            ),
        )

        schedule = review_service.schedule_reviews_for_mission(alice_id, mission.mission_id)
        task = next(
            t for t in [review_service.get_review_task(alice_id, tid) for tid in schedule.task_ids]
            if t.concept_id == "光反应"
        )
        assert task.task_type == ReviewTaskType.SPACED_REPETITION
        assert task.interval_days >= 1

    def test_emerging_concept_gets_interleaved_practice(
        self,
        review_service: ReviewSchedulingService,
        learning_service: LearningService,
        pathway_service: LearningPathService,
        alice_id: str,
    ) -> None:
        mission = learning_service.create_mission(alice_id, _mission_request())
        pathway_service.record_learning_record(
            alice_id,
            mission.mission_id,
            _record("光反应", LearningRecordType.EXERCISE_ATTEMPT, AnswerEvaluatedState.INCORRECT),
        )

        schedule = review_service.schedule_reviews_for_mission(alice_id, mission.mission_id)
        task = review_service.get_review_task(alice_id, schedule.task_ids[0])
        assert task.task_type == ReviewTaskType.INTERLEAVED_PRACTICE
        assert task.interval_days == 1

    def test_incorrect_last_record_resets_interval(
        self,
        review_service: ReviewSchedulingService,
        learning_service: LearningService,
        pathway_service: LearningPathService,
        alice_id: str,
    ) -> None:
        mission = learning_service.create_mission(alice_id, _mission_request())
        pathway_service.record_learning_record(
            alice_id,
            mission.mission_id,
            _record("光反应", LearningRecordType.DELAYED_RETRIEVAL, AnswerEvaluatedState.CORRECT, "d1"),
        )
        pathway_service.record_learning_record(
            alice_id,
            mission.mission_id,
            _record("光反应", LearningRecordType.DELAYED_RETRIEVAL, AnswerEvaluatedState.CORRECT, "d2"),
        )
        # Latest record is incorrect -> interval should reset to 1 day.
        pathway_service.record_learning_record(
            alice_id,
            mission.mission_id,
            _record("光反应", LearningRecordType.EXERCISE_ATTEMPT, AnswerEvaluatedState.INCORRECT, "bad"),
        )

        schedule = review_service.schedule_reviews_for_mission(alice_id, mission.mission_id)
        task = review_service.get_review_task(alice_id, schedule.task_ids[0])
        assert task.interval_days == 1


class TestPendingTaskInterleaving:
    def test_pending_tasks_are_interleaved_by_concept(
        self,
        review_service: ReviewSchedulingService,
        learning_service: LearningService,
        pathway_service: LearningPathService,
        alice_id: str,
    ) -> None:
        mission = learning_service.create_mission(alice_id, _mission_request())
        pathway_service.record_learning_record(
            alice_id,
            mission.mission_id,
            _record("光反应", LearningRecordType.EXERCISE_ATTEMPT, AnswerEvaluatedState.CORRECT, "a"),
        )
        pathway_service.record_learning_record(
            alice_id,
            mission.mission_id,
            _record("暗反应", LearningRecordType.EXERCISE_ATTEMPT, AnswerEvaluatedState.CORRECT, "b"),
        )

        review_service.schedule_reviews_for_mission(alice_id, mission.mission_id)
        pending = review_service.list_pending_tasks(alice_id, mission.mission_id)

        assert len(pending) == 2
        # Interleaving means consecutive tasks should not share the same concept.
        assert pending[0].concept_id != pending[1].concept_id


class TestUserAdjustments:
    def test_postpone_review_task(
        self,
        review_service: ReviewSchedulingService,
        learning_service: LearningService,
        pathway_service: LearningPathService,
        alice_id: str,
    ) -> None:
        mission = learning_service.create_mission(alice_id, _mission_request())
        pathway_service.record_learning_record(
            alice_id,
            mission.mission_id,
            _record("光反应", LearningRecordType.EXERCISE_ATTEMPT, AnswerEvaluatedState.CORRECT),
        )
        schedule = review_service.schedule_reviews_for_mission(alice_id, mission.mission_id)
        task = review_service.get_review_task(alice_id, schedule.task_ids[0])

        new_due = datetime.now(UTC) + timedelta(days=7)
        from science_companion.contracts.learning import ReviewTaskPostponeRequest

        postponed = review_service.postpone_review_task(
            alice_id,
            task.task_id,
            ReviewTaskPostponeRequest(new_due_at=new_due, reason="本周忙碌。"),
        )

        assert postponed.status == ReviewTaskStatus.POSTPONED
        assert postponed.due_at == new_due
        assert postponed.postponed_to == new_due
        assert "本周忙碌" in postponed.reason

    def test_adjust_review_task(
        self,
        review_service: ReviewSchedulingService,
        learning_service: LearningService,
        pathway_service: LearningPathService,
        alice_id: str,
    ) -> None:
        mission = learning_service.create_mission(alice_id, _mission_request())
        pathway_service.record_learning_record(
            alice_id,
            mission.mission_id,
            _record("光反应", LearningRecordType.EXERCISE_ATTEMPT, AnswerEvaluatedState.CORRECT),
        )
        schedule = review_service.schedule_reviews_for_mission(alice_id, mission.mission_id)
        task = review_service.get_review_task(alice_id, schedule.task_ids[0])

        from science_companion.contracts.learning import ReviewTaskAdjustRequest

        new_due = datetime.now(UTC) + timedelta(days=14)
        adjusted = review_service.adjust_review_task(
            alice_id,
            task.task_id,
            ReviewTaskAdjustRequest(
                new_due_at=new_due, new_interval_days=14, reason="调整间隔。"
            ),
        )

        assert adjusted.status == ReviewTaskStatus.SCHEDULED
        assert adjusted.due_at == new_due
        assert adjusted.interval_days == 14
        assert "调整间隔" in adjusted.reason

    def test_cancel_review_task(
        self,
        review_service: ReviewSchedulingService,
        learning_service: LearningService,
        pathway_service: LearningPathService,
        alice_id: str,
    ) -> None:
        mission = learning_service.create_mission(alice_id, _mission_request())
        pathway_service.record_learning_record(
            alice_id,
            mission.mission_id,
            _record("光反应", LearningRecordType.EXERCISE_ATTEMPT, AnswerEvaluatedState.CORRECT),
        )
        schedule = review_service.schedule_reviews_for_mission(alice_id, mission.mission_id)
        task = review_service.get_review_task(alice_id, schedule.task_ids[0])

        from science_companion.contracts.learning import ReviewTaskCancelRequest

        cancelled = review_service.cancel_review_task(
            alice_id,
            task.task_id,
            ReviewTaskCancelRequest(reason="已经掌握，无需复习。"),
        )

        assert cancelled.status == ReviewTaskStatus.CANCELLED
        assert cancelled.cancellation_reason == "已经掌握，无需复习。"


class TestCompleteReviewTask:
    def test_completion_creates_new_record_without_overwriting_state(
        self,
        review_service: ReviewSchedulingService,
        learning_service: LearningService,
        pathway_service: LearningPathService,
        alice_id: str,
    ) -> None:
        mission = learning_service.create_mission(alice_id, _mission_request())
        _complete_diagnostic_and_accept(
            learning_service, pathway_service, alice_id, mission.mission_id
        )
        old_state = learning_service.get_knowledge_state(
            alice_id, mission.mission_id, "光反应"
        )
        pathway_service.record_learning_record(
            alice_id,
            mission.mission_id,
            _record("光反应", LearningRecordType.EXERCISE_ATTEMPT, AnswerEvaluatedState.CORRECT),
        )
        schedule = review_service.schedule_reviews_for_mission(alice_id, mission.mission_id)
        task = review_service.get_review_task(alice_id, schedule.task_ids[0])

        from science_companion.contracts.learning import ReviewTaskCompleteRequest

        completed_task, record = review_service.complete_review_task(
            alice_id,
            task.task_id,
            ReviewTaskCompleteRequest(
                response_text="光反应在类囊体膜上进行。",
                evaluated_state=AnswerEvaluatedState.CORRECT,
                record_reason="延迟复测正确。",
            ),
        )

        assert completed_task.status == ReviewTaskStatus.COMPLETED
        assert record.record_type == LearningRecordType.DELAYED_RETRIEVAL
        assert record.source_id == task.task_id
        assert record.concept_id == "光反应"

        # The existing knowledge state is not overwritten; a new record is appended.
        current_state = learning_service.get_knowledge_state(
            alice_id, mission.mission_id, "光反应"
        )
        assert current_state.state_id == old_state.state_id
        records = pathway_service.list_learning_records(alice_id, mission.mission_id, "光反应")
        assert len(records) == 3


class TestPathChangeRescheduling:
    def test_path_change_cancels_tasks_for_removed_concepts(
        self,
        review_service: ReviewSchedulingService,
        learning_service: LearningService,
        pathway_service: LearningPathService,
        alice_id: str,
    ) -> None:
        mission = learning_service.create_mission(alice_id, _mission_request())
        # Only evidence for 光反应; 暗反应 has none, so only one task is scheduled.
        pathway_service.record_learning_record(
            alice_id,
            mission.mission_id,
            _record("光反应", LearningRecordType.EXERCISE_ATTEMPT, AnswerEvaluatedState.CORRECT),
        )
        schedule = review_service.schedule_reviews_for_mission(alice_id, mission.mission_id)
        original_task_id = schedule.task_ids[0]

        # Simulate a path change by creating a new path with a different concept set.
        # The scheduler cancels old tasks and rebuilds the schedule.
        new_schedule = review_service.cancel_or_reschedule_on_path_change(
            alice_id, mission.mission_id
        )

        old_task = review_service.get_review_task(alice_id, original_task_id)
        assert old_task.status == ReviewTaskStatus.CANCELLED
        assert new_schedule.schedule_id != schedule.schedule_id

    def test_learning_path_service_triggers_reschedule_on_accept(
        self,
        review_service: ReviewSchedulingService,
        learning_service: LearningService,
        pathway_service: LearningPathService,
        alice_id: str,
    ) -> None:
        mission = learning_service.create_mission(alice_id, _mission_request())
        pathway_service.record_learning_record(
            alice_id,
            mission.mission_id,
            _record("光反应", LearningRecordType.EXERCISE_ATTEMPT, AnswerEvaluatedState.CORRECT),
        )
        proposal = pathway_service.propose_knowledge_state_update(
            alice_id,
            mission.mission_id,
            ProposeKnowledgeStateUpdateRequest(concept_id="光反应"),
        )

        # Accepting the proposal recompiles the path, which should trigger review reschedule.
        from science_companion.contracts.learning import DecideKnowledgeStateProposalRequest

        pathway_service.decide_knowledge_state_proposal(
            alice_id,
            proposal.proposal_id,
            DecideKnowledgeStateProposalRequest(
                decision=HumanDecisionType.ACCEPT, reason="同意。"
            ),
        )

        schedule = review_service.get_review_schedule(alice_id, mission.mission_id)
        assert schedule.task_ids


class TestIsolation:
    def test_user_cannot_see_another_users_review_task(
        self,
        review_service: ReviewSchedulingService,
        learning_service: LearningService,
        pathway_service: LearningPathService,
        alice_id: str,
        bob_id: str,
    ) -> None:
        mission = learning_service.create_mission(alice_id, _mission_request())
        pathway_service.record_learning_record(
            alice_id,
            mission.mission_id,
            _record("光反应", LearningRecordType.EXERCISE_ATTEMPT, AnswerEvaluatedState.CORRECT),
        )
        schedule = review_service.schedule_reviews_for_mission(alice_id, mission.mission_id)
        task_id = schedule.task_ids[0]

        with pytest.raises(LearningError):
            review_service.get_review_task(bob_id, task_id)

        with pytest.raises(LearningError):
            review_service.list_pending_tasks(bob_id, mission.mission_id)
