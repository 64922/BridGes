"""Scientific expression task contracts.

These models define the public surface of the expression pipeline introduced in
T025: the expression task brief, audience model, genre contract, argument plan,
fact-lock-bound draft spans and the expression quality gate. They are the
authoritative shape of CONTRACT-CREATE-01.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from science_companion.contracts.ai import ModelRunLock
from science_companion.contracts.science import (
    WordingStrength,
)


class Genre(StrEnum):
    """Supported scientific expression genres."""

    POPULAR_SCIENCE = "popular_science"
    LECTURE_SCRIPT = "lecture_script"
    RESEARCH_REPORT = "research_report"
    PAPER_ASSIST = "paper_assist"


class RiskTier(StrEnum):
    """Risk tier of an expression task."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ExpressionDraftStatus(StrEnum):
    """Lifecycle status of an expression draft."""

    DRAFTED = "drafted"
    BLOCKED = "blocked"
    WAITING_HUMAN = "waiting_human"


class ArgumentNodeRole(StrEnum):
    """Role of a node in the argument plan."""

    QUESTION = "question"
    CLAIM = "claim"
    EVIDENCE = "evidence"
    EXPLANATION = "explanation"
    EXAMPLE = "example"
    LIMITATION = "limitation"
    COUNTERPOINT = "counterpoint"
    TRANSITION = "transition"
    ACTION = "action"


class AudienceModel(BaseModel):
    """Minimal, task-scoped audience model.

    The audience model is not a personality profile; it only carries the
    information needed to adapt presentation without changing scientific claims.
    """

    audience_id: str = Field(description="Stable audience identifier.")
    prior_knowledge: str = Field(description="Expected prior knowledge.")
    terminology_familiarity: str = Field(description="Familiarity with domain terms.")
    intended_action: str | None = Field(
        default=None, description="What the audience should understand or do."
    )
    likely_misconceptions: list[str] = Field(
        default_factory=list, description="Known misconceptions to avoid reinforcing."
    )
    allowed_complexity: str = Field(description="Complexity ceiling for this task.")
    culture_locale: str = Field(default="zh-CN", description="Language and culture context.")
    accessibility_needs: list[str] = Field(
        default_factory=list, description="Accessibility requirements."
    )
    source: str = Field(
        default="user_specified",
        description="Where the model came from: user_specified, task_context, memory_slice.",
    )


class ExpressionBrief(BaseModel):
    """Expression task brief: the contract between user and expression engine."""

    brief_id: str = Field(description="Stable brief identifier.")
    task_goal: str = Field(description="What the expression must achieve.", min_length=1)
    deliverable_type: str = Field(
        description="Concrete deliverable, e.g. article, slides, script.", min_length=1
    )
    genre: Genre = Field(description="Genre contract to apply.")
    audience_id: str | None = Field(default=None, description="Audience identifier if known.")
    channel: str = Field(description="Publication or presentation channel.", min_length=1)
    length_or_duration: str | None = Field(default=None, description="Target length or duration.")
    language_locale: str = Field(default="zh-CN", description="Output language and locale.")
    risk_tier: RiskTier = Field(default=RiskTier.LOW, description="Risk tier.")
    required_claim_ids: list[str] = Field(
        default_factory=list,
        description="Claims that must appear in the draft.",
    )
    optional_claim_ids: list[str] = Field(
        default_factory=list,
        description="Claims that may appear if space and evidence allow.",
    )
    forbidden_content: list[str] = Field(
        default_factory=list,
        description="Content or approaches explicitly forbidden by the user.",
    )
    success_criteria: list[str] = Field(
        default_factory=list,
        description="Observable criteria that decide success.",
    )
    deadline_and_context: str | None = Field(
        default=None, description="Deadline and situational context."
    )
    memory_slice_id: str | None = Field(
        default=None, description="Memory slice bound to this expression task."
    )


class GenreContract(BaseModel):
    """Machine-readable contract for a scientific expression genre."""

    genre: Genre = Field(description="Genre this contract governs.")
    required_sections: list[str] = Field(
        default_factory=list,
        description="Sections the genre must contain.",
    )
    allowed_omissions: list[str] = Field(
        default_factory=list,
        description="Sections the genre may omit.",
    )
    tone_and_person: str = Field(default="", description="Tone and person constraints.")
    evidence_and_citation_presentation: str = Field(
        default="",
        description="How evidence and citations must be presented.",
    )
    colloquialism_ceiling: str = Field(
        default="moderate",
        description="Maximum colloquialism allowed.",
    )
    human_confirmation_required: bool = Field(
        default=False,
        description="Whether this genre requires explicit human confirmation.",
    )
    prohibited_behaviors: list[str] = Field(
        default_factory=list,
        description="Behaviors the genre must not allow.",
    )
    applicable_report_standard: str | None = Field(
        default=None,
        description="Applicable reporting standard, e.g. CONSORT, STROBE.",
    )


class ArgumentNode(BaseModel):
    """A single node in the argument plan."""

    argument_node_id: str = Field(description="Stable node identifier.")
    role: ArgumentNodeRole = Field(description="Role of the node.")
    claim_ids: list[str] = Field(
        default_factory=list,
        description="Claims this node carries.",
    )
    depends_on: list[str] = Field(
        default_factory=list,
        description="Node ids this node depends on.",
    )
    audience_purpose: str | None = Field(
        default=None, description="Why this node exists for the audience."
    )
    order: int = Field(default=0, description="Order in the argument plan.")
    omission_policy: str = Field(
        default="required",
        description="Whether the node may be omitted: required, optional, context_dependent.",
    )


