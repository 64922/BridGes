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
    """Lifecycle status of a promoted profile assertion."""

    ACTIVE = "active"
    FROZEN = "frozen"
    STALE = "stale"
    DELETED = "deleted"


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
    source_type: ProfileSourceType = Field(description="How the observation originated.")
    source_ref: str = Field(
        description="Reference to the source object (conversation, run, import, etc.)."
    )
    source_span_or_event: str = Field(
        description="Specific span or event within the source (message id, turn, etc.)."
    )
    scene: str = Field(description="Scene or situation in which the signal occurred.")
    purpose: str = Field(description="Declared purpose for which the observation was collected.")
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
    content_hash: str = Field(description="SHA-256 hash of observed_content and source metadata.")
    status: ObservationStatus = Field(description="Lifecycle status.")
    created_at: datetime = Field(description="Creation timestamp.")
    updated_at: datetime = Field(description="Last update timestamp.")


class ProfileObservationCreateRequest(BaseModel):
    """Request to record a new profile observation."""

    owner_account_id: str = Field(description="Owning account identifier.")
    project_id: str | None = Field(default=None, description="Optional project context.")
    source_type: ProfileSourceType = Field(description="How the observation originated.")
    source_ref: str = Field(description="Reference to the source object.")
    source_span_or_event: str = Field(description="Specific span or event within the source.")
    scene: str = Field(description="Scene or situation.")
    purpose: str = Field(description="Declared purpose.")
    observed_content: str = Field(description="Observed content.")
    signal_kind: ProfileSignalKind = Field(description="Kind of signal.")
    extractor_and_version: str = Field(description="Extractor and version.")
    model_rationale: str | None = Field(default=None, description="Extractor rationale.")
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
    canonical_dimension: str = Field(description="Profile dimension (e.g. 'expression_brevity').")
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
    authorization_scope: str = Field(default="general", description="Authorization scope.")
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
    created_at: datetime = Field(description="Creation timestamp.")
    updated_at: datetime = Field(description="Last update timestamp.")


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


class ProfileAssertionRollbackRequest(BaseModel):
    """Request to roll an assertion back to a previous version."""

    to_version: int = Field(ge=1, description="Target historical version number.")
    reason: str = Field(description="Human-readable reason for the rollback.")


class ProfileFreezeRequest(BaseModel):
    """Request to freeze a profile assertion."""

    reason: str = Field(description="Human-readable reason for freezing.")


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


