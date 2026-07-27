"""Learning mission, diagnostic and knowledge-state contracts.

These models define the public surface of T021: a learning mission is the
purpose anchor for every teaching record; a diagnostic binds questions and
answers to scientific evidence and versions; knowledge states are revocable
judgements that can only be updated by observable learning evidence.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from science_companion.contracts.science import FactLock


class DiagnosticQuestionType(StrEnum):
    """Kind of diagnostic question."""

    RECALL = "recall"
    EXPLANATION = "explanation"
    COMPARISON = "comparison"
    APPLICATION = "application"
    COMPUTATION = "computation"
    META_COGNITIVE = "meta_cognitive"


class AnswerEvaluatedState(StrEnum):
    """Result of evaluating a single diagnostic answer."""

    CORRECT = "correct"
    PARTIAL = "partial"
    INCORRECT = "incorrect"
    NEEDS_REVIEW = "needs_review"


class DiagnosticRunStatus(StrEnum):
    """Lifecycle status of a diagnostic run."""

    DRAFT = "draft"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class KnowledgeStateStatus(StrEnum):
    """Current judgement of knowledge for a single concept."""

    UNKNOWN = "unknown"
    EMERGING = "emerging"
    SUPPORTED = "supported"
    ROBUST = "robust"
    STALE = "stale"


class KnowledgeConfidence(StrEnum):
    """Calibration of the knowledge-state judgement, not a percentage."""

    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"


class TeachingPlanStatus(StrEnum):
    """Lifecycle status of a teaching plan."""

    DRAFT = "draft"
    QUALIFIED = "qualified"
    BLOCKED = "blocked"


class LearningActivityType(StrEnum):
    """Activity that does not, by itself, prove mastery."""

    BROWSING = "browsing"
    COMPLETION = "completion"
    MODEL_GUESS = "model_guess"
    ASSESSMENT = "assessment"


class EvidenceRef(BaseModel):
    """Reference to a scientific evidence item that grounds a question or answer.

    Evidence refs point into the claim--evidence--citation graph (T015) or into
    imported sources (T013). They carry enough identity to audit the grounding
    without copying private source body.
    """

    claim_id: str | None = Field(default=None, description="Claim identifier.")
    evidence_id: str | None = Field(default=None, description="Evidence identifier.")
    source_id: str | None = Field(default=None, description="Source entry identifier.")
    document_id: str | None = Field(default=None, description="Document version identifier.")
    reason: str = Field(description="Why this evidence grounds the question or answer.")


class LearningMission(BaseModel):
    """A user's real learning goal and the contract that teaching must serve."""

    mission_id: str = Field(description="Stable mission identifier.")
    owner_account_id: str = Field(description="Owning account identifier.")
    project_id: str | None = Field(default=None, description="Project scope if any.")
    title: str = Field(description="Short human-readable title.")
    goal: str = Field(description="Real learning goal.")
    scope_concepts: list[str] = Field(description="Concepts covered by the mission.")
    constraints: list[str] = Field(
        default_factory=list, description="Constraints and scope limits."
    )
    success_criteria: list[str] = Field(description="Observable success criteria.")
    version: int = Field(default=1, ge=1, description="Optimistic concurrency version.")
    created_at: datetime = Field(description="Creation timestamp.")
    updated_at: datetime = Field(description="Last update timestamp.")


class LearningMissionCreateRequest(BaseModel):
    """Request to create a learning mission."""

    title: str = Field(description="Short human-readable title.", min_length=1)
    goal: str = Field(description="Real learning goal.", min_length=1)
    scope_concepts: list[str] = Field(description="Concepts covered by the mission.")
    constraints: list[str] = Field(
        default_factory=list, description="Constraints and scope limits."
    )
    success_criteria: list[str] = Field(description="Observable success criteria.")
    project_id: str | None = Field(default=None, description="Optional project scope.")


class DiagnosticQuestion(BaseModel):
    """A single diagnostic question bound to evidence and a concept."""

    question_id: str = Field(description="Stable question identifier.")
    mission_id: str = Field(description="Mission this question belongs to.")
    concept_id: str = Field(description="Concept being diagnosed.")
    question_text: str = Field(description="Question text.")
    question_type: DiagnosticQuestionType = Field(description="Kind of question.")
    expected_answer_hints: list[str] = Field(
        default_factory=list,
        description="Hints for what a correct answer must contain; not the only truth.",
    )
    evidence_refs: list[EvidenceRef] = Field(
        default_factory=list,
        description="Scientific evidence that grounds the question.",
    )
    version: int = Field(default=1, ge=1, description="Question version.")


