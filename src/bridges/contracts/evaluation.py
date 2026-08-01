"""Evaluation run lock and result bundle contracts.

These models define the public surface of T012: a project task run can be frozen
into an immutable ``EvaluationRunLock`` and replayed to produce a comparable
``EvaluationResultBundle``. The evaluation center compares inputs, locks,
results and failures without exposing private body to logs.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from bridges.contracts.ai import ModelRunLock
from bridges.contracts.workflows import RunProjection, WorkOrder


class EvaluationRunStatus(str, Enum):
    """Lifecycle status of an evaluation run."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class EvaluationFailureCategory(str, Enum):
    """High-level failure classification for an evaluation result."""

    NONE = "none"
    DATA = "data"
    RETRIEVAL = "retrieval"
    SCIENTIFIC_JUDGMENT = "scientific_judgment"
    CITATION = "citation"
    CALIBRATION = "calibration"
    PEDAGOGY = "pedagogy"
    PROFILE = "profile"
    MEMORY = "memory"
    EXPRESSION = "expression"
    MULTIMODAL = "multimodal"
    ACCESSIBILITY = "accessibility"
    SECURITY = "security"
    AUTHORIZATION = "authorization"
    ORCHESTRATION = "orchestration"
    TOOL = "tool"
    INFRASTRUCTURE = "infrastructure"
    COST = "cost"
    JUDGE = "judge"


class EvaluationSuiteRef(BaseModel):
    """Pointer to a registered evaluation suite and case."""

    suite_id: str = Field(description="Stable suite identifier.")
    suite_version: str = Field(description="Suite semantic version.")
    case_id: str | None = Field(
        default=None,
        description="Case identifier within the suite; null for the whole suite.",
    )


class EvaluationRunLock(BaseModel):
    """Immutable snapshot of everything needed to replay an evaluation run.

    The lock freezes the task input, code/environment identifier, model locks,
    schema versions, workflow version and other reproducibility metadata so that
    the same evaluation can be re-executed on any supported runtime carrier.
    """

    lock_id: str = Field(description="Stable evaluation run lock identifier.")
    source_run_id: str = Field(
        description="Project task run from which this evaluation was generated."
    )
    account_id: str = Field(description="Account that owns the evaluation run.")
    tenant_id: str | None = Field(default=None, description="Institution tenant if applicable.")
    project_id: str = Field(description="Project within which the evaluation is scoped.")
    suite: EvaluationSuiteRef = Field(description="Suite and case being evaluated.")
    work_order: WorkOrder = Field(description="Frozen task input.")
    workflow_name: str = Field(description="Compiled workflow template name.")
    workflow_version: str = Field(description="Compiled workflow template version.")
    code_commit_or_build_digest: str = Field(
        description="Code commit hash or production build digest at lock time."
    )
    runtime_identifier: str = Field(
        description="Runtime carrier: conda-agent, manual, unified-cli, docker or podman."
    )
    os_hardware_summary: str = Field(description="Operating system and hardware summary.")
    database_migration_version: str = Field(description="Database migration version at lock time.")
    config_digest: str = Field(description="Deterministic digest of relevant configuration.")
    random_seed: int = Field(description="Random seed used for stochastic operations.")
    dataset_versions: dict[str, str] = Field(
        default_factory=dict,
        description="Dataset name -> version digest.",
    )
    domain_pack_versions: dict[str, str] = Field(
        default_factory=dict,
        description="Domain pack name -> version.",
    )
    model_run_locks: list[ModelRunLock] = Field(
        default_factory=list,
        description="Immutable model invocation locks captured from the source run."
    )
    prompt_versions: dict[str, str] = Field(
        default_factory=dict,
        description="Capability name -> prompt/template version.",
    )
    schema_versions: dict[str, str] = Field(
        default_factory=dict,
        description="Schema name -> version used during the run.",
    )
    tool_adapter_versions: dict[str, str] = Field(
        default_factory=dict,
        description="Tool capability name -> adapter version.",
    )
    judge_versions: dict[str, str] = Field(
        default_factory=dict,
        description="Judge/referee name -> version.",
    )
    execution_count: int = Field(
        default=1,
        ge=1,
        description="How many times the case should be executed for this run.",
    )
    network_cache_policy: str = Field(
        default="frozen",
        description="Network/cache policy: frozen, warm, or online.",
    )
    time_baseline: datetime = Field(description="Reference timestamp for the lock.")
    created_at: datetime = Field(description="When the lock was created.")


class EvaluationMetric(BaseModel):
    """A single derived metric in a result bundle."""

    name: str = Field(description="Metric name.")
    value: float = Field(description="Metric value.")
    unit: str | None = Field(default=None, description="Metric unit.")
    slice_tag: str | None = Field(
        default=None,
        description="Risk slice or subgroup tag, e.g. high_risk, novice."
    )


