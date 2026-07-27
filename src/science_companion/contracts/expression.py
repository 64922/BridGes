"""Scientific expression task contracts.

These models define the public surface of the expression pipeline introduced in
T025: the expression task brief, audience model, genre contract, argument plan,
fact-lock-bound draft spans and the expression quality gate. They are the
authoritative shape of CONTRACT-CREATE-01.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from science_companion.contracts.ai import ModelRunLock
from science_companion.contracts.projects import ObjectRef
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
    PREREQUISITE = "prerequisite"
    COUNTERPOINT = "counterpoint"
    TRANSITION = "transition"
    ACTION = "action"
    ANALYSIS = "analysis"
    INTERPRETATION = "interpretation"


class GenreElementRole(StrEnum):
    """Role of a genre-specific structural element within a draft.

    Popular science and lecture script genres expose their required sections as
    first-class, auditable elements so users can verify that analogies, analogy
    boundaries, learning objectives and comprehension checks are present and
    distinct. Research report and paper-assist genres add observation,
    analysis, interpretation, limitation, next-step and author-assistance
    elements (T027).
    """

    CORE_CONCEPT = "core_concept"
    ANALOGY = "analogy"
    ANALOGY_BOUNDARY = "analogy_boundary"
    ACTION_RELEVANCE = "action_relevance"
    LEARNING_OBJECTIVE = "learning_objective"
    PREREQUISITE = "prerequisite"
    COMPREHENSION_CHECK = "comprehension_check"
    PRACTICE_PAUSE = "practice_pause"
    OBSERVATION = "observation"
    ANALYSIS = "analysis"
    INTERPRETATION = "interpretation"
    LIMITATION = "limitation"
    NEXT_STEP = "next_step"
    STRUCTURE_SUGGESTION = "structure_suggestion"
    LANGUAGE_SUGGESTION = "language_suggestion"
    CITATION_VERIFICATION = "citation_verification"
    ARGUMENT_SUGGESTION = "argument_suggestion"
    AI_DISCLOSURE_REMINDER = "ai_disclosure_reminder"


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
    requested_strength_upgrade: bool = Field(
        default=False,
        description=(
            "Whether the user explicitly asked to strengthen wording beyond the "
            "evidence-derived ceiling."
        ),
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


class PopularScienceElement(BaseModel):
    """A structural element required by the popular-science genre contract.

    The element distinguishes the core concept from analogies, marks the boundary
    where an analogy stops being a mechanism, and links the content to audience
    action relevance.
    """

    element_id: str = Field(description="Stable element identifier.")
    role: GenreElementRole = Field(description="Role of this element.")
    span_ids: list[str] = Field(
        default_factory=list,
        description="Draft spans that realize this element.",
    )
    claim_ids: list[str] = Field(
        default_factory=list,
        description="Claims bound to this element.",
    )
    analogy_target: str | None = Field(
        default=None,
        description="For analogies: the concrete target the abstract concept is mapped to.",
    )
    boundary_note: str | None = Field(
        default=None,
        description="For analogy boundaries: where the analogy no longer applies.",
    )
    action_relevance: str | None = Field(
        default=None,
        description="For action relevance: why this matters to the audience.",
    )


class LectureScriptElement(BaseModel):
    """A structural element required by the lecture-script genre contract.

    The element exposes learning objectives, prerequisites, comprehension checks
    and practice pauses as first-class objects so a teacher can review and
    adjust them independently of the wording.
    """

    element_id: str = Field(description="Stable element identifier.")
    role: GenreElementRole = Field(description="Role of this element.")
    span_ids: list[str] = Field(
        default_factory=list,
        description="Draft spans that realize this element.",
    )
    claim_ids: list[str] = Field(
        default_factory=list,
        description="Claims bound to this element.",
    )
    checkpoint_question: str | None = Field(
        default=None,
        description="For comprehension checks: the question posed to learners.",
    )
    expected_answer: str | None = Field(
        default=None,
        description="For comprehension checks: the expected answer or key elements.",
    )
    pause_prompt: str | None = Field(
        default=None,
        description="For practice pauses: the facilitator prompt.",
    )


class ResearchReportElement(BaseModel):
    """A structural element required by the research-report genre contract (T027).

    Research reports must keep observation, analysis, interpretation, limitation
    and next-step distinct so peers can judge the boundary between data and
    inference.
    """

    element_id: str = Field(description="Stable element identifier.")
    role: GenreElementRole = Field(description="Role of this element.")
    span_ids: list[str] = Field(
        default_factory=list,
        description="Draft spans that realize this element.",
    )
    claim_ids: list[str] = Field(
        default_factory=list,
        description="Claims bound to this element.",
    )
    observation_data_ref: str | None = Field(
        default=None,
        description="For observations: reference to the data or result being reported.",
    )
    analysis_method: str | None = Field(
        default=None,
        description="For analyses: the method or procedure applied to the data.",
    )
    interpretation_scope: str | None = Field(
        default=None,
        description="For interpretations: scope within which the inference holds.",
    )
    limitation_note: str | None = Field(
        default=None,
        description="For limitations: explicit constraint on interpretation.",
    )
    next_step_action: str | None = Field(
        default=None,
        description="For next steps: concrete follow-up study or verification.",
    )


class PaperAssistElement(BaseModel):
    """A structural element for the paper-assist genre contract (T027).

    Paper-assist elements make the system's assistance scope visible: it may
    suggest structure, language, citation checks and argument improvements, but
    must not fabricate data, experiments or author decisions.
    """

    element_id: str = Field(description="Stable element identifier.")
    role: GenreElementRole = Field(description="Role of this element.")
    span_ids: list[str] = Field(
        default_factory=list,
        description="Draft spans that realize this element.",
    )
    claim_ids: list[str] = Field(
        default_factory=list,
        description="Claims bound to this element.",
    )
    suggestion_text: str | None = Field(
        default=None,
        description="For suggestions: concrete, non-fabricated advice.",
    )
    verified: bool | None = Field(
        default=None,
        description="For citation verification: whether the citation was located.",
    )
    disclosure_text: str | None = Field(
        default=None,
        description="For AI disclosure reminders: text the author should review.",
    )
    requires_author_confirm: bool = Field(
        default=False,
        description="Whether this element requires explicit author confirmation.",
    )


class AuthorResponsibilityStatement(BaseModel):
    """Author responsibility declaration attached to paper-assist drafts (T027).

    The statement records that the AI tool is an assistant, not an author, and
    that the human author remains responsible for data accuracy, citation
    integrity, disclosure and final submission decisions.
    """

    statement_id: str = Field(description="Stable statement identifier.")
    draft_id: str = Field(description="Draft this statement belongs to.")
    genre: Genre = Field(description="Genre this statement applies to.")
    responsibility_text: str = Field(
        description="Human-readable responsibility and AI-disclosure text."
    )
    ai_disclosure_required: bool = Field(
        default=True,
        description="Whether the target venue requires AI-use disclosure.",
    )
    author_confirm_required: bool = Field(
        default=True,
        description="Whether the author must explicitly confirm the statement.",
    )
    confirmed_at: datetime | None = Field(
        default=None, description="When the author confirmed the statement."
    )
    confirmed_by: str | None = Field(
        default=None, description="Account identifier of the confirming author."
    )


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


class ReviewFindingKind(StrEnum):
    """Kind of genre-rule finding."""

    REQUIRED = "required"
    PROHIBITED = "prohibited"
    MISSING = "missing"
    PRESERVED = "preserved"


class ReviewFindingSeverity(StrEnum):
    """Severity of a review finding."""

    INFO = "info"
    WARNING = "warning"
    BLOCKING = "blocking"


class ReviewFinding(BaseModel):
    """A single finding explaining why a genre rule requires or prohibits content.

    Findings are user-visible justifications tied to spans, claims or the whole
    draft. They answer "why must this be here?" or "why is this not allowed?".
    """

    finding_id: str = Field(description="Stable finding identifier.")
    kind: ReviewFindingKind = Field(description="Kind of finding.")
    severity: ReviewFindingSeverity = Field(description="Severity of the finding.")
    rule: str = Field(description="The genre rule that was checked.")
    span_id: str | None = Field(
        default=None, description="Span affected, if applicable."
    )
    claim_id: str | None = Field(
        default=None, description="Claim affected, if applicable."
    )
    reason: str = Field(description="Why the rule requires or prohibits this.")
    remediation: str | None = Field(
        default=None,
        description="What to do if the finding indicates a problem.",
    )


class ReviewReport(BaseModel):
    """Genre-rule compliance review report for an expression draft."""

    report_id: str = Field(description="Stable report identifier.")
    draft_id: str = Field(description="Draft this report reviews.")
    genre: Genre = Field(description="Genre the report evaluates.")
    passed: bool = Field(
        description="Whether the draft satisfies all blocking genre rules."
    )
    findings: list[ReviewFinding] = Field(
        default_factory=list, description="Findings explaining rule application."
    )


class StyleIssueType(StrEnum):
    """Categories of Chinese expression issues diagnosed by T028.

    These map to the six observable dimensions of human-flavored expression:
    task truth, audience fit, argument clarity, Chinese naturalness, restraint,
    and accountability. The diagnostic engine never emits an "AI probability".
    """

    TEMPLATE_PATTERN = "template_pattern"
    TRANSLATION_PATTERN = "translation_pattern"
    RHYTHM_ISSUE = "rhythm_issue"
    TONE_BOUNDARY = "tone_boundary"
    VAGUE_CONTENT = "vague_content"
    MECHANICAL_ARGUMENT = "mechanical_argument"
    STIFF_LANGUAGE = "stiff_language"
    FORMAT_IMBALANCE = "format_imbalance"
    CONVERSATION_RESIDUE = "conversation_residue"
    SCIENTIFIC_OVERREACH = "scientific_overreach"


class StyleDiagnosticSeverity(StrEnum):
    """Severity of a style diagnostic finding."""

    INFO = "info"
    SUGGESTION = "suggestion"
    WARNING = "warning"
    BLOCKING = "blocking"


class StylePolicy(BaseModel):
    """Style policy synthesized for a single expression task.

    The policy constrains wording without changing fact locks. It is built from
    the genre contract, project vocabulary, public rules and the authorized
    memory slice.
    """

    policy_id: str = Field(description="Stable policy identifier.")
    draft_id: str | None = Field(default=None, description="Draft this policy serves.")
    genre: Genre = Field(description="Genre the policy applies to.")
    locale: str = Field(default="zh-CN", description="Target language and locale.")
    max_sentence_length: int | None = Field(
        default=None, description="Suggested maximum sentence length in characters."
    )
    preferred_term_style: str | None = Field(
        default=None, description="Term familiarity level for this audience."
    )
    forbidden_phrases: list[str] = Field(
        default_factory=list,
        description="Phrases explicitly forbidden by genre or project rules.",
    )
    required_qualifier_style: str | None = Field(
        default=None,
        description="How uncertainty and limitations must be expressed.",
    )
    personalization_note: str | None = Field(
        default=None,
        description="How the memory slice may influence presentation.",
    )


class StyleDiagnosticFinding(BaseModel):
    """A single Chinese expression issue with a suggested local patch.

    Findings anchor to a concrete span and text fragment so the user can review
    them one by one. They explain why the fragment may hurt the current genre
    and offer a non-binding patch that must still preserve fact locks.
    """

    finding_id: str = Field(description="Stable finding identifier.")
    span_id: str | None = Field(
        default=None, description="Span containing the flagged text."
    )
    issue_type: StyleIssueType = Field(description="Category of expression issue.")
    severity: StyleDiagnosticSeverity = Field(description="Severity of the issue.")
    original_text: str = Field(description="Concrete flagged text fragment.")
    reason: str = Field(description="Why this fragment is flagged for the genre.")
    suggested_patch: str | None = Field(
        default=None, description="Suggested wording that preserves fact locks."
    )
    genre_rule: str | None = Field(
        default=None, description="Genre or style rule motivating the finding."
    )


class StyleDiagnosticReport(BaseModel):
    """Report from the Chinese human-flavor diagnostic engine (T028).

    The report lists concrete text fragments, issue types and reasons. It never
    uses an AI-detector score as a pass/fail gate.
    """

    report_id: str = Field(description="Stable report identifier.")
    draft_id: str = Field(description="Draft this report diagnoses.")
    findings: list[StyleDiagnosticFinding] = Field(
        default_factory=list, description="Diagnostic findings."
    )
    ai_detector_score: float | None = Field(
        default=None,
        description="Optional detector score recorded for transparency only.",
    )
    ai_detector_used_as_gate: bool = Field(
        default=False,
        description="Always false: AI detector scores are not used as gates.",
    )
    passed: bool = Field(
        description=(
            "True when no blocking findings remain and the diagnostic is complete."
        )
    )


class PatchAction(StrEnum):
    """User decision on a single revision patch."""

    ACCEPT = "accept"
    REJECT = "reject"
    REWRITE = "rewrite"


class FactLockInvariance(BaseModel):
    """Evidence that a patch or rewrite preserved scientific boundaries.

    The comparison covers fact locks, claim ids, citation ids and the evidence-
    derived wording strength ceiling.
    """

    original_span_text: str = Field(description="Span text before the change.")
    patched_span_text: str = Field(description="Span text after the change.")
    claim_ids_preserved: bool = Field(description="Claim ids unchanged.")
    citation_ids_preserved: bool = Field(description="Citation ids unchanged.")
    fact_lock_ids_preserved: bool = Field(description="Fact lock ids unchanged.")
    wording_strength_ceiling_preserved: bool = Field(
        description="Wording strength ceiling unchanged."
    )
    numeric_values_preserved: bool = Field(description="Numeric values unchanged.")
    units_preserved: bool = Field(description="Units unchanged.")
    qualifiers_preserved: bool = Field(description="Qualifiers and limitations unchanged.")
    passed: bool = Field(description="Whether all invariance checks hold.")


class RevisionPatch(BaseModel):
    """A local wording patch that must preserve fact locks and citations.

    Each patch records the original span text, the patched text, the diagnostic
    finding that motivated it, and an explicit fact-lock invariance check.
    """

    patch_id: str = Field(description="Stable patch identifier.")
    finding_id: str | None = Field(
        default=None, description="Diagnostic finding this patch addresses."
    )
    target_span_id: str = Field(description="Span the patch applies to.")
    original_text: str = Field(description="Text before the patch.")
    patched_text: str = Field(description="Text after the patch.")
    issue_type: StyleIssueType = Field(description="Category of issue being fixed.")
    reason: str = Field(description="Why the patch is suggested.")
    fact_lock_invariance: FactLockInvariance | None = Field(
        default=None, description="Fact-lock comparison before and after the patch."
    )
    applied: bool = Field(default=False, description="Whether the patch is applied.")
    applied_at: datetime | None = Field(default=None, description="When the patch was applied.")
    rejected: bool = Field(default=False, description="Whether the user rejected the patch.")
    user_rewrite: str | None = Field(
        default=None, description="User-provided alternative to the suggested patch."
    )


class UserFeedbackTarget(StrEnum):
    """Routing target for user feedback on an expression draft."""

    CURRENT_VERSION = "current_version"
    CANDIDATE_PREFERENCE = "candidate_preference"
    LEARNING_RECORD = "learning_record"
    FACT_REVIEW = "fact_review"


class UserFeedback(BaseModel):
    """A single piece of user feedback with explicit routing.

    Feedback is first applied to the current artifact; only stable, authorized
    signals are routed to long-term preference or learning record. Factual
    corrections always go to fact review and never become style preferences.
    """

    feedback_id: str = Field(description="Stable feedback identifier.")
    draft_id: str = Field(description="Draft the feedback relates to.")
    target: UserFeedbackTarget = Field(description="Where the feedback is routed.")
    message: str = Field(description="User-facing feedback text.")
    referenced_span_id: str | None = Field(
        default=None, description="Span the feedback refers to, if any."
    )
    referenced_claim_id: str | None = Field(
        default=None, description="Claim the feedback refers to, if any."
    )
    creates_candidate_preference: bool = Field(
        default=False,
        description="Whether this feedback may form a candidate preference.",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Timestamp.")


class StyleDiagnosticRequest(BaseModel):
    """Request to run the Chinese expression diagnostic on a draft."""

    draft_id: str = Field(description="Draft to diagnose.")
    project_id: str | None = Field(default=None, description="Project scope if any.")


class StyleDiagnosticResult(BaseModel):
    """Result of running the Chinese expression diagnostic."""

    draft: ExpressionDraft = Field(description="Draft with diagnostic report attached.")
    report: StyleDiagnosticReport = Field(description="Diagnostic report.")
    gate: ExpressionGateResult | None = Field(
        default=None, description="Re-evaluated gate result after diagnostic."
    )


class ApplyRevisionPatchRequest(BaseModel):
    """Request to accept, reject or rewrite a revision patch."""

    action: PatchAction = Field(description="User action.")
    rewrite_text: str | None = Field(
        default=None, description="User rewrite when action is REWRITE."
    )


class ApplyRevisionPatchResult(BaseModel):
    """Result of applying a revision patch."""

    patch_id: str = Field(description="Patch identifier.")
    draft: ExpressionDraft = Field(description="Draft after the action.")
    invariance: FactLockInvariance = Field(description="Invariance evidence.")


class SubmitExpressionFeedbackRequest(BaseModel):
    """Request to submit user feedback for an expression draft."""

    target: UserFeedbackTarget = Field(description="Routing target.")
    message: str = Field(description="Feedback text.")
    referenced_span_id: str | None = Field(default=None)
    referenced_claim_id: str | None = Field(default=None)


class SubmitExpressionFeedbackResult(BaseModel):
    """Result of submitting user feedback."""

    feedback_id: str = Field(description="Feedback identifier.")
    draft: ExpressionDraft = Field(description="Draft with feedback logged.")
    routed_to: UserFeedbackTarget = Field(description="Confirmed routing target.")


# T029: artifact trust status and human decisions must be defined before
# ExpressionDraft because the draft model references them.
class ArtifactTrustStatus(StrEnum):
    """Scientific trust state of an expression artifact (draft)."""

    DRAFT = "draft"
    EVIDENCE_BOUND = "evidence_bound"
    QUALIFIED = "qualified"
    APPROVED = "approved"
    CONFLICTED = "conflicted"
    INVALIDATED = "invalidated"


class HumanDecisionType(StrEnum):
    """Named human decision on an expression artifact."""

    APPROVE = "approve"
    REJECT = "reject"
    REQUEST_CHANGES = "request_changes"


class HumanDecision(BaseModel):
    """A named, auditable human decision to approve, reject or revise an artifact."""

    decision_id: str = Field(description="Stable decision identifier.")
    target_artifact_id: str = Field(description="Draft this decision applies to.")
    account_id: str = Field(description="Account that made the decision.")
    decision: HumanDecisionType = Field(description="Decision type.")
    reason: str = Field(description="Human-readable rationale.")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Timestamp.")


class ArtifactVersion(BaseModel):
    """Version metadata for a publishable expression artifact."""

    version_id: str = Field(description="Stable version identifier.")
    draft_id: str = Field(description="Draft this version represents.")
    version_number: int = Field(ge=1, description="Monotonic version number.")
    artifact_trust_status: ArtifactTrustStatus = Field(
        description="Current trust state of the artifact."
    )
    created_at: datetime = Field(description="Version timestamp.")


class ExpressionDraft(BaseModel):
    """A fact-lock-bound expression draft."""

    draft_id: str = Field(description="Stable draft identifier.")
    brief_id: str = Field(description="Brief this draft serves.")
    genre: Genre = Field(description="Genre this draft was generated for.")
    brief: ExpressionBrief = Field(description="Expression brief this draft serves.")
    graph_id: str = Field(description="Claim graph the draft is built from.")
    account_id: str = Field(description="Owning account.")
    project_id: str | None = Field(default=None, description="Project scope if any.")
    run_id: str | None = Field(
        default=None, description="Workflow run this artifact belongs to, if any."
    )
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
    # T029: artifact trust status and approval decisions separate workflow success
    # from scientific approval and from publication eligibility.
    artifact_trust_status: ArtifactTrustStatus = Field(
        default=ArtifactTrustStatus.DRAFT,
        description="Scientific trust state of the artifact.",
    )
    approval_decisions: list[HumanDecision] = Field(
        default_factory=list,
        description="Human decisions recorded for this artifact.",
    )
    popular_science_elements: list[PopularScienceElement] = Field(
        default_factory=list,
        description="Genre-specific elements for popular science (T026).",
    )
    lecture_script_elements: list[LectureScriptElement] = Field(
        default_factory=list,
        description="Genre-specific elements for lecture script (T026).",
    )
    research_report_elements: list[ResearchReportElement] = Field(
        default_factory=list,
        description="Genre-specific elements for research report (T027).",
    )
    paper_assist_elements: list[PaperAssistElement] = Field(
        default_factory=list,
        description="Genre-specific elements for paper assist (T027).",
    )
    author_responsibility_statement: AuthorResponsibilityStatement | None = Field(
        default=None,
        description="Author responsibility statement for paper-assist drafts (T027).",
    )
    review_report: ReviewReport | None = Field(
        default=None,
        description="Genre-rule compliance review report (T026/T027).",
    )
    # T028: style policy, diagnostic report and revision patch state.
    style_policy: StylePolicy | None = Field(
        default=None,
        description="Style policy synthesized for this draft.",
    )
    style_diagnostic_report: StyleDiagnosticReport | None = Field(
        default=None,
        description="Chinese expression diagnostic report.",
    )
    pending_patches: list[RevisionPatch] = Field(
        default_factory=list,
        description="Suggested revision patches awaiting user decision.",
    )
    applied_patches: list[RevisionPatch] = Field(
        default_factory=list,
        description="Revision patches that have been applied to this draft.",
    )
    rejected_patches: list[RevisionPatch] = Field(
        default_factory=list,
        description="Revision patches the user explicitly rejected.",
    )
    feedback_log: list[UserFeedback] = Field(
        default_factory=list,
        description="User feedback submitted for this draft and its routing.",
    )
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
    STRENGTH_ESCALATION_HUMAN_REVIEW = "strength_escalation_human_review"
    POPULAR_SCIENCE_ELEMENTS_PRESENT = "popular_science_elements_present"
    LECTURE_SCRIPT_ELEMENTS_PRESENT = "lecture_script_elements_present"
    RESEARCH_REPORT_ELEMENTS_PRESENT = "research_report_elements_present"
    PAPER_ASSIST_ELEMENTS_PRESENT = "paper_assist_elements_present"
    AUTHOR_RESPONSIBILITY_PRESENT = "author_responsibility_present"
    PAPER_ASSIST_AUTHOR_CONFIRMATION_REQUIRED = "paper_assist_author_confirmation_required"
    # T028: human-flavor diagnostics and revision-loop checks.
    STYLE_DIAGNOSTIC_COMPLETE = "style_diagnostic_complete"
    AI_DETECTOR_NOT_GATE = "ai_detector_not_gate"


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
    run_id: str | None = Field(
        default=None, description="Optional workflow run this artifact belongs to."
    )


class ExpressionDraftResult(BaseModel):
    """Result of generating an expression draft."""

    draft: ExpressionDraft = Field(description="Generated draft.")
    gate: ExpressionGateResult = Field(description="Expression quality gate result.")
    model_run_lock: ModelRunLock | None = Field(
        default=None, description="Model run lock for the generator."
    )


class GenreConversionInvariance(BaseModel):
    """Evidence that a genre conversion preserved scientific boundaries (T026).

    When the same fact-lock set is rendered under a different genre, the
    conversion must not change claims, citations, fact locks or the evidence-
    derived wording strength ceiling.
    """

    original_genre: Genre = Field(description="Genre before conversion.")
    target_genre: Genre = Field(description="Genre after conversion.")
    fact_lock_set_id_preserved: bool = Field(
        description="Whether the same fact lock set id is used."
    )
    citation_ids_preserved: bool = Field(
        description="Whether all citation ids are preserved across spans."
    )
    claim_ids_preserved: bool = Field(
        description="Whether all claim ids are preserved across spans."
    )
    wording_strength_ceiling_preserved: bool = Field(
        description="Whether the wording strength ceiling is unchanged."
    )
    passed: bool = Field(description="Whether all invariants hold.")


class ConvertGenreRequest(BaseModel):
    """Request to convert an existing draft to a different genre."""

    target_genre: Genre = Field(description="Genre to convert the draft into.")
    project_id: str | None = Field(default=None, description="Project scope.")


class ConvertGenreResult(BaseModel):
    """Result of converting a draft to a different genre."""

    original_draft_id: str = Field(description="Original draft identifier.")
    converted_draft: ExpressionDraft = Field(description="Draft in the target genre.")
    invariance: GenreConversionInvariance = Field(
        description="Invariance evidence for the conversion."
    )


# ------------------------------------------------------------------------------
# T029: expression version comparison, release gate and publication contracts
# ------------------------------------------------------------------------------


class ReleaseGateCheck(StrEnum):
    """Named checks performed by the expression release gate."""

    EXPRESSION_GATE_PASSED = "expression_gate_passed"
    ARTIFACT_APPROVED = "artifact_approved"
    WORKFLOW_SUCCEEDED = "workflow_succeeded"
    NO_OPEN_HUMAN_TODOS = "no_open_human_todos"
    UPSTREAM_OBJECTS_ACTIVE = "upstream_objects_active"
    REQUIRED_HUMAN_CONFIRMATIONS_PRESENT = "required_human_confirmations_present"
    NO_BLOCKING_STYLE_FINDINGS = "no_blocking_style_findings"


class ReleaseEligibilityStatus(StrEnum):
    """High-level release eligibility state derived from release gate checks."""

    ELIGIBLE = "eligible"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_WORKFLOW = "waiting_workflow"
    WAITING_HUMAN_TODO = "waiting_human_todo"
    UPSTREAM_INVALIDATED = "upstream_invalidated"
    BLOCKED = "blocked"


class ReleaseGateResult(BaseModel):
    """Result of running the expression release gate over a draft."""

    passed: bool = Field(description="Whether the artifact may be published.")
    status: ReleaseEligibilityStatus = Field(description="Derived eligibility status.")
    checks: dict[ReleaseGateCheck, bool] = Field(default_factory=dict)
    failed_checks: list[ReleaseGateCheck] = Field(default_factory=list)
    blocked_claim_ids: list[str] = Field(default_factory=list)
    reason: str | None = Field(default=None, description="Human-readable gate summary.")
    upstream_invalid_object_refs: list[ObjectRef] = Field(
        default_factory=list,
        description="Upstream object refs that are revoked or tombstoned.",
    )


class VersionDifferenceField(StrEnum):
    """Fields that can differ between two expression artifact versions."""

    FACT_LOCK_SET = "fact_lock_set"
    CLAIM_IDS = "claim_ids"
    CITATION_IDS = "citation_ids"
    WORDING_STRENGTH_CEILING = "wording_strength_ceiling"
    ARGUMENT_PLAN = "argument_plan"
    SPAN_TEXT = "span_text"
    GENRE = "genre"
    MODEL_RUN_LOCK = "model_run_lock"
    APPLIED_PATCHES = "applied_patches"
    ARTIFACT_TRUST_STATUS = "artifact_trust_status"
    HUMAN_DECISIONS = "human_decisions"


class VersionDifference(BaseModel):
    """A single semantic difference between two artifact versions."""

    field: VersionDifferenceField = Field(description="What changed.")
    before: Any = Field(description="Value in the first version.")
    after: Any = Field(description="Value in the second version.")
    reason: str | None = Field(default=None, description="Why the difference matters.")


class VersionComparisonResult(BaseModel):
    """Result of comparing two expression artifact versions."""

    comparison_id: str = Field(description="Stable comparison identifier.")
    draft_id_a: str = Field(description="First draft.")
    draft_id_b: str = Field(description="Second draft.")
    differences: list[VersionDifference] = Field(
        default_factory=list, description="Semantic differences between versions."
    )
    release_eligibility_a: ReleaseGateResult = Field(
        description="Release gate result for the first version."
    )
    release_eligibility_b: ReleaseGateResult = Field(
        description="Release gate result for the second version."
    )
    only_b_is_publishable: bool = Field(
        default=False,
        description=(
            "True when version B is eligible and version A is not; used by the "
            "upgrade-or-publish seam."
        ),
    )


class PublishEvent(BaseModel):
    """Immutable record that an expression artifact was published."""

    event_id: str = Field(description="Stable publish event identifier.")
    draft_id: str = Field(description="Draft that was published.")
    version_id: str = Field(description="Artifact version id at publish time.")
    account_id: str = Field(description="Account that authorized publication.")
    project_id: str | None = Field(default=None, description="Project scope if any.")
    published_at: datetime = Field(description="Publication timestamp.")
    release_gate_result: ReleaseGateResult = Field(
        description="Release gate result that authorized publication."
    )
    human_decision_id: str = Field(description="Approval decision that authorized publication.")


class ApproveArtifactRequest(BaseModel):
    """Request to approve, reject or request changes for an expression artifact."""

    decision: HumanDecisionType = Field(default=HumanDecisionType.APPROVE)
    reason: str = Field(description="Human-readable rationale.", min_length=1)


class ApproveArtifactResult(BaseModel):
    """Result of recording a human decision on an expression artifact."""

    decision_id: str = Field(description="Decision identifier.")
    draft: ExpressionDraft = Field(description="Draft after the decision.")


class PublishArtifactRequest(BaseModel):
    """Request to publish an expression artifact."""

    run_id: str | None = Field(
        default=None, description="Workflow run whose success is required for release."
    )


class PublishArtifactResult(BaseModel):
    """Result of publishing an expression artifact."""

    event: PublishEvent = Field(description="Published event.")
    draft: ExpressionDraft = Field(description="Draft after publication.")


class CompareVersionsRequest(BaseModel):
    """Request to compare two expression artifact versions."""

    draft_id_a: str = Field(description="First draft identifier.")
    draft_id_b: str = Field(description="Second draft identifier.")


CompareVersionsResult = VersionComparisonResult


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
    "GenreElementRole",
    "AudienceModel",
    "ExpressionBrief",
    "GenreContract",
    "ArgumentNode",
    "ArgumentPlan",
    "PopularScienceElement",
    "LectureScriptElement",
    "ResearchReportElement",
    "PaperAssistElement",
    "AuthorResponsibilityStatement",
    "DraftSpan",
    "ReviewFindingKind",
    "ReviewFindingSeverity",
    "ReviewFinding",
    "ReviewReport",
    # T028: style diagnostics, revision patches and feedback routing.
    "StyleIssueType",
    "StyleDiagnosticSeverity",
    "StylePolicy",
    "StyleDiagnosticFinding",
    "StyleDiagnosticReport",
    "PatchAction",
    "FactLockInvariance",
    "RevisionPatch",
    "UserFeedbackTarget",
    "UserFeedback",
    "StyleDiagnosticRequest",
    "StyleDiagnosticResult",
    "ApplyRevisionPatchRequest",
    "ApplyRevisionPatchResult",
    "SubmitExpressionFeedbackRequest",
    "SubmitExpressionFeedbackResult",
    "ExpressionDraft",
    "ExpressionGateCheck",
    "ExpressionGateResult",
    "ExpressionDraftRequest",
    "ExpressionDraftResult",
    "GenreConversionInvariance",
    "ConvertGenreRequest",
    "ConvertGenreResult",
    # T029: version comparison, release gate and publication.
    "ArtifactTrustStatus",
    "HumanDecisionType",
    "HumanDecision",
    "ArtifactVersion",
    "ReleaseGateCheck",
    "ReleaseEligibilityStatus",
    "ReleaseGateResult",
    "VersionDifferenceField",
    "VersionDifference",
    "VersionComparisonResult",
    "PublishEvent",
    "ApproveArtifactRequest",
    "ApproveArtifactResult",
    "PublishArtifactRequest",
    "PublishArtifactResult",
    "CompareVersionsRequest",
    "CompareVersionsResult",
    "ExpressionError",
]