class DiagnosticQuestionCreateRequest(BaseModel):
    """Request to add a question to a diagnostic run."""

    concept_id: str = Field(description="Concept being diagnosed.")
    question_text: str = Field(description="Question text.")
    question_type: DiagnosticQuestionType = Field(description="Kind of question.")
    expected_answer_hints: list[str] = Field(
        default_factory=list, description="Hints for a correct answer."
    )
    evidence_refs: list[EvidenceRef] = Field(
        default_factory=list, description="Scientific evidence grounding the question."
    )


class DiagnosticAnswerCreateRequest(BaseModel):
    """Request to record a user's answer to a diagnostic question."""

    question_id: str = Field(description="Question being answered.")
    response_text: str = Field(description="User's response.")
    evidence_refs: list[EvidenceRef] = Field(
        default_factory=list,
        description="Evidence the user cited to support the answer.",
    )
    evaluated_state: AnswerEvaluatedState = Field(description="Evaluated result.")
    evaluator: str = Field(description="Agent that evaluated the answer: rule, model, human.")
    evaluation_reason: str = Field(description="Transparent reason for the evaluation.")


class DiagnosticAnswer(BaseModel):
    """A user's answer to one diagnostic question, with evidence-backed evaluation."""

    answer_id: str = Field(description="Stable answer identifier.")
    run_id: str = Field(description="Diagnostic run this answer belongs to.")
    question_id: str = Field(description="Question being answered.")
    response_text: str = Field(description="User's response.")
    evidence_refs: list[EvidenceRef] = Field(
        default_factory=list,
        description="Evidence the user cited to support the answer.",
    )
    evaluated_state: AnswerEvaluatedState = Field(description="Evaluated result.")
    evaluator: str = Field(description="Agent that evaluated the answer: rule, model, human.")
    evaluation_reason: str = Field(description="Transparent reason for the evaluation.")
    created_at: datetime = Field(description="When the answer was recorded.")


class DiagnosticRun(BaseModel):
    """A versioned diagnostic session for a learning mission."""

    run_id: str = Field(description="Stable run identifier.")
    mission_id: str = Field(description="Mission being diagnosed.")
    owner_account_id: str = Field(description="Owning account identifier.")
    status: DiagnosticRunStatus = Field(default=DiagnosticRunStatus.DRAFT)
    questions: list[DiagnosticQuestion] = Field(default_factory=list)
    answers: list[DiagnosticAnswer] = Field(default_factory=list)
    version: int = Field(default=1, ge=1, description="Run version.")
    created_at: datetime = Field(description="Creation timestamp.")
    completed_at: datetime | None = Field(default=None, description="Completion timestamp.")


class KnowledgeState(BaseModel):
    """A revocable judgement of a user's current mastery of one concept.

    Knowledge states are updated only by observable learning evidence. They carry
    uncertainty, supporting and refuting records, scope, and the next task that
    would best validate the state.
    """

    state_id: str = Field(description="Stable state identifier.")
    mission_id: str = Field(description="Mission this state belongs to.")
    owner_account_id: str = Field(description="Owning account identifier.")
    concept_id: str = Field(description="Concept being judged.")
    status: KnowledgeStateStatus = Field(description="Current knowledge state.")
    confidence: KnowledgeConfidence = Field(
        default=KnowledgeConfidence.LOW,
        description="Calibration of the judgement.",
    )
    supporting_record_ids: list[str] = Field(
        default_factory=list, description="Answer/record ids that support the state."
    )
    refuting_record_ids: list[str] = Field(
        default_factory=list, description="Answer/record ids that refute or limit it."
    )
    uncertainty_reason: str | None = Field(
        default=None, description="Why confidence is not higher, or why state is unknown."
    )
    scope: str = Field(description="Scope within which the state applies.")
    next_validation_task: str = Field(description="Next task to validate or refine the state.")
    version: int = Field(default=1, ge=1, description="Optimistic concurrency version.")
    superseded_by_state_id: str | None = Field(
        default=None, description="Newer state version that replaces this one."
    )
    created_at: datetime = Field(description="Creation timestamp.")
    updated_at: datetime = Field(description="Last update timestamp.")


