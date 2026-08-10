"""Issue 19：对话课时序列、测验评价和计划调整。"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from bridges.contracts.learning import AnswerEvaluatedState
from bridges.contracts.teaching import (
    TeachingAnswerEvidence,
    TeachingEvidenceGate,
    TeachingEvidenceSource,
    TeachingEvidenceSourceType,
    TeachingEvidenceStatus,
    TeachingLessonProjection,
    TeachingMission,
    TeachingPlanProjection,
    TeachingPlanSession,
    TeachingQuiz,
    TeachingStage,
    TeachingTurnProjection,
)
from bridges.contracts.teaching_progress import (
    LearningArtifactStatus,
    LearningNextActionKind,
    PlanAdjustmentTrigger,
)
from bridges.learning.progress import TeachingProgressError, TeachingProgressService
from bridges.storage.database import BridgesDatabase


def _projection(
    *,
    account_id: str = "alice",
    lesson_number: int = 1,
    with_lesson: bool = True,
    answer: TeachingAnswerEvidence | None = None,
) -> TeachingTurnProjection:
    now = datetime.now(UTC)
    plan = TeachingPlanProjection(
        plan_id="plan-transformer",
        owner_account_id=account_id,
        version=1,
        goal_snapshot="学习 Transformer 的核心机制",
        target_concepts=["自注意力"],
        sessions=[
            TeachingPlanSession(
                lesson_number=1,
                title="自注意力：核心概念",
                objective="理解自注意力。",
                checkpoint="能解释查询、键和值。",
            ),
            TeachingPlanSession(
                lesson_number=2,
                title="自注意力：迁移",
                objective="在新情境中应用自注意力。",
                checkpoint="能说明应用依据。",
            ),
        ],
        target_snapshot_id="target-1",
        evidence_snapshot_id="evidence-1",
    )
    quiz = TeachingQuiz(
        question_id=f"question-{lesson_number}",
        concept="自注意力",
        question="请解释自注意力。",
        expected_focus=["自注意力"],
        evidence_refs=["source-1"],
    )
    lesson = (
        TeachingLessonProjection(
            lesson_id=f"lesson-{lesson_number}",
            plan_id=plan.plan_id,
            owner_account_id=account_id,
            lesson_number=lesson_number,
            title=f"自注意力：第 {lesson_number} 课",
            objective="理解自注意力。",
            understanding_check=quiz,
            evidence_refs=["source-1"],
            target_snapshot_id=plan.target_snapshot_id,
            evidence_snapshot_id=plan.evidence_snapshot_id,
        )
        if with_lesson
        else None
    )
    source = TeachingEvidenceSource(
        source_type=TeachingEvidenceSourceType.KNOWLEDGE_BASE,
        source_id="source-1",
        title="Transformer 讲义",
        accessed_at=now,
    )
    mission = TeachingMission(
        mission_id="mission-1",
        stage=TeachingStage.UNDERSTANDING_CHECK,
        goal=plan.goal_snapshot,
        user_intent="我想学习 Transformer",
        current_concept="自注意力",
        level_assumption="初学者",
        level_basis="默认",
        next_action="回答理解检查题。",
    )
    return TeachingTurnProjection(
        status="ready",
        mission=mission,
        goal=plan.goal_snapshot,
        level_assumption="初学者",
        steps=["讲解", "检查"],
        check_method="短测验",
        evidence_gate=TeachingEvidenceGate(
            status=TeachingEvidenceStatus.SUFFICIENT,
            reason="证据充分。",
            local_sources=[source],
            checked_at=now,
        ),
        plan=plan,
        lesson=lesson,
        quiz=quiz,
        evidence=[answer] if answer else [],
        next_prompt="继续。",
        can_answer_reliably=True,
    )


@pytest.fixture
def progress(tmp_path: Path) -> TeachingProgressService:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    return TeachingProgressService(database)


def test_successful_lesson_and_quiz_are_restorable_and_idempotent(
    progress: TeachingProgressService,
) -> None:
    projection = _projection()
    first = progress.publish_successful_lesson(
        "alice", "conversation-1", "message-1", projection, "第一课正文"
    )
    assert first.progress is not None
    assert first.progress.lesson_number == 1
    assert first.progress.quiz_status == LearningArtifactStatus.DELIVERED
    assert len(progress.list_lessons("alice", "conversation-1")) == 1
    assert len(progress.list_quizzes("alice", "conversation-1")) == 1

    replay = progress.publish_successful_lesson(
        "alice", "conversation-1", "message-1", projection, "迟到结果"
    )
    assert replay.progress is not None
    assert len(progress.list_lessons("alice", "conversation-1")) == 1
    assert progress.get_plan("alice", "conversation-1") is not None
    assert progress.get_plan("bob", "conversation-1") is None


def test_answer_assessment_drives_remedial_next_action(
    progress: TeachingProgressService,
) -> None:
    progress.publish_successful_lesson(
        "alice", "conversation-1", "message-1", _projection(), "第一课正文"
    )
    answer = TeachingAnswerEvidence(
        answer_id="answer-1",
        question_id="question-1",
        source_message_id="message-2",
        response_text="我还不理解",
        evaluated_state=AnswerEvaluatedState.INCORRECT,
        evaluation_basis="没有覆盖关键点。",
        evidence_refs=["source-1"],
        knowledge_state="unknown",
        knowledge_state_reason="需要补救。",
    )
    second = progress.publish_successful_lesson(
        "alice",
        "conversation-1",
        "message-2",
        _projection(lesson_number=2, with_lesson=False, answer=answer),
        "补救课正文",
    )
    assert second.progress is not None
    assert second.progress.next_action == LearningNextActionKind.REMEDIATE
    assert len(progress.list_lessons("alice", "conversation-1")) == 2
    assert len(progress.list_adjustments("alice", "conversation-1")) == 0
    restored = progress.get_progress("alice", "conversation-1")
    assert restored is not None
    assert restored.assessment_state == AnswerEvaluatedState.INCORRECT
    assert restored.next_action == LearningNextActionKind.REMEDIATE


def test_invalid_answer_does_not_advance_the_lesson(
    progress: TeachingProgressService,
) -> None:
    progress.publish_successful_lesson(
        "alice", "conversation-1", "message-1", _projection(), "第一课正文"
    )
    invalid = TeachingAnswerEvidence(
        answer_id="answer-invalid",
        question_id="question-1",
        source_message_id="message-2",
        response_text="",
        evaluated_state=AnswerEvaluatedState.NEEDS_REVIEW,
        evaluation_basis="作答为空，不能形成评价。",
        evidence_refs=[],
        knowledge_state="unknown",
        knowledge_state_reason="等待补答。",
    )
    progress.publish_successful_lesson(
        "alice",
        "conversation-1",
        "message-2",
        _projection(lesson_number=2, with_lesson=False, answer=invalid),
        "请先补充你的理解。",
    )
    plan = progress.get_plan("alice", "conversation-1")
    assert plan is not None
    assert plan.delivered_lesson_count == 1
    assert plan.next_lesson_number == 2
    assert progress.list_lessons("alice", "conversation-1")[0].lesson_number == 1


def test_evidence_invalidation_marks_lesson_and_quiz(
    progress: TeachingProgressService,
) -> None:
    progress.publish_successful_lesson(
        "alice", "conversation-1", "message-1", _projection(), "第一课正文"
    )
    assert progress.invalidate_evidence("alice", "conversation-1", "evidence-1") == 2
    assert progress.list_lessons("alice", "conversation-1")[0].status == (
        LearningArtifactStatus.INVALIDATED
    )
    assert progress.list_quizzes("alice", "conversation-1")[0].status == (
        LearningArtifactStatus.INVALIDATED
    )


def test_plan_adjustment_and_state_transition_are_idempotent(
    progress: TeachingProgressService,
) -> None:
    progress.publish_successful_lesson(
        "alice", "conversation-1", "message-1", _projection(), "第一课正文"
    )
    first = progress.apply_adjustment(
        "alice",
        "conversation-1",
        PlanAdjustmentTrigger.USER_FEEDBACK,
        "feedback-1",
        "用户反馈太难，下一课降低难度。",
    )
    second = progress.apply_adjustment(
        "alice",
        "conversation-1",
        PlanAdjustmentTrigger.USER_FEEDBACK,
        "feedback-1",
        "迟到的重复反馈。",
    )
    assert first is not None and second is not None
    assert first.adjustment_id == second.adjustment_id
    assert first.new_plan_version == 2
    assert len(progress.list_adjustments("alice", "conversation-1")) == 1

    with pytest.raises(TeachingProgressError):
        progress.assert_transition("artifact", "invalidated", "assessed")