class ArgumentPlan(BaseModel):
    """Ordered plan that structures the draft before wording is chosen."""

    plan_id: str = Field(description="Stable plan identifier.")
    brief_id: str = Field(description="Brief this plan serves.")
    graph_id: str = Field(description="Claim graph the plan is built from.")
    nodes: list[ArgumentNode] = Field(default_factory=list, description="Plan nodes.")


class DraftSpan(BaseModel):
    """A single text span in the expression draft.

    Each span binds to claims, evidence, citations and fact locks so that every
    important judgment can be traced back to its scientific justification.
    """

    span_id: str = Field(description="Stable span identifier.")
    text: str = Field(description="Span text content.")
    argument_node_ids: list[str] = Field(
        default_factory=list,
        description="Argument nodes this span realizes.",
    )
    claim_ids: list[str] = Field(
        default_factory=list,
        description="Claims this span expresses.",
    )
    citation_ids: list[str] = Field(
        default_factory=list,
        description="Citations rendered in this span.",
    )
    fact_lock_ids: list[str] = Field(
        default_factory=list,
        description="Fact locks that constrain this span.",
    )
    generated_by_run: str | None = Field(
        default=None, description="Run id of the generator that produced the span."
    )
    style_policy_version: str = Field(default="1", description="Style policy version.")


class ExpressionDraft(BaseModel):
    """A fact-lock-bound expression draft."""

    draft_id: str = Field(description="Stable draft identifier.")
    brief_id: str = Field(description="Brief this draft serves.")
    graph_id: str = Field(description="Claim graph the draft is built from.")
    account_id: str = Field(description="Owning account.")
    project_id: str | None = Field(default=None, description="Project scope if any.")
    status: ExpressionDraftStatus = Field(description="Draft lifecycle status.")
    status_reason: str | None = Field(default=None, description="Why the draft has this status.")
    argument_plan: ArgumentPlan = Field(description="Argument plan for the draft.")
    spans: list[DraftSpan] = Field(default_factory=list, description="Draft spans.")
    fact_lock_set_id: str = Field(description="Fact lock set constraining the draft.")
    memory_slice_id: str | None = Field(
        default=None, description="Memory slice used for personalization."
    )
    personalization_note: str | None = Field(
        default=None,
        description="How personalization influenced presentation, without changing facts.",
    )
    wording_strength_ceiling: WordingStrength = Field(
        default=WordingStrength.UNASSESSABLE,
        description="Global wording strength ceiling derived from evidence.",
    )
    model_run_lock: ModelRunLock | None = Field(
        default=None, description="Model run lock for the generator.")
    created_at: datetime = Field(description="Draft creation timestamp.")


class ExpressionGateCheck(StrEnum):
    """Named checks performed by the expression quality gate."""

    BRIEF_COMPLETE = "brief_complete"
    REQUIRED_CLAIMS_PRESENT = "required_claims_present"
    KEY_CLAIMS_FACT_LOCKED = "key_claims_fact_locked"
    MEMORY_SLICE_USABLE = "memory_slice_usable"
    GENRE_DUTY_KNOWN = "genre_duty_known"
    SOURCE_EVIDENCE_PRESENT = "source_evidence_present"
    RISK_TIER_HUMAN_REVIEW = "risk_tier_human_review"


class ExpressionGateResult(BaseModel):
    """Result of running the expression quality gate over a draft."""

    passed: bool = Field(description="Whether the draft may proceed.")
    draft_status: ExpressionDraftStatus = Field(description="Derived draft status.")
    checks: dict[ExpressionGateCheck, bool] = Field(default_factory=dict)
    failed_checks: list[ExpressionGateCheck] = Field(default_factory=list)
    blocked_claim_ids: list[str] = Field(default_factory=list)
    reason: str | None = Field(default=None, description="Human-readable gate summary.")


class ExpressionDraftRequest(BaseModel):
    """Request to generate a fact-lock-bound expression draft."""

    brief: ExpressionBrief = Field(description="Expression task brief.")
    graph_id: str = Field(description="Claim graph to base the draft on.")
    project_id: str | None = Field(default=None, description="Project scope.")
    memory_slice_id: str | None = Field(
        default=None, description="Optional memory slice for personalization."
    )


class ExpressionDraftResult(BaseModel):
    """Result of generating an expression draft."""

    draft: ExpressionDraft = Field(description="Generated draft.")
    gate: ExpressionGateResult = Field(description="Expression quality gate result.")
    model_run_lock: ModelRunLock | None = Field(
        default=None, description="Model run lock for the generator."
    )


class ExpressionError(BaseModel):
    """Uniform expression error response."""

    error: str = Field(description="Stable error code.")
    message: str = Field(description="Human-readable, non-leaking message.")
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Opaque detail safe for logging; must not expose internal state.",
    )


__all__ = [
    "Genre",
    "RiskTier",
    "ExpressionDraftStatus",
    "ArgumentNodeRole",
    "AudienceModel",
    "ExpressionBrief",
    "GenreContract",
    "ArgumentNode",
    "ArgumentPlan",
    "DraftSpan",
    "ExpressionDraft",
    "ExpressionGateCheck",
    "ExpressionGateResult",
    "ExpressionDraftRequest",
    "ExpressionDraftResult",
    "ExpressionError",
]