class DiagnosticResult(BaseModel):
    """Outcome of completing a diagnostic run, including ZPD and explanation."""

    result_id: str = Field(description="Stable result identifier.")
    run_id: str = Field(description="Diagnostic run that produced the result.")
    mission_id: str = Field(description="Mission the result belongs to.")
    owner_account_id: str = Field(description="Owning account identifier.")
    knowledge_states: list[KnowledgeState] = Field(description="Computed states per concept.")
    zpd_concepts: list[str] = Field(
        default_factory=list,
        description="Concepts within the zone of proximal development.",
    )
    ready_concepts: list[str] = Field(
        default_factory=list, description="Concepts judged robust or supported."
    )
    unknown_concepts: list[str] = Field(
        default_factory=list, description="Concepts with no evidence or judged unknown."
    )
    evidence_based_explanation: str = Field(
        description="Human-readable explanation grounded in diagnostic evidence."
    )
    next_recommended_task: str = Field(description="Next task to advance the mission.")
    created_at: datetime = Field(description="Result timestamp.")


class TeachingPlan(BaseModel):
    """A minimal teaching plan derived from a mission and its knowledge states.

    T022 will expand this into short lessons, examples and retrieval exercises.
    T021 only needs the plan as a stable contract that traces back to the mission
    and evidence.
    """

    plan_id: str = Field(description="Stable plan identifier.")
    mission_id: str = Field(description="Mission the plan serves.")
    owner_account_id: str = Field(description="Owning account identifier.")
    title: str = Field(description="Plan title.")
    learning_objective: str = Field(description="Single learning victory.")
    target_concepts: list[str] = Field(description="Concepts to address next.")
    prerequisites: list[str] = Field(description="Concepts assumed or to review first.")
    validation_task: str = Field(description="Task that will validate the learning objective.")
    evidence_refs: list[EvidenceRef] = Field(
        default_factory=list, description="Evidence grounding the plan."
    )
    status: TeachingPlanStatus = Field(default=TeachingPlanStatus.DRAFT)
    version: int = Field(default=1, ge=1, description="Plan version.")
    created_at: datetime = Field(description="Creation timestamp.")


class KnowledgeStateCorrection(BaseModel):
    """User correction of a knowledge-state judgement."""

    concept_id: str = Field(description="Concept being corrected.")
    corrected_state: KnowledgeStateStatus = Field(description="Corrected state.")
    reason: str = Field(description="Why the correction is being made.")


class LearningActivity(BaseModel):
    """Recorded activity that does not, by itself, update knowledge state."""

    activity_id: str = Field(description="Stable activity identifier.")
    mission_id: str = Field(description="Mission the activity belongs to.")
    owner_account_id: str = Field(description="Owning account identifier.")
    activity_type: LearningActivityType = Field(description="Type of activity.")
    concept_id: str | None = Field(default=None, description="Concept if any.")
    details: dict[str, Any] = Field(
        default_factory=dict, description="Opaque activity details safe to log."
    )
    created_at: datetime = Field(description="Activity timestamp.")


class LessonStatus(StrEnum):
    """Lifecycle status of a generated short lesson."""

    DRAFT = "draft"
    EVIDENCE_BOUND = "evidence_bound"
    QUALIFIED = "qualified"
    DEGRADED = "degraded"
    BLOCKED = "blocked"


class RetrievalExerciseType(StrEnum):
    """Retrieval-practice exercise types that require active recall.

    Browsing or completion-only activities are deliberately excluded.
    """

    RECALL = "recall"
    EXPLANATION = "explanation"
    COMPUTATION = "computation"
    COMPARISON = "comparison"
    APPLICATION = "application"


class LessonExample(BaseModel):
    """A worked example bound to evidence and fact locks."""

    example_id: str = Field(description="Stable example identifier.")
    concept_id: str = Field(description="Concept the example illustrates.")
    title: str = Field(description="Short example title.")
    explanation: str = Field(description="Example explanation.")
    evidence_refs: list[EvidenceRef] = Field(
        default_factory=list, description="Evidence grounding the example."
    )
    fact_lock_ids: list[str] = Field(
        default_factory=list, description="Fact locks the example must respect."
    )


