"""可恢复教学序列与自适应测验的版本化合同（Issue 19）。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from bridges.contracts.learning import AnswerEvaluatedState


class LearningPlanStatus(StrEnum):
    """教学计划的业务状态，与生成运行状态分离。"""

    ACTIVE = "active"
    WAITING_FOR_QUIZ = "waiting_for_quiz"
    BLOCKED = "blocked"
    COMPLETED = "completed"


class LearningArtifactStatus(StrEnum):
    """课时、测验和评价的可审计状态。"""

    DELIVERED = "delivered"
    SUBMITTED = "submitted"
    ASSESSED = "assessed"
    INVALIDATED = "invalidated"


class LearningNextActionKind(StrEnum):
    """评分后允许的下一步。"""

    CONTINUE = "continue"
    REVIEW = "review"
    REMEDIATE = "remediate"
    WAIT_FOR_QUIZ = "wait_for_quiz"


class PlanAdjustmentTrigger(StrEnum):
    """能够改变未交付课时的触发源。"""

    USER_FEEDBACK = "user_feedback"
    PROFILE_UPDATED = "profile_updated"
    PROFILE_WITHDRAWN = "profile_withdrawn"


class LearningPlan(BaseModel):
    """账户和对话范围内的可恢复计划快照。"""

    schema_version: str = "learning-plan-v1"
    plan_id: str
    account_id: str
    conversation_id: str
    version: int = Field(ge=1)
    status: LearningPlanStatus
    goal_snapshot: str
    target_snapshot_id: str
    evidence_snapshot_id: str
    next_lesson_number: int = Field(default=1, ge=1)
    delivered_lesson_count: int = Field(default=0, ge=0)
    next_checkpoint: int = Field(default=2, ge=1)
    current_quiz_id: str | None = None
    profile_slice_id: str | None = None
    created_at: datetime
    updated_at: datetime


class LearningLesson(BaseModel):
    """一次成功正式教学回复对应的不可倒改课时。"""

    schema_version: str = "learning-lesson-v1"
    lesson_id: str
    plan_id: str
    account_id: str
    conversation_id: str
    plan_version: int = Field(ge=1)
    lesson_number: int = Field(ge=1)
    message_id: str
    title: str
    objective: str
    content: str
    evidence_snapshot_id: str
    evidence_refs: list[str] = Field(default_factory=list)
    status: LearningArtifactStatus = LearningArtifactStatus.DELIVERED
    created_at: datetime
    delivered_at: datetime


class LearningQuiz(BaseModel):
    """绑定已教内容和证据快照的短测验。"""

    schema_version: str = "learning-quiz-v1"
    quiz_id: str
    question_id: str
    lesson_id: str
    plan_id: str
    account_id: str
    conversation_id: str
    plan_version: int = Field(ge=1)
    lesson_number: int = Field(ge=1)
    concept: str
    question: str
    expected_focus: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    status: LearningArtifactStatus = LearningArtifactStatus.DELIVERED
    created_at: datetime


class LearningAttempt(BaseModel):
    """用户对某道测验的单次幂等作答。"""

    schema_version: str = "learning-attempt-v1"
    attempt_id: str
    quiz_id: str
    account_id: str
    conversation_id: str
    source_message_id: str
    response_text: str
    status: LearningArtifactStatus = LearningArtifactStatus.SUBMITTED
    created_at: datetime


class LearningAssessment(BaseModel):
    """绑定作答证据的评价；不宣称永久掌握。"""

    schema_version: str = "learning-assessment-v1"
    assessment_id: str
    attempt_id: str
    quiz_id: str
    account_id: str
    conversation_id: str
    evaluated_state: AnswerEvaluatedState
    evaluation_basis: str
    evidence_refs: list[str] = Field(default_factory=list)
    status: LearningArtifactStatus = LearningArtifactStatus.ASSESSED
    created_at: datetime


class LearningNextAction(BaseModel):
    """一次评分或课时交付后的用户可见下一步。"""

    schema_version: str = "learning-next-action-v1"
    next_action_id: str
    plan_id: str
    account_id: str
    conversation_id: str
    lesson_number: int = Field(ge=1)
    kind: LearningNextActionKind
    reason: str
    target_lesson_number: int = Field(ge=1)
    created_at: datetime


class PlanAdjustment(BaseModel):
    """只影响尚未交付课时的版本化计划调整。"""

    schema_version: str = "learning-plan-adjustment-v1"
    adjustment_id: str
    plan_id: str
    account_id: str
    conversation_id: str
    trigger: PlanAdjustmentTrigger
    trigger_key: str
    reason: str
    old_plan_version: int = Field(ge=1)
    new_plan_version: int = Field(ge=1)
    affected_future_lesson_numbers: list[int] = Field(default_factory=list)
    created_at: datetime


class LearningProgressProjection(BaseModel):
    """学习模式只读展示的轻量进度，不代表课程、测验或掌握结论。"""

    schema_version: str = "learning-progress-v1"
    goal: str = Field(description="当前会话的学习目标。")
    covered_topics: list[str] = Field(
        default_factory=list, description="回答中已经覆盖的主题标题。"
    )
    source_message_id: str = Field(description="最近一次更新进度的助手消息。")
    created_at: datetime
    updated_at: datetime


class TeachingProgressProjection(BaseModel):
    """消息流中展示的课时进度，不是长期画像维度。"""

    schema_version: str = "teaching-progress-v1"
    plan_id: str
    plan_version: int = Field(ge=1)
    lesson_number: int = Field(ge=1)
    delivered_lesson_count: int = Field(ge=0)
    next_checkpoint: int = Field(ge=1)
    quiz_id: str | None = None
    quiz_status: LearningArtifactStatus | None = None
    assessment_state: AnswerEvaluatedState | None = None
    next_action: LearningNextActionKind
    next_action_reason: str


__all__ = [
    "LearningArtifactStatus",
    "LearningAssessment",
    "LearningAttempt",
    "LearningLesson",
    "LearningNextAction",
    "LearningNextActionKind",
    "LearningProgressProjection",
    "LearningPlan",
    "LearningPlanStatus",
    "LearningQuiz",
    "PlanAdjustment",
    "PlanAdjustmentTrigger",
    "TeachingProgressProjection",
]
