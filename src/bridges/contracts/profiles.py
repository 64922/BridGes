"""Profile observation, candidate and human-decision contracts.

These models define the public surface of the evidence-backed profile loop:
observations are traceable atomic signals; candidates are explainable hypotheses
that cannot be treated as stable facts; human decisions (accept, reject, modify)
are recorded and auditable. Only accepted and promoted assertions may enter a
memory slice for task use.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ProfileDimension(StrEnum):
    """The nine governable profile dimensions of the digital twin center.

    Issue 25: each dimension is a separately governed record category with its
    own assertions, authorization and history, rather than a merged long text.
    """

    BASIC_INFORMATION = "basic_information"
    STAGE_GOAL = "stage_goal"
    INTEREST_PREFERENCE = "interest_preference"
    EXPRESSION_HABIT = "expression_habit"
    KNOWLEDGE_STATE = "knowledge_state"
    EMOTION_TREND = "emotion_trend"
    IMPORTANT_EXPERIENCE = "important_experience"
    CURRENT_PROBLEM = "current_problem"
    AUTHORIZATION_SCOPE = "authorization_scope"


class FourDimension(StrEnum):
    """扩展合同中的四个产品画像维度。

    Expand/migrate 期间旧的 :class:`ProfileDimension` 仍可解码。新画像记录使用
    独立枚举，避免旧治理维度成为新的写入目标。
    """

    ACADEMIC_STATUS = "academic_status"
    KNOWLEDGE_INTEREST = "knowledge_interest"
    HOBBY = "hobby"
    STAGE_GOAL = "stage_goal"

    @property
    def label(self) -> str:
        return FOUR_DIMENSION_LABELS[self]


FOUR_DIMENSION_LABELS: dict[FourDimension, str] = {
    FourDimension.ACADEMIC_STATUS: "学业情况",
    FourDimension.KNOWLEDGE_INTEREST: "感兴趣的知识",
    FourDimension.HOBBY: "兴趣爱好",
    FourDimension.STAGE_GOAL: "阶段目标",
}


PROFILE_DIMENSION_LABELS: dict[ProfileDimension, str] = {
    ProfileDimension.BASIC_INFORMATION: "基本情况",
    ProfileDimension.STAGE_GOAL: "阶段目标",
    ProfileDimension.INTEREST_PREFERENCE: "兴趣偏好",
    ProfileDimension.EXPRESSION_HABIT: "表达习惯",
    ProfileDimension.KNOWLEDGE_STATE: "知识状态",
    ProfileDimension.EMOTION_TREND: "情绪变化趋势",
    ProfileDimension.IMPORTANT_EXPERIENCE: "重要经历",
    ProfileDimension.CURRENT_PROBLEM: "正在面对的问题",
    ProfileDimension.AUTHORIZATION_SCOPE: "授权范围",
}

#: Dimensions whose records are user-confirmed by design (ADR-0002): they may
#: only be written after explicit confirmation, never by silent inference.
USER_CONFIRMED_DIMENSIONS: frozenset[ProfileDimension] = frozenset(
    {
        ProfileDimension.EMOTION_TREND,
        ProfileDimension.IMPORTANT_EXPERIENCE,
        ProfileDimension.CURRENT_PROBLEM,
    }
)

#: Low-risk dimensions that may be auto-written only under a user-granted
#: permission for the matching category and applicable scene (ADR-0002 /
#: Issue 26). Permissions default off and can never be granted by a model.
AUTO_WRITABLE_DIMENSIONS: frozenset[ProfileDimension] = frozenset(
    {
        ProfileDimension.STAGE_GOAL,
        ProfileDimension.INTEREST_PREFERENCE,
        ProfileDimension.EXPRESSION_HABIT,
    }
)


class ProfileSourceType(StrEnum):
    """How the observation originated."""

    EXPLICIT_STATEMENT = "explicit_statement"
    CORRECTION = "correction"
    CHOICE = "choice"
    TASK_BEHAVIOR = "task_behavior"
    ASSESSMENT = "assessment"
    IMPORTED = "imported"
    SYSTEM_INFERENCE = "system_inference"


class ProfileSignalKind(StrEnum):
    """Kind of signal the observation carries."""

    PREFERENCE = "preference"
    GOAL = "goal"
    STYLE = "style"
    PRIOR_KNOWLEDGE = "prior_knowledge"
    MISCONCEPTION = "misconception"
    TRANSIENT_EMOTION = "transient_emotion"
    ROLE_PLAY = "role_play"
    THIRD_PARTY_STORY = "third_party_story"
    SENSITIVE_IDENTITY = "sensitive_identity"
    OTHER = "other"


class ProfileSensitivityClass(StrEnum):
    """Sensitivity classification that governs retention and promotion."""

    PUBLIC = "public"
    PREFERENCE = "preference"
    LEARNING = "learning"
    SENSITIVE = "sensitive"
    PROHIBITED = "prohibited"


class ObservationStatus(StrEnum):
    """Lifecycle status of a profile observation."""

    ACTIVE = "active"
    DISCARDED = "discarded"


class CandidateReviewStatus(StrEnum):
    """Where the candidate is in the human-review loop."""

    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    MODIFIED = "modified"
    CONFLICTED = "conflicted"
    STALE = "stale"
    DISCARDED = "discarded"


class CandidateStabilityState(StrEnum):
    """Stability classification of a candidate."""

    CANDIDATE = "candidate"
    ACTIVE = "active"
    RESTRICTED = "restricted"
    FROZEN = "frozen"
    STALE = "stale"
    DELETED = "deleted"


class DecisionType(StrEnum):
    """Human decision on a candidate profile."""

    ACCEPT = "accept"
    REJECT = "reject"
    MODIFY = "modify"


class AssertionStatus(StrEnum):
    """Lifecycle status of a promoted profile assertion.

    ``WITHDRAWN`` stops further answer use while keeping the auditable history;
    ``FROZEN`` additionally prevents automatic updates (enforced from Issue 26).
    """

    ACTIVE = "active"
    FROZEN = "frozen"
    WITHDRAWN = "withdrawn"
    STALE = "stale"
    DELETED = "deleted"


class FourDimensionRecordStatus(StrEnum):
    """扩展四维画像记录的生命周期状态。"""

    ACTIVE = "active"
    WITHDRAWN = "withdrawn"


class FourDimensionMigrationStatus(StrEnum):
    """一次账户级确定性迁移尝试的结果。"""

    COMPLETED = "completed"
    RETRYABLE = "retryable"


class SliceStatus(StrEnum):
    """Lifecycle status of a compiled memory slice bound to a run."""

    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"
    CANCELLED = "cancelled"


class ProfileObservation(BaseModel):
    """Traceable atomic signal that may support a candidate profile."""

    observation_id: str = Field(description="Stable observation identifier.")
    owner_account_id: str = Field(description="Owning account identifier.")
    project_id: str | None = Field(
        default=None,
        description="Project context when the observation belongs to a project.",
    )
    source_type: ProfileSourceType = Field(
        description="How the observation originated."
    )
    source_ref: str = Field(
        description="Reference to the source object (conversation, run, import, etc.)."
    )
    source_span_or_event: str = Field(
        description="Specific span or event within the source (message id, turn, etc.)."
    )
    scene: str = Field(description="Scene or situation in which the signal occurred.")
    purpose: str = Field(
        description="Declared purpose for which the observation was collected."
    )
    observed_content: str = Field(description="Literal or summarized observed content.")
    signal_kind: ProfileSignalKind = Field(description="Kind of signal.")
    extractor_and_version: str = Field(
        description="Extractor and version that produced the observation."
    )
    model_rationale: str | None = Field(
        default=None,
        description="Structured rationale when produced by a model extractor.",
    )
    reliability_factors: list[str] = Field(
        default_factory=list,
        description="Factors that support or weaken the observation.",
    )
    sensitivity_class: ProfileSensitivityClass = Field(
        description="Sensitivity classification governing retention and promotion."
    )
    retention_policy: str = Field(description="Retention policy for this observation.")
    authorization_version: str = Field(
        default="authz-1.0",
        description="Authorization policy version at collection time.",
    )
    content_hash: str = Field(
        description="SHA-256 hash of observed_content and source metadata."
    )
    status: ObservationStatus = Field(description="Lifecycle status.")
    created_at: datetime = Field(description="Creation timestamp.")
    updated_at: datetime = Field(description="Last update timestamp.")


class ProfileObservationCreateRequest(BaseModel):
    """Request to record a new profile observation."""

    owner_account_id: str = Field(description="Owning account identifier.")
    project_id: str | None = Field(
        default=None, description="Optional project context."
    )
    source_type: ProfileSourceType = Field(
        description="How the observation originated."
    )
    source_ref: str = Field(description="Reference to the source object.")
    source_span_or_event: str = Field(
        description="Specific span or event within the source."
    )
    scene: str = Field(description="Scene or situation.")
    purpose: str = Field(description="Declared purpose.")
    observed_content: str = Field(description="Observed content.")
    signal_kind: ProfileSignalKind = Field(description="Kind of signal.")
    extractor_and_version: str = Field(description="Extractor and version.")
    model_rationale: str | None = Field(
        default=None, description="Extractor rationale."
    )
    reliability_factors: list[str] = Field(
        default_factory=list, description="Reliability factors."
    )
    sensitivity_class: ProfileSensitivityClass = Field(description="Sensitivity class.")
    retention_policy: str = Field(description="Retention policy.")
    authorization_version: str = Field(
        default="authz-1.0", description="Authorization policy version."
    )


class HumanDecision(BaseModel):
    """Named human decision on a candidate profile."""

    decision_id: str = Field(description="Stable decision identifier.")
    candidate_id: str = Field(description="Candidate the decision applies to.")
    account_id: str = Field(description="Account that made the decision.")
    decision: DecisionType = Field(description="Decision type.")
    reason: str = Field(description="Human-readable rationale.")
    modified_value_or_rule: str | None = Field(
        default=None,
        description="Modified value when decision is 'modify'.",
    )
    modified_applicable_scenes: list[str] | None = Field(
        default=None,
        description="Modified applicable scenes when decision is 'modify'.",
    )
    created_at: datetime = Field(description="When the decision was recorded.")


class ProfileCandidate(BaseModel):
    """Explainable candidate profile awaiting human review."""

    candidate_id: str = Field(description="Stable candidate identifier.")
    owner_account_id: str = Field(description="Owning account identifier.")
    canonical_dimension: str = Field(
        description="Profile dimension (e.g. 'expression_brevity')."
    )
    value_or_rule: str = Field(description="Proposed value or rule.")
    applicable_scenes: list[str] = Field(
        default_factory=list,
        description="Scenes in which the candidate may apply.",
    )
    non_applicable_scenes: list[str] = Field(
        default_factory=list,
        description="Scenes in which the candidate must not apply.",
    )
    supporting_observation_ids: list[str] = Field(
        default_factory=list,
        description="Observations that support the candidate.",
    )
    contradicting_observation_ids: list[str] = Field(
        default_factory=list,
        description="Observations that contradict the candidate.",
    )
    evidence_summary: str = Field(description="Structured evidence summary.")
    authorization_scope: str = Field(description="Authorization scope for use.")
    promotion_policy_version: str = Field(
        default="promotion-1.0",
        description="Promotion policy version used to evaluate the candidate.",
    )
    review_status: CandidateReviewStatus = Field(description="Review status.")
    stability_state: CandidateStabilityState = Field(
        default=CandidateStabilityState.CANDIDATE,
        description="Stability classification.",
    )
    sensitivity_class: ProfileSensitivityClass = Field(
        default=ProfileSensitivityClass.PREFERENCE,
        description="Sensitivity classification governing retention and slice inclusion.",
    )
    proposed_at: datetime = Field(description="When the candidate was proposed.")
    updated_at: datetime = Field(description="Last update timestamp.")
    expires_at: datetime | None = Field(
        default=None,
        description="Optional expiration after which the candidate goes stale.",
    )
    human_decision: HumanDecision | None = Field(
        default=None,
        description="Recorded human decision, if any.",
    )


class ProfileCandidateCreateRequest(BaseModel):
    """Request to propose a candidate profile from observations."""

    owner_account_id: str = Field(description="Owning account identifier.")
    canonical_dimension: str = Field(description="Profile dimension.")
    value_or_rule: str = Field(description="Proposed value or rule.")
    applicable_scenes: list[str] = Field(default_factory=list)
    non_applicable_scenes: list[str] = Field(default_factory=list)
    supporting_observation_ids: list[str] = Field(default_factory=list)
    contradicting_observation_ids: list[str] = Field(default_factory=list)
    evidence_summary: str = Field(
        default="", description="Structured evidence summary for the candidate."
    )
    authorization_scope: str = Field(
        default="general", description="Authorization scope."
    )
    promotion_policy_version: str = Field(default="promotion-1.0")
    sensitivity_class: ProfileSensitivityClass = Field(
        default=ProfileSensitivityClass.PREFERENCE,
        description="Sensitivity classification of the candidate.",
    )
    expires_at: datetime | None = Field(default=None)


class CandidateDecision(BaseModel):
    """Request to accept, reject or modify a candidate profile."""

    decision: DecisionType = Field(description="Decision type.")
    reason: str = Field(description="Human-readable rationale.")
    modified_value_or_rule: str | None = Field(default=None)
    modified_applicable_scenes: list[str] | None = Field(default=None)


class ProfileAssertion(BaseModel):
    """Stable, promoted profile entry that may enter a memory slice."""

    assertion_id: str = Field(description="Stable assertion identifier.")
    owner_account_id: str = Field(description="Owning account identifier.")
    canonical_dimension: str = Field(description="Profile dimension.")
    value_or_rule: str = Field(description="Confirmed value or rule.")
    applicable_scenes: list[str] = Field(default_factory=list)
    supporting_observation_ids: list[str] = Field(default_factory=list)
    contradicting_observation_ids: list[str] = Field(default_factory=list)
    authorization_scope: str = Field(description="Authorization scope for use.")
    status: AssertionStatus = Field(description="Lifecycle status.")
    sensitivity_class: ProfileSensitivityClass = Field(
        default=ProfileSensitivityClass.PREFERENCE,
        description="Sensitivity classification governing slice inclusion.",
    )
    expires_at: datetime | None = Field(
        default=None,
        description="Optional expiration after which the assertion cannot be recalled.",
    )
    promoted_from_candidate_id: str | None = Field(
        default=None,
        description="Candidate from which this assertion was promoted.",
    )
    version: int = Field(default=1, description="Optimistic concurrency version.")
    last_used_at: datetime | None = Field(
        default=None,
        description="When the assertion was last included in an answer slice.",
    )
    created_at: datetime = Field(description="Creation timestamp.")
    updated_at: datetime = Field(description="Last update timestamp.")


class FourDimensionProfileRecord(BaseModel):
    """一条扩展四维画像的内部记录合同。

    ``source_record_id``、``source_version``、``content_hash`` 和 ``write_origin``
    用于账户级回滚和审计；默认画像页面刻意不渲染这些内部字段。
    """

    record_id: str = Field(description="Stable four-dimension record identifier.")
    owner_account_id: str = Field(description="Owning account identifier.")
    dimension: FourDimension = Field(description="One of the four product dimensions.")
    label: str = Field(description="Chinese display label for the dimension.")
    content: str = Field(
        min_length=1, max_length=1000, description="Confirmed record content."
    )
    first_stable_recorded_at: datetime = Field(
        description="First time this record became stable; edits do not reset it."
    )
    updated_at: datetime = Field(description="Internal last-edit timestamp.")
    version: int = Field(ge=1, description="Optimistic concurrency version.")
    status: FourDimensionRecordStatus = Field(
        description="Active or withdrawn tombstone."
    )
    source_record_id: str = Field(description="Internal legacy source identifier.")
    source_version: int = Field(
        ge=1, description="Legacy source version used for migration."
    )
    content_hash: str = Field(description="Internal SHA-256 content hash.")
    write_origin: str = Field(description="Internal write origin: migration or user.")
    migration_version: str = Field(
        description="Expanded contract version used for migration."
    )


class FourDimensionProfileProjection(BaseModel):
    """普通画像页面可见的四维记录投影。

    来源引用、哈希、迁移版本和审计字段只保留在内部记录中，不进入普通 API
    响应或模型上下文；版本号作为修改/撤回的乐观锁令牌保留。
    """

    record_id: str = Field(description="稳定的四维画像记录标识。")
    dimension: FourDimension = Field(description="四个产品维度之一。")
    label: str = Field(description="维度中文标签。")
    content: str = Field(min_length=1, max_length=1000, description="画像记录内容。")
    first_stable_recorded_at: datetime = Field(
        description="首次稳定记录时间，修改不会重置。"
    )
    version: int = Field(ge=1, description="修改/撤回使用的乐观锁版本号。")
    status: FourDimensionRecordStatus = Field(description="记录状态。")


class FourDimensionProfileModifyRequest(BaseModel):
    """修改已有四维记录的乐观锁请求。"""

    content: str = Field(
        min_length=1, max_length=1000, description="Replacement content."
    )
    version: int = Field(ge=1, description="Version read by the caller.")


class FourDimensionProfileWithdrawRequest(BaseModel):
    """撤回已有四维记录的乐观锁请求。"""

    version: int = Field(ge=1, description="Version read by the caller.")


class FourDimensionLearningRecord(BaseModel):
    """旧知识状态记录交给教学域的内部交接合同。

    此合同不会由四维画像 API 返回，也不能被当作画像记录使用。
    """

    record_id: str = Field(description="Stable internal teaching handoff id.")
    owner_account_id: str = Field(description="Owning account identifier.")
    source_record_id: str = Field(description="Legacy knowledge-state identifier.")
    source_version: int = Field(ge=1, description="Legacy source version.")
    content_hash: str = Field(description="Hash of the legacy evidence body.")
    created_at: datetime = Field(description="Handoff creation time.")


class FourDimensionLegacyRecord(BaseModel):
    """无法安全映射的记录使用的内部不可变封存合同。"""

    archive_id: str = Field(description="Stable legacy archive identifier.")
    owner_account_id: str = Field(description="Owning account identifier.")
    source_record_id: str = Field(description="Legacy source identifier.")
    source_dimension: str = Field(description="Legacy source dimension.")
    content: str = Field(
        description="Archived body; never returned by the profile API."
    )
    content_hash: str = Field(description="Hash retained for migration audit.")
    reason_code: str = Field(description="Deterministic reason for preserving legacy.")
    created_at: datetime = Field(description="Archive creation time.")


class FourDimensionMigrationReport(BaseModel):
    """Account-scoped migration result without profile正文泄露."""

    report_id: str = Field(description="Stable migration report identifier.")
    owner_account_id: str = Field(description="Account migrated by this report.")
    migration_version: str = Field(description="Migration contract version.")
    status: FourDimensionMigrationStatus = Field(description="Migration outcome.")
    four_dimension_migrated: int = Field(
        ge=0, description="New four-dimension records created."
    )
    teaching_records_migrated: int = Field(
        ge=0, description="Knowledge records handed to teaching."
    )
    legacy_preserved: int = Field(
        ge=0, description="Records retained in the legacy archive."
    )
    skipped: int = Field(
        ge=0, description="Already migrated or intentionally skipped records."
    )
    failed: int = Field(
        ge=0, description="Records that failed deterministic migration."
    )
    stable_record_ids: list[str] = Field(
        default_factory=list,
        description="Stable target ids for audit tracing; never profile正文.",
    )
    failure_codes: list[str] = Field(
        default_factory=list, description="Safe retry diagnostics."
    )
    retryable: bool = Field(
        description="Whether the same account migration may be retried."
    )
    created_at: datetime = Field(description="Report creation time.")


class ProfileSliceItem(BaseModel):
    """One entry included in a compiled memory slice."""

    assertion_id: str = Field(description="Stable assertion identifier.")
    dimension: str = Field(description="Profile dimension.")
    value_or_rule: str = Field(description="Value or rule used in the slice.")
    inclusion_reason: str = Field(description="Why the entry was included.")
    sensitivity_class: ProfileSensitivityClass = Field(
        default=ProfileSensitivityClass.PREFERENCE,
        description="Sensitivity classification of the source assertion.",
    )
    expires_at: datetime | None = Field(
        default=None,
        description="Expiration of the source assertion.",
    )


class UnusedSliceItem(BaseModel):
    """One active assertion that was not included in the slice, with a reason."""

    assertion_id: str = Field(description="Stable assertion identifier.")
    dimension: str = Field(description="Profile dimension.")
    value_or_rule: str = Field(description="Value or rule of the assertion.")
    exclusion_reason: str = Field(description="Why the assertion was not included.")


class RejectedSliceItem(BaseModel):
    """One candidate that was not promoted and therefore not used."""

    candidate_id: str = Field(description="Stable candidate identifier.")
    dimension: str = Field(description="Profile dimension.")
    value_or_rule: str = Field(description="Proposed value or rule.")
    rejection_reason: str = Field(description="Why the candidate was not used.")


class ProfileSlice(BaseModel):
    """Minimal, authorized profile information compiled for a single run.

    A slice is the only long-term information carrier allowed into a model
    context. It records why each item was included, excluded or rejected so the
    context inspector can explain the personalization decision.
    """

    slice_id: str = Field(description="Stable slice identifier.")
    owner_account_id: str = Field(description="Owning account identifier.")
    run_id: str = Field(description="Run the slice is bound to.")
    purpose: str = Field(description="Declared processing purpose.")
    project_id: str | None = Field(
        default=None,
        description="Project scope for which the slice was compiled.",
    )
    included_items: list[ProfileSliceItem] = Field(
        default_factory=list,
        description="Promoted assertions included in the slice.",
    )
    unused_items: list[UnusedSliceItem] = Field(
        default_factory=list,
        description="Active assertions excluded from the slice with reasons.",
    )
    excluded_candidate_ids: list[str] = Field(
        default_factory=list,
        description="Candidates explicitly excluded from the slice.",
    )
    exclusion_reasons: dict[str, str] = Field(
        default_factory=dict,
        description="Reason each candidate was excluded.",
    )
    rejected_items: list[RejectedSliceItem] = Field(
        default_factory=list,
        description="Candidates rejected or not yet promoted with reasons.",
    )
    authorization_snapshot: str = Field(
        default="authz-1.0",
        description="Authorization policy version at compile time.",
    )
    key_epoch: str = Field(
        default="epoch-0",
        description="Key epoch under which the slice is bound.",
    )
    expires_at: datetime | None = Field(
        default=None,
        description="Expiration after which the slice must not be used.",
    )
    sensitivity_classes_allowed: list[ProfileSensitivityClass] = Field(
        default_factory=list,
        description="Sensitivity classes permitted in this slice.",
    )
    compiled_policy_version: str = Field(
        default="slice-1.0",
        description="Version of the slice compilation policy used.",
    length_budget: int = Field(
        default=6,
        ge=0,
        description="Maximum number of profile items allowed in this slice.",
    )
    )
    status: SliceStatus = Field(
        default=SliceStatus.ACTIVE,
        description="Lifecycle status of the slice.",
    )
    invalidated_at: datetime | None = Field(
        default=None,
        description="When the slice was invalidated.",
    )
    invalidation_reason: str | None = Field(
        default=None,
        description="Why the slice was invalidated.",
    )
    compiled_at: datetime = Field(description="When the slice was compiled.")


class ProfileSliceCompileRequest(BaseModel):
    """Request to compile a profile memory slice for a run."""

    purpose: str = Field(description="Declared processing purpose.")
    run_id: str = Field(description="Run identifier.")
    project_id: str | None = Field(
        default=None,
        description="Project scope for which the slice is compiled.",
    )
    sensitivity_classes: list[ProfileSensitivityClass] | None = Field(
        default=None,
        description="Sensitivity classes permitted in this slice; excludes prohibited by default.",
    )
    ttl_seconds: int = Field(
        default=3600,
        ge=1,
        description="Time-to-live for the slice in seconds.",
    )
    authorization_version: str = Field(
        default="authz-1.0",
        description="Authorization policy version snapshot.",
    )
    key_epoch: str = Field(
        default="epoch-0",
        description="Key epoch under which the slice is bound.",
    )


class ProfileError(BaseModel):
    """Uniform profile error response."""

    error: str = Field(description="Stable error code.")
    message: str = Field(description="Human-readable, non-leaking message.")
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Opaque detail safe for logging; must not expose internal state.",
    )


class ProfileAssertionVersion(BaseModel):
    """Immutable snapshot of a profile assertion at a point in its history.

    Rollback creates a new active version from a prior snapshot without erasing
    the audit chain. Each version stores the value and metadata at that point so
    that rollback is possible, but audit events and exports retain only the
    content hash rather than the full value, ensuring deleted content body is not
    preserved in the governance audit trail.
    """

    version_id: str = Field(description="Stable version snapshot identifier.")
    assertion_id: str = Field(description="Assertion this version belongs to.")
    owner_account_id: str = Field(description="Owning account identifier.")
    version: int = Field(description="Assertion version number this snapshot records.")
    canonical_dimension: str = Field(description="Profile dimension.")
    value_or_rule: str = Field(description="Value or rule at this version.")
    applicable_scenes: list[str] = Field(default_factory=list)
    status: AssertionStatus = Field(description="Assertion status at this version.")
    sensitivity_class: ProfileSensitivityClass = Field(
        default=ProfileSensitivityClass.PREFERENCE
    )
    promoted_from_candidate_id: str | None = Field(default=None)
    content_hash: str = Field(description="SHA-256 hash of value and scenes.")
    changed_at: datetime = Field(description="When this version was created.")
    changed_by: str = Field(description="Actor that created this version.")
    change_reason: str = Field(description="Human-readable reason for the change.")


class ProfileAssertionHistory(BaseModel):
    """Version history of a single profile assertion."""

    assertion_id: str = Field(description="Assertion identifier.")
    owner_account_id: str = Field(description="Owning account identifier.")
    current_version: int = Field(description="Current optimistic concurrency version.")
    versions: list[ProfileAssertionVersion] = Field(
        default_factory=list, description="Historical snapshots, oldest first."
    )


class ProfileAssertionModifyRequest(BaseModel):
    """Request to modify an active profile assertion, creating a new version."""

    value_or_rule: str = Field(description="New value or rule.")
    applicable_scenes: list[str] = Field(default_factory=list)
    reason: str = Field(description="Human-readable reason for the change.")


class ManualAssertionCreateRequest(BaseModel):
    """Request to manually create a governed profile record.

    The user declares the fact, its applicable scenes, sensitivity, authorization
    scope and a source note; the record is promoted immediately because the
    declarer is the owner, and every field is retained for audit.
    """

    dimension: ProfileDimension = Field(
        description="One of the nine profile dimensions."
    )
    value_or_rule: str = Field(
        description="The declared value or rule.",
        min_length=1,
        max_length=1000,
    )
    applicable_scenes: list[str] = Field(default_factory=list)
    sensitivity_class: ProfileSensitivityClass = Field(
        default=ProfileSensitivityClass.PREFERENCE,
        description="Sensitivity classification governing slice inclusion.",
    )
    authorization_scope: str = Field(
        default="general",
        description="Authorization scope; set by the user, never inferred.",
    )
    source_note: str = Field(
        default="用户手动记录",
        description="User-declared provenance note for the record.",
        max_length=500,
    )


class ProfileAssertionRollbackRequest(BaseModel):
    """Request to roll an assertion back to a previous version."""

    to_version: int = Field(ge=1, description="Target historical version number.")
    reason: str = Field(description="Human-readable reason for the rollback.")


class ProfileFreezeRequest(BaseModel):
    """Request carrying a reason for freeze / withdraw / unfreeze operations."""

    reason: str = Field(description="Human-readable reason for the operation.")


class ProfileDeleteRequest(BaseModel):
    """Request to delete a profile assertion."""

    reason: str = Field(description="Human-readable reason for deletion.")


class ProfileExportAssertion(BaseModel):
    """One assertion as it appears in a user export."""

    assertion_id: str = Field(description="Stable assertion identifier.")
    canonical_dimension: str = Field(description="Profile dimension.")
    status: AssertionStatus = Field(description="Current lifecycle status.")
    value_or_rule: str | None = Field(
        default=None,
        description="Current value; redacted when the assertion has been deleted.",
    )
    applicable_scenes: list[str] = Field(default_factory=list)
    version: int = Field(description="Current optimistic concurrency version.")
    content_hash: str = Field(description="SHA-256 hash of current value and scenes.")
    promoted_from_candidate_id: str | None = Field(default=None)
    last_used_at: datetime | None = Field(
        default=None,
        description="When the assertion was last included in an answer slice.",
    )
    created_at: datetime = Field(description="When the assertion was first promoted.")
    updated_at: datetime = Field(description="When the assertion was last changed.")
    deleted_at: datetime | None = Field(default=None)


class ProfileExport(BaseModel):
    """Structured, portable export of a user's evidence-backed profile.

    Exports include assertion metadata and content hashes so the user can verify
    integrity, but they omit deleted observed content and audit-unfriendly body
    copies. The export is itself scope-bound to the requesting account.
    """

    export_id: str = Field(description="Stable export identifier.")
    owner_account_id: str = Field(description="Account the export belongs to.")
    exported_at: datetime = Field(description="Export generation timestamp.")
    assertions: list[ProfileExportAssertion] = Field(
        default_factory=list, description="Current assertions."
    )
    history: dict[str, ProfileAssertionHistory] = Field(
        default_factory=dict,
        description="Version history keyed by assertion identifier.",
    )
    audit_event_refs: list[str] = Field(
        default_factory=list,
        description="References to governance audit events included in this export.",
    )


class ProfilePermission(BaseModel):
    """User-granted permission for low-risk automatic profile updates.

    Issue 26 (ADR-0002): a permission is bound to one auto-writable category and
    one applicable scene, defaults to off, and can only be set by the user
    through the account-scoped API — never inferred from silence, tone or past
    behavior.
    """

    account_id: str = Field(description="Owning account identifier.")
    dimension: ProfileDimension = Field(
        description="Auto-writable category (AUTO_WRITABLE_DIMENSIONS)."
    )
    scene: str = Field(description="Applicable scene, e.g. 'companion' or 'study'.")
    enabled: bool = Field(description="Whether automatic updates are permitted.")
    updated_at: datetime = Field(description="Last change timestamp.")


class ProfilePermissionUpdateRequest(BaseModel):
    """Request to enable or disable one low-risk automatic-update permission."""

    dimension: ProfileDimension = Field(
        description="Auto-writable category (AUTO_WRITABLE_DIMENSIONS)."
    )
    scene: str = Field(description="Applicable scene, e.g. 'companion' or 'study'.")
    enabled: bool = Field(description="True to enable, False to disable.")


class ProfileNotificationKind(StrEnum):
    """Kind of a profile notification delivered to the user."""

    AUTO_WRITE = "auto_write"
    CANDIDATE_PROPOSED = "candidate_proposed"
    INTENT_RECORDED = "intent_recorded"
    TRANSIENT_EMOTION = "transient_emotion"


class ProfileNotification(BaseModel):
    """Visible, source-backed notification produced by profile processing.

    ``recallable`` is true for auto-written records so the user can recall them
    with one click; the recall withdraws the record and blocks re-writing the
    same fact automatically.
    """

    notification_id: str = Field(description="Stable notification identifier.")
    owner_account_id: str = Field(description="Owning account identifier.")
    kind: ProfileNotificationKind = Field(description="Notification kind.")
    title: str = Field(description="Short Chinese title.")
    message: str = Field(description="Chinese message body.")
    source_ref: str = Field(
        description="Reference to the source, e.g. '<conversation_id>:<message_id>'."
    )
    source_text: str = Field(
        description="Source message text the notification refers to."
    )
    dimension: ProfileDimension | None = Field(
        default=None, description="Profile dimension the notification refers to."
    )
    scene: str | None = Field(default=None, description="Applicable scene.")
    assertion_id: str | None = Field(
        default=None, description="Target assertion for one-click recall."
    )
    candidate_id: str | None = Field(
        default=None, description="Target candidate, when proposed."
    )
    recallable: bool = Field(
        default=False, description="Whether one-click recall applies."
    )
    recalled_at: datetime | None = Field(
        default=None, description="When the record was recalled, if ever."
    )
    read_at: datetime | None = Field(
        default=None, description="When the user marked the notification read."
    )
    created_at: datetime = Field(description="Creation timestamp.")


class ProfileBatchCandidateDecisionRequest(BaseModel):
    """Request to apply one decision to multiple candidates at once.

    Batch decisions are idempotent: already-decided candidates whose status
    already reflects the requested decision are reported as succeeded, so a
    failed batch can be safely retried without duplicate writes.
    """

    candidate_ids: list[str] = Field(
        min_length=1,
        max_length=50,
        description="Candidate identifiers to decide (owned by the account).",
    )
    decision: DecisionType = Field(description="Decision applied to each candidate.")
    reason: str = Field(
        min_length=1,
        max_length=200,
        description="Shared human-readable rationale recorded for each decision.",
    )
    modified_value_or_rule: str | None = Field(
        default=None,
        description="Modified value when decision is 'modify'.",
    )
    modified_applicable_scenes: list[str] | None = Field(
        default=None,
        description="Modified applicable scenes when decision is 'modify' "
        "(mirrors the single-decision contract).",
    )


class ProfileBatchCandidateResult(BaseModel):
    """Result of a batch candidate decision."""

    succeeded: list[str] = Field(
        default_factory=list, description="Candidate ids successfully decided."
    )
    already_decided: list[str] = Field(
        default_factory=list,
        description="Candidate ids already in the target state (idempotent retry).",
    )
    failed: list[dict[str, str]] = Field(
        default_factory=list,
        description="Failed candidate ids with a safe Chinese reason.",
    )