class RetrievalExercise(BaseModel):
    """A single retrieval-practice exercise bound to evidence and fact locks."""

    exercise_id: str = Field(description="Stable exercise identifier.")
    lesson_id: str = Field(description="Lesson this exercise belongs to.")
    concept_id: str = Field(description="Concept being exercised.")
    exercise_type: RetrievalExerciseType = Field(description="Type of retrieval task.")
    question_text: str = Field(description="Question text.")
    expected_answer: str = Field(description="Expected answer or key elements.")
    misconception_hints: list[str] = Field(
        default_factory=list,
        description="Common misconceptions this exercise surfaces.",
    )
    evidence_refs: list[EvidenceRef] = Field(
        default_factory=list, description="Evidence grounding the answer."
    )
    fact_lock_ids: list[str] = Field(
        default_factory=list, description="Fact locks the answer must respect."
    )


class ImmediateFeedback(BaseModel):
    """Specific feedback for a single exercise attempt, bound to evidence."""

    is_correct: bool = Field(description="Whether the response is correct.")
    explanation: str = Field(description="Explanation of why the response is correct or not.")
    misconception: str | None = Field(
        default=None, description="Identified misconception if any."
    )
    evidence_refs: list[EvidenceRef] = Field(
        default_factory=list, description="Evidence supporting the feedback."
    )
    next_step: str = Field(description="Recommended next action.")


class ExerciseAttempt(BaseModel):
    """A user's attempt at a retrieval exercise."""

    attempt_id: str = Field(description="Stable attempt identifier.")
    exercise_id: str = Field(description="Exercise being attempted.")
    lesson_id: str = Field(description="Lesson the exercise belongs to.")
    owner_account_id: str = Field(description="Owning account identifier.")
    response_text: str = Field(description="User's response.")
    evaluated_state: AnswerEvaluatedState = Field(description="Result of evaluation.")
    evaluator: str = Field(description="Agent that evaluated the attempt.")
    feedback: ImmediateFeedback = Field(description="Immediate feedback.")
    created_at: datetime = Field(description="When the attempt was recorded.")


class ExerciseAttemptRequest(BaseModel):
    """Request to submit an exercise attempt."""

    response_text: str = Field(description="User's response.", min_length=1)


class LessonEvidenceBundle(BaseModel):
    """Evidence and fact-lock input carried from a claim graph (T016) into a lesson.

    The bundle is the minimal authoritative context the teaching service needs to
    bind explanations, examples, answers and feedback to evidence without directly
    depending on the science module's internal services.
    """

    graph_id: str | None = Field(default=None, description="Source claim graph id.")
    evidence_refs: list[EvidenceRef] = Field(
        default_factory=list, description="Evidence references grounding the lesson."
    )
    fact_locks: list[FactLock] = Field(
        default_factory=list, description="Fact locks constraining the lesson."
    )


class TeachingQualityGateCheck(StrEnum):
    """Named checks performed by the teaching quality gate."""

    MISSION_BOUND = "mission_bound"
    EVIDENCE_BOUND = "evidence_bound"
    FACT_LOCK_BOUND = "fact_lock_bound"
    EXERCISE_REQUIRES_RETRIEVAL = "exercise_requires_retrieval"
    NO_HIGH_RISK_WITHOUT_HUMAN_GATE = "no_high_risk_without_human_gate"
    SINGLE_LEARNING_VICTORY = "single_learning_victory"


class TeachingQualityGateResult(BaseModel):
    """Result of running the teaching quality gate over a short lesson."""

    passed: bool = Field(description="Whether the lesson passes the gate.")
    checks: dict[TeachingQualityGateCheck, bool] = Field(default_factory=dict)
    failed_checks: list[TeachingQualityGateCheck] = Field(default_factory=list)
    requires_human_gate: bool = Field(default=False)
    human_gate_reason: str | None = Field(default=None)
    reason: str | None = Field(default=None, description="Human-readable gate summary.")


