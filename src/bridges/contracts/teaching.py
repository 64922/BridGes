"""学习模式教学轮次与证据充足性门的公开合同。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from bridges.contracts.learning import AnswerEvaluatedState
from bridges.contracts.teaching_progress import (
    LearningProgressProjection,
    TeachingProgressProjection,
)


class TeachingCardStatus(StrEnum):
    """教学卡片的用户可见生命周期状态。"""

    LOADING = "loading"
    READY = "ready"
    EMPTY = "empty"
    ERROR = "error"
    PERMISSION = "permission"
    RECOVERY = "recovery"


class TeachingArtifactStatus(StrEnum):
    """计划与课时在原子发布前后的可审计状态。"""

    STAGED = "staged"
    PUBLISHED = "published"


class TeachingStage(StrEnum):
    """会话中持久化的教学状态机阶段（Issue 08）。

    - ``mission_setup``：确认学习目标、用途与已有水平，不生成正式教学回答；
    - ``micro_lesson``：基于合格来源一次讲一个概念（解释、例子、边界）；
    - ``understanding_check``：本轮理解检查题等待作答；
    - ``adaptation``：依据回答证据选择补讲、换例子、迁移或下一概念；
    - ``blocked``：来源受阻时保留 mission 与恢复动作，不回退成无关回答。
    """

    MISSION_SETUP = "mission_setup"
    MICRO_LESSON = "micro_lesson"
    UNDERSTANDING_CHECK = "understanding_check"
    ADAPTATION = "adaptation"
    BLOCKED = "blocked"


class TeachingIntent(StrEnum):
    """用户消息的意图分类（Issue 08：不用单一关键词正则决定全部行为）。"""

    ESTABLISH_MISSION = "establish_mission"
    MODIFY_MISSION = "modify_mission"
    FACT_QUESTION = "fact_question"
    ANSWER = "answer"
    FOLLOW_UP = "follow_up"
    SKIP = "skip"
    SWITCH_MODE = "switch_mode"


class TeachingEvidenceStatus(StrEnum):
    """教学正式回答使用的证据裁决。"""

    SUFFICIENT = "sufficient"
    INSUFFICIENT = "insufficient"
    CONFLICT = "conflict"
    UNAVAILABLE = "unavailable"


class TeachingSearchSource(StrEnum):
    """证据门需要补充的公开来源。"""

    NONE = "none"
    DUCKDUCKGO = "duckduckgo"
    ARXIV = "arxiv"
    BOTH = "both"


class TeachingEvidenceSourceType(StrEnum):
    """教学引用的来源层级与公开来源类型。"""

    ATTACHMENT = "attachment"
    PROJECT = "project"
    KNOWLEDGE_BASE = "knowledge_base"
    DUCKDUCKGO = "duckduckgo"
    BRAVE_SEARCH = "brave_search"
    ARXIV = "arxiv"
    #: 兼容早期未区分本地层级的历史投影；新记录不得使用。
    LEGACY_LOCAL = "local"


class TeachingKnowledgeState(StrEnum):
    """单轮作答形成的、仍需确认的知识状态候选。"""

    UNKNOWN = "unknown"
    EMERGING_CANDIDATE = "emerging_candidate"
    SUPPORTED_CANDIDATE = "supported_candidate"


class TeachingEvidenceSource(BaseModel):
    """教学轮次使用的最小来源标识，不复制私人原文。"""

    source_type: TeachingEvidenceSourceType = Field(description="来源层级或公开来源类型。")
    source_id: str = Field(description="来源或引用标识。")
    title: str = Field(description="面向用户展示的来源标题。")
    locator: str | None = Field(default=None, description="页码、章节、网址或 arXiv 标识。")
    url: str | None = Field(default=None, description="可直接打开的真实来源网址。")
    alternate_url: str | None = Field(
        default=None, description="同一来源的备用网址，例如 arXiv PDF。"
    )
    accessed_at: datetime = Field(description="本次读取或搜索来源的时间。")


class TeachingEvidenceCoverage(BaseModel):
    """公开来源覆盖裁决的脱敏观测摘要。"""

    rules_version: str = Field(description="覆盖裁决规则版本。")
    topic_aliases_version: str | None = Field(
        default=None, description="主题别名表版本；历史投影可为空。"
    )
    candidate_count: int = Field(default=0, ge=0, description="进入裁决的候选来源数。")
    fetched_count: int = Field(default=0, ge=0, description="标记为已抓取的候选数。")
    accepted_count: int = Field(default=0, ge=0, description="通过覆盖裁决的来源数。")
    rejection_counts: dict[str, int] = Field(
        default_factory=dict,
        description="按稳定原因码聚合的拒绝数，不包含来源正文。",
    )
    conflict_count: int = Field(default=0, ge=0, description="冲突来源数。")
    adjudication_duration_ms: int = Field(
        default=0,
        ge=0,
        description="覆盖裁决耗时（毫秒）。",
    )


class TeachingEvidenceGate(BaseModel):
    """每轮教学开始前的结构化证据裁决。"""

    status: TeachingEvidenceStatus = Field(description="充分、不足、冲突或不可用。")
    reason: str = Field(description="裁决理由。")
    local_sources: list[TeachingEvidenceSource] = Field(default_factory=list)
    external_sources: list[TeachingEvidenceSource] = Field(default_factory=list)
    required_search: TeachingSearchSource = Field(default=TeachingSearchSource.NONE)
    search_status: TeachingCardStatus | None = Field(default=None)
    search_error_code: str | None = Field(
        default=None, description="公开搜索失败的稳定错误码，不包含提供方正文。"
    )
    gap: str | None = Field(default=None, description="仍不能可靠回答的缺口。")
    allow_model_knowledge: bool = Field(
        default=False,
        description="公开证据不可用且没有本地可用证据时，是否允许模型以谨慎方式回答。",
    )
    recovery_steps: list[str] = Field(default_factory=list)
    coverage: TeachingEvidenceCoverage | None = Field(
        default=None,
        description="公开来源覆盖裁决的脱敏观测摘要。",
    )
    checked_at: datetime = Field(description="证据门检查时间。")


class TeachingQuiz(BaseModel):
    """单轮理解检查；每轮最多展示一个问题。"""

    question_id: str = Field(description="稳定的本轮问题标识。")
    concept: str = Field(description="问题针对的概念。")
    question: str = Field(description="面向用户的问题。")
    expected_focus: list[str] = Field(default_factory=list, description="评价时关注的关键点。")
    evidence_refs: list[str] = Field(default_factory=list, description="依据来源标识。")
    can_skip: bool = Field(default=True)
    can_follow_up: bool = Field(default=True)


class TeachingProfileUsage(BaseModel):
    """本轮教学使用画像的最小、可审计快照。"""

    schema_version: str = Field(default="teaching-profile-usage-v1")
    used_categories: list[str] = Field(
        default_factory=list,
        description="实际使用的四维画像类别，不包含画像正文。",
    )
    applied_to: list[str] = Field(
        default_factory=list,
        description="画像只影响难度、路径或例子中的哪些部分。",
    )
    defaulted: bool = Field(
        default=False,
        description="没有可用画像时是否采用中性默认值。",
    )


class TeachingPlanSession(BaseModel):
    """计划中的一个课时摘要。"""

    lesson_number: int = Field(ge=1)
    title: str
    objective: str
    checkpoint: str


class TeachingPlanProjection(BaseModel):
    """聊天内展示并持久化的版本化教学计划。"""

    schema_version: str = Field(default="teaching-plan-v1")
    plan_id: str
    owner_account_id: str = Field(default="")
    object_domain: str = Field(default="personal_vault")
    artifact_status: TeachingArtifactStatus = Field(
        default=TeachingArtifactStatus.STAGED
    )
    content_hash: str = Field(default="")
    version: int = Field(default=1, ge=1)
    goal_snapshot: str
    target_concepts: list[str] = Field(default_factory=list)
    prerequisite_assumptions: list[str] = Field(default_factory=list)
    sessions: list[TeachingPlanSession] = Field(default_factory=list)
    stage_checkpoints: list[str] = Field(default_factory=list)
    completion_criteria: list[str] = Field(default_factory=list)
    target_snapshot_id: str
    evidence_snapshot_id: str
    profile_slice_id: str | None = None
    profile_usage: TeachingProfileUsage = Field(default_factory=TeachingProfileUsage)


class TeachingLessonProjection(BaseModel):
    """计划首次发布时交付的、可独立阅读的第一个课时。"""

    schema_version: str = Field(default="teaching-lesson-v1")
    lesson_id: str
    plan_id: str
    owner_account_id: str = Field(default="")
    object_domain: str = Field(default="personal_vault")
    artifact_status: TeachingArtifactStatus = Field(
        default=TeachingArtifactStatus.STAGED
    )
    content_hash: str = Field(default="")
    lesson_number: int = Field(default=1, ge=1)
    title: str
    objective: str
    explanation: str = Field(default="")
    examples: list[str] = Field(default_factory=list)
    summary: list[str] = Field(default_factory=list)
    understanding_check: TeachingQuiz | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    target_snapshot_id: str
    evidence_snapshot_id: str
    profile_usage: TeachingProfileUsage = Field(default_factory=TeachingProfileUsage)


class TeachingAnswerEvidence(BaseModel):
    """把题目、回答、评价依据和知识状态候选串成可追溯记录。"""

    answer_id: str = Field(description="答案记录标识。")
    question_id: str = Field(description="对应的测验题标识。")
    source_message_id: str = Field(description="产生回答的用户消息标识。")
    response_text: str = Field(description="用户原始回答。")
    evaluated_state: AnswerEvaluatedState = Field(
        description="correct、partial、incorrect 或 needs_review。"
    )
    evaluation_basis: str = Field(description="可解释的评价依据。")
    evidence_refs: list[str] = Field(default_factory=list)
    knowledge_state: TeachingKnowledgeState = Field(description="本轮形成的知识状态候选。")
    knowledge_state_reason: str = Field(description="知识状态候选的限制与下一步。")
    requires_confirmation: bool = Field(
        default=True, description="是否仍需更多证据或用户确认。"
    )
    mastery_claim_allowed: bool = Field(
        default=False, description="模型不得仅凭自述或单次回答标记已掌握。"
    )


class TeachingMission(BaseModel):
    """会话中持久化的教学任务：目标、当前概念、水平假设、进度与下一步。

    随 ``TeachingTurnProjection.mission`` 落库，刷新/切换会话后恢复同一
    教学进度；mission 未确认（stage 为 mission_setup）时不生成正式教学
    回答，因此不受事实证据门约束。
    """

    mission_id: str = Field(description="稳定任务标识。")
    stage: TeachingStage = Field(description="当前教学阶段。")
    goal: str = Field(description="规范学习目标（例如“学习 Transformer 的工作原理”）。")
    user_intent: str = Field(description="用户原始表述摘要（不直接当检索查询）。")
    current_concept: str | None = Field(
        default=None, description="本轮正在教学的概念（micro_lesson 起有值）。"
    )
    level_assumption: str = Field(description="水平假设：初学者/已有基础。")
    level_basis: str = Field(description="水平假设的依据（用户声明或默认）。")
    taught_concepts: list[str] = Field(
        default_factory=list, description="已完成讲解的概念清单（进度）。"
    )
    difficulty_streak: int = Field(
        default=0, description="连续未通过/跳过检查的轮次数；达到阈值自动缩小概念或换例子。"
    )
    next_action: str = Field(description="下一步动作的用户可见说明。")
    blocked_reason: str | None = Field(
        default=None, description="来源受阻原因（stage 为 blocked 时有值）。"
    )
    recovery_steps: list[str] = Field(
        default_factory=list, description="受阻时给用户的恢复动作。"
    )


class TeachingTurnProjection(BaseModel):
    """统一聊天流中的一轮教学投影。

    ``mission``、``plan``、``lesson``、``quiz`` 和 ``progress`` 保留用于旧
    消息兼容渲染；新的学习模式只写入 ``learning_progress``。
    """

    status: TeachingCardStatus = Field(description="教学卡片状态。")
    mission: TeachingMission | None = Field(
        default=None, description="旧版教学任务，仅用于历史消息兼容渲染。"
    )
    goal: str = Field(description="本轮确认或推导的学习目标。")
    level_assumption: str = Field(description="当前水平假设及其可修正性。")
    steps: list[str] = Field(description="本轮教学步骤。")
    check_method: str = Field(description="理解检查方式。")
    evidence_gate: TeachingEvidenceGate = Field(description="本轮证据充足性门结果。")
    plan: TeachingPlanProjection | None = Field(
        default=None, description="旧版版本化教学计划，仅用于历史消息兼容渲染。"
    )
    lesson: TeachingLessonProjection | None = Field(
        default=None, description="旧版课时，仅用于历史消息兼容渲染。"
    )
    quiz: TeachingQuiz | None = Field(
        default=None, description="旧版理解检查题，仅用于历史消息兼容渲染。"
    )
    progress: TeachingProgressProjection | None = Field(
        default=None, description="旧版课时进度，仅用于历史消息兼容。"
    )
    learning_progress: LearningProgressProjection | None = Field(
        default=None, description="当前会话的轻量学习目标与已覆盖主题。"
    )
    evidence: list[TeachingAnswerEvidence] = Field(default_factory=list)
    next_prompt: str = Field(description="下一步邀请。")
    gap_response: str | None = Field(default=None, description="证据不足时的安全说明。")
    can_answer_reliably: bool = Field(default=False)
    can_cancel: bool = Field(default=False)
    can_retry: bool = Field(default=False)
    can_skip: bool = Field(default=True)
    can_follow_up: bool = Field(default=True)
    can_switch_mode: bool = Field(default=True)