class EvaluationResultBundle(BaseModel):
    """Saved result of one evaluation execution.

    Bundles are append-only: replaying the same lock produces a new bundle rather
    than overwriting the old one. They never include private body, full prompts,
    secrets or unnecessary raw model output.
    """

    bundle_id: str = Field(description="Stable result bundle identifier.")
    lock_id: str = Field(description="Evaluation run lock that produced this bundle.")
    source_run_id: str = Field(description="Source project task run identifier.")
    account_id: str = Field(description="Account that owns the bundle.")
    project_id: str = Field(description="Project within which the bundle is scoped.")
    suite: EvaluationSuiteRef = Field(description="Suite and case being evaluated.")
    status: EvaluationRunStatus = Field(description="Final status of the evaluation run.")
    frozen_inputs: WorkOrder = Field(description="Inputs frozen by the lock.")
    run_projection: RunProjection | None = Field(
        default=None,
        description="Task-stage projection produced by replay, if any.",
    )
    outputs: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured outputs produced by the replayed run.")
    state_trajectory: list[str] = Field(
        default_factory=list,
        description="Ordered run-status values observed during replay.")
    typed_artifact_refs: list[str] = Field(
        default_factory=list,
        description="References to typed artifacts produced during replay.")
    model_tool_calls: list[ModelRunLock] = Field(
        default_factory=list,
        description="Model/tool invocation locks observed during replay.")
    failure_category: EvaluationFailureCategory = Field(
        default=EvaluationFailureCategory.NONE,
        description="High-level failure classification.")
    failure_reason: str | None = Field(
        default=None,
        description="Human-readable failure reason without internal details.")
    derived_metrics: list[EvaluationMetric] = Field(
        default_factory=list,
        description="Derived metrics such as latency, cost, coverage.")
    cost_latency: dict[str, Any] = Field(
        default_factory=dict,
        description="Cost and latency metadata.")
    logs_and_traces: dict[str, Any] = Field(
        default_factory=dict,
        description="Scrubbed logs and trace references; no private body.")
    reproduction_command: str = Field(
        description="Exact command that can replay this evaluation run.")
    environment_summary: dict[str, Any] = Field(
        default_factory=dict,
        description="Environment summary for the runner that produced this bundle.")
    created_at: datetime = Field(description="When the bundle was produced.")


class EvaluationRunProjection(BaseModel):
    """Task-stage / evaluation-center projection of one evaluation run."""

    evaluation_run_id: str = Field(description="Same as the lock identifier.")
    source_run_id: str = Field(description="Source project task run identifier.")
    account_id: str = Field(description="Account that owns the evaluation run.")
    project_id: str = Field(description="Project within which the evaluation is scoped.")
    suite: EvaluationSuiteRef = Field(description="Suite and case being evaluated.")
    status: EvaluationRunStatus = Field(description="Current status of the evaluation run.")
    lock: EvaluationRunLock = Field(description="Immutable replay lock.")
    bundle_ids: list[str] = Field(
        default_factory=list,
        description="Result bundles produced from this lock, in creation order.")
    latest_bundle_id: str | None = Field(
        default=None,
        description="Most recently produced bundle identifier.")
    created_at: datetime = Field(description="When the evaluation run was created.")


class EvaluationDiffEntry(BaseModel):
    """One field difference between two evaluation result bundles."""

    field: str = Field(description="Differing field path.")
    value_a: Any = Field(description="Value in bundle A.")
    value_b: Any = Field(description="Value in bundle B.")
    kind: str = Field(
        description="Diff kind: changed, added_a, added_b, removed_a, removed_b.")


class EvaluationDiffProjection(BaseModel):
    """Comparison of two evaluation result bundles in the evaluation center."""

    evaluation_run_id_a: str = Field(description="Evaluation run A identifier.")
    evaluation_run_id_b: str = Field(description="Evaluation run B identifier.")
    bundle_id_a: str = Field(description="Bundle A identifier.")
    bundle_id_b: str = Field(description="Bundle B identifier.")
    lock_match: bool = Field(
        description="True when both bundles share the same evaluation run lock.")
    input_diffs: list[EvaluationDiffEntry] = Field(
        default_factory=list,
        description="Differences in frozen inputs.")
    lock_diffs: list[EvaluationDiffEntry] = Field(
        default_factory=list,
        description="Differences in evaluation run locks.")
    result_diffs: list[EvaluationDiffEntry] = Field(
        default_factory=list,
        description="Differences in outputs, status and metrics.")
    failure_diffs: list[EvaluationDiffEntry] = Field(
        default_factory=list,
        description="Differences in failure category and reason.")


class EvaluationCreateRequest(BaseModel):
    """Request to create an evaluation run from a completed project task run."""

    suite_id: str = Field(description="Suite identifier for the evaluation.")
    suite_version: str = Field(description="Suite version.")
    case_id: str | None = Field(
        default=None,
        description="Case identifier; null evaluates the whole suite snapshot.")
    runtime_identifier: str = Field(
        default="conda-agent",
        description="Runtime carrier used to produce the lock.")
    random_seed: int = Field(default=42, description="Random seed for replay.")
    execution_count: int = Field(default=1, ge=1, description="Executions per replay.")


class EvaluationErrorResponse(BaseModel):
    """Uniform evaluation error response."""

    error: str = Field(description="Stable error code.")
    message: str = Field(description="Human-readable, non-leaking message.")
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Opaque detail safe for logging; must not expose internal state.")