class ShortLesson(BaseModel):
    """A short lesson around a single learning victory.

    Explanations, examples, exercises and feedback are bound to an EvidenceSet and
    constrained by fact locks. The lesson status is independent of workflow run
    status; it reflects the teaching quality gate outcome.
    """

    lesson_id: str = Field(description="Stable lesson identifier.")
    plan_id: str = Field(description="Teaching plan this lesson serves.")
    mission_id: str = Field(description="Mission the lesson serves.")
    owner_account_id: str = Field(description="Owning account identifier.")
    title: str = Field(description="Lesson title.")
    learning_objective: str = Field(description="Single learning victory.")
    target_concepts: list[str] = Field(description="Concepts addressed in this lesson.")
    explanation: str = Field(description="Core explanation of the learning objective.")
    examples: list[LessonExample] = Field(default_factory=list)
    exercises: list[RetrievalExercise] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(
        default_factory=list, description="Evidence grounding the lesson."
    )
    fact_lock_set_id: str | None = Field(
        default=None, description="Optional fact lock set id from T016."
    )
    fact_lock_ids: list[str] = Field(
        default_factory=list, description="Fact locks constraining the lesson."
    )
    quality_gate: TeachingQualityGateResult = Field(description="Teaching quality gate result.")
    status: LessonStatus = Field(default=LessonStatus.DRAFT)
    human_gate_required: bool = Field(default=False)
    version: int = Field(default=1, ge=1, description="Optimistic concurrency version.")
    created_at: datetime = Field(description="Creation timestamp.")


class GenerateLessonRequest(BaseModel):
    """Request to generate a short lesson from a teaching plan."""

    evidence_bundle: LessonEvidenceBundle | None = Field(
        default=None, description="Optional evidence and fact-lock bundle from T016."
    )


class LearningRecordType(StrEnum):
    """Kind of observable learning evidence that may update a knowledge state.

    Browsing and completion-only activities are deliberately excluded; they are
    stored as ``LearningActivity`` and never promoted to learning records.
    """

    EXERCISE_ATTEMPT = "exercise_attempt"
    MISCONCEPTION_CORRECTION = "misconception_correction"
    PREREQUISITE_EVIDENCE = "prerequisite_evidence"
    DELAYED_RETRIEVAL = "delayed_retrieval"
    TRANSFER_TASK = "transfer_task"


class LearningRecordSource(StrEnum):
    """Origin of a learning record."""

    DIAGNOSTIC_ANSWER = "diagnostic_answer"
    EXERCISE_ATTEMPT = "exercise_attempt"
    USER_CORRECTION = "user_correction"


class LearningRecord(BaseModel):
    """Evidence-backed record of a learning event that can update knowledge state.

    A learning record references the concrete response, misconception correction
    or prerequisite evidence that justifies a knowledge-state change. It is
    versioned, scoped to a mission and never derived from browsing or completion.
    """

    record_id: str = Field(description="Stable record identifier.")
    mission_id: str = Field(description="Mission this record belongs to.")
    owner_account_id: str = Field(description="Owning account identifier.")
    concept_id: str = Field(description="Concept the record evidences.")
    record_type: LearningRecordType = Field(description="Kind of learning evidence.")
    source_type: LearningRecordSource = Field(description="Origin of the evidence.")
    source_id: str = Field(
        description="Id of the source object (attempt id, answer id, correction id)."
    )
    response_text: str | None = Field(
        default=None, description="User response or product when applicable."
    )
    evaluated_state: AnswerEvaluatedState | None = Field(
        default=None, description="Evaluated result when applicable."
    )
    misconception_corrected: str | None = Field(
        default=None, description="Misconception that was corrected, if any."
    )
    evidence_refs: list[EvidenceRef] = Field(
        default_factory=list,
        description="Scientific evidence grounding the record.",
    )
    record_reason: str = Field(
        description="Why this record counts as qualified learning evidence."
    )
    created_at: datetime = Field(description="When the record was created.")


class LearningRecordCreateRequest(BaseModel):
    """Request to create a qualified learning record."""

    concept_id: str = Field(description="Concept the record evidences.")
    record_type: LearningRecordType = Field(description="Kind of learning evidence.")
    source_type: LearningRecordSource = Field(description="Origin of the evidence.")
    source_id: str = Field(description="Id of the source object.")
    response_text: str | None = Field(default=None, description="User response.")
    evaluated_state: AnswerEvaluatedState | None = Field(
        default=None, description="Evaluated result."
    )
    misconception_corrected: str | None = Field(
        default=None, description="Corrected misconception."
    )
    evidence_refs: list[EvidenceRef] = Field(
        default_factory=list, description="Evidence grounding the record."
    )
    record_reason: str = Field(description="Why this is qualified evidence.")


class HumanDecisionType(StrEnum):
    """Named human decision on a proposed knowledge-state or path change."""

    ACCEPT = "accept"
    REJECT = "reject"
    MODIFY = "modify"


class HumanDecision(BaseModel):
    """A named, auditable human decision on a knowledge-state proposal."""

    decision_id: str = Field(description="Stable decision identifier.")
    target_id: str = Field(description="Proposal the decision applies to.")
    account_id: str = Field(description="Account that made the decision.")
    decision: HumanDecisionType = Field(description="Decision type.")
    reason: str = Field(description="Human-readable rationale.")
    modified_value: str | None = Field(
        default=None, description="Modified value when decision is 'modify'."
    )
    created_at: datetime = Field(description="When the decision was recorded.")


class KnowledgeStateProposalStatus(StrEnum):
    """Lifecycle status of a knowledge-state update proposal."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class KnowledgeStateProposal(BaseModel):
    """A proposed update to a knowledge state derived from learning records.

    The proposal is only a candidate until the user accepts, rejects or modifies
    it. Rejected proposals remain auditable and cannot be silently re-applied by
    the model.
    """

    proposal_id: str = Field(description="Stable proposal identifier.")
    mission_id: str = Field(description="Mission this proposal belongs to.")
    owner_account_id: str = Field(description="Owning account identifier.")
    concept_id: str = Field(description="Concept being updated.")
    proposed_status: KnowledgeStateStatus = Field(description="Proposed state.")
    proposed_confidence: KnowledgeConfidence = Field(
        default=KnowledgeConfidence.LOW, description="Proposed confidence."
    )
    supporting_record_ids: list[str] = Field(
        default_factory=list, description="Records that support the proposal."
    )
    refuting_record_ids: list[str] = Field(
        default_factory=list, description="Records that refute or limit it."
    )
    uncertainty_reason: str | None = Field(
        default=None, description="Why confidence is not higher."
    )
    scope: str = Field(description="Scope within which the state applies.")
    next_validation_task: str = Field(
        description="Next task to validate or refine the state."
    )
    status: KnowledgeStateProposalStatus = Field(
        default=KnowledgeStateProposalStatus.PENDING
    )
    decision: HumanDecision | None = Field(
        default=None, description="Recorded human decision, if any."
    )
    version: int = Field(default=1, ge=1, description="Optimistic concurrency version.")
    created_at: datetime = Field(description="Creation timestamp.")
    decided_at: datetime | None = Field(
        default=None, description="When the proposal was decided."
    )


class ProposeKnowledgeStateUpdateRequest(BaseModel):
    """Request to propose a knowledge-state update from learning records."""

    concept_id: str = Field(description="Concept to update.")
    record_ids: list[str] | None = Field(
        default=None,
        description="Records to base the proposal on; auto-select if omitted.",
    )


class DecideKnowledgeStateProposalRequest(BaseModel):
    """Request to accept, reject or modify a knowledge-state proposal."""

    decision: HumanDecisionType = Field(description="Decision type.")
    reason: str = Field(description="Human-readable rationale.", min_length=1)
    modified_status: KnowledgeStateStatus | None = Field(
        default=None, description="Modified status when decision is 'modify'."
    )
    modified_confidence: KnowledgeConfidence | None = Field(
        default=None, description="Modified confidence when decision is 'modify'."
    )


class LearningPathNodeStatus(StrEnum):
    """Lifecycle status of a node on a learning path."""

    PENDING = "pending"
    COMPLETED = "completed"
    SKIPPED = "skipped"


class LearningPathNode(BaseModel):
    """A single step on a learning path bound to evidence."""

    node_id: str = Field(description="Stable node identifier.")
    concept_id: str = Field(description="Concept addressed by this node.")
    title: str = Field(description="Short node title.")
    description: str = Field(description="What the learner should do.")
    status: LearningPathNodeStatus = Field(
        default=LearningPathNodeStatus.PENDING
    )
    evidence_record_ids: list[str] = Field(
        default_factory=list,
        description="Learning records that justify this node's placement.",
    )
    depends_on_node_ids: list[str] = Field(
        default_factory=list, description="Nodes that should be addressed first."
    )


class LearningPath(BaseModel):
    """A per-user learning route driven by confirmed knowledge states.

    The path is recomputed when knowledge states change. It is anchored to the
    learning mission and each node traces back to learning records.
    """

    path_id: str = Field(description="Stable path identifier.")
    mission_id: str = Field(description="Mission the path serves.")
    owner_account_id: str = Field(description="Owning account identifier.")
    title: str = Field(description="Path title.")
    nodes: list[LearningPathNode] = Field(default_factory=list)
    current_node_id: str | None = Field(
        default=None, description="Next recommended node."
    )
    version: int = Field(default=1, ge=1, description="Optimistic concurrency version.")
    created_at: datetime = Field(description="Creation timestamp.")
    updated_at: datetime = Field(description="Last update timestamp.")


class ReviewTaskType(StrEnum):
    """Kind of review task: spaced repetition or interleaved practice."""

    SPACED_REPETITION = "spaced_repetition"
    INTERLEAVED_PRACTICE = "interleaved_practice"


class ReviewTaskStatus(StrEnum):
    """Lifecycle status of a scheduled review task."""

    SCHEDULED = "scheduled"
    POSTPONED = "postponed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ReviewTask(BaseModel):
    """A single spaced-repetition or interleaved-practice task.

    The task is justified by learning records, a knowledge state, and the
    learning mission. It can be postponed, adjusted, cancelled, or completed.
    Completing a task creates a new learning record rather than overwriting the
    old knowledge state.
    """

    task_id: str = Field(description="Stable task identifier.")
    mission_id: str = Field(description="Mission this task belongs to.")
    owner_account_id: str = Field(description="Owning account identifier.")
    concept_id: str = Field(description="Concept being reviewed.")
    task_type: ReviewTaskType = Field(description="Spaced repetition or interleaved practice.")
    status: ReviewTaskStatus = Field(default=ReviewTaskStatus.SCHEDULED)
    due_at: datetime = Field(description="When the task is due.")
    reason: str = Field(
        description="Human-readable justification referencing records, state, "
        "forgetting evidence and the learning mission."
    )
    source_record_ids: list[str] = Field(
        default_factory=list,
        description="Learning records that justify this scheduling decision.",
    )
    knowledge_state_id: str | None = Field(
        default=None, description="Knowledge-state snapshot at scheduling time."
    )
    interval_days: int = Field(
        default=1, ge=1, description="Scheduled interval in days."
    )
    postponed_to: datetime | None = Field(
        default=None, description="New due date when the task is postponed."
    )
    cancellation_reason: str | None = Field(default=None, description="Why the task was cancelled.")
    run_id: str | None = Field(
        default=None, description="Optional workflow run id if materialized as a WorkOrder."
    )
    version: int = Field(default=1, ge=1, description="Optimistic concurrency version.")
    created_at: datetime = Field(description="Creation timestamp.")
    updated_at: datetime = Field(description="Last update timestamp.")


class ReviewSchedule(BaseModel):
    """Collection of pending review tasks for a learning mission."""

    schedule_id: str = Field(description="Stable schedule identifier.")
    mission_id: str = Field(description="Mission the schedule serves.")
    owner_account_id: str = Field(description="Owning account identifier.")
    task_ids: list[str] = Field(default_factory=list, description="Tasks in this schedule.")
    version: int = Field(default=1, ge=1, description="Optimistic concurrency version.")
    created_at: datetime = Field(description="Creation timestamp.")
    updated_at: datetime = Field(description="Last update timestamp.")


class ReviewTaskPostponeRequest(BaseModel):
    """Request to postpone a review task to a new due date."""

    new_due_at: datetime = Field(description="New due date.")
    reason: str = Field(description="Why the task is being postponed.", min_length=1)


class ReviewTaskAdjustRequest(BaseModel):
    """Request to adjust the due date or interval of a review task."""

    new_due_at: datetime | None = Field(default=None, description="New due date if any.")
    new_interval_days: int | None = Field(
        default=None, description="New interval in days if any.", ge=1
    )
    reason: str = Field(description="Why the task is being adjusted.", min_length=1)


class ReviewTaskCancelRequest(BaseModel):
    """Request to cancel a review task."""

    reason: str = Field(description="Why the task is being cancelled.", min_length=1)


class ReviewTaskCompleteRequest(BaseModel):
    """Request to complete a review task and record the result as evidence."""

    response_text: str = Field(description="User's response during the review.", min_length=1)
    evaluated_state: AnswerEvaluatedState = Field(description="Evaluated result.")
    record_reason: str = Field(description="Why this result counts as evidence.", min_length=1)


class LearningError(BaseModel):
    """Uniform learning-domain error response."""

    error: str = Field(description="Stable error code.")
    message: str = Field(description="Human-readable, non-leaking message.")
    details: dict[str, Any] = Field(
        default_factory=dict, description="Opaque detail safe for logging."
    )
