"""Workflow, WorkOrder, and task-stage lifecycle contracts.

These models define the public surface of `WorkOrder` submission, the immutable
`RunContextEnvelope` carried by every run and node, and the `RunProjection`
displayed on the task stage. They are the authoritative shape of CONTRACT-WF-01.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from science_companion.contracts.projects import ObjectDomain


class WorkflowRunStatus(str, Enum):
    """Lifecycle status of the workflow orchestration itself."""

    DRAFT = "draft"
    COMPILED = "compiled"
    RUNNING = "running"
    WAITING_HUMAN = "waiting_human"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class ArtifactTrustStatus(str, Enum):
    """Lifecycle status of the scientific artifact produced by a run."""

    NOT_CREATED = "not_created"
    DRAFT = "draft"
    EVIDENCE_BOUND = "evidence_bound"
    QUALIFIED = "qualified"
    APPROVED = "approved"
    CONFLICTED = "conflicted"
    QUARANTINED = "quarantined"
    INVALIDATED = "invalidated"


class NodeStatus(str, Enum):
    """Status of a single workflow node attempt."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class HumanTodoStatus(str, Enum):
    """Status of a named human decision attached to a run."""

    OPEN = "open"
    RESOLVED = "resolved"
    BLOCKED = "blocked"


class GateResult(str, Enum):
    """Structured result returned by a quality gate."""

    PASS = "pass"
    WAIT_HUMAN = "wait_human"
    RETRYABLE_FAIL = "retryable_fail"
    FAIL_CLOSED = "fail_closed"
    INVALIDATE = "invalidate"


class WorkOrder(BaseModel):
    """A task submitted by the global science companion for compilation into a run.

    The user must confirm the objective, success criteria, and risk statement
    before the run leaves the draft state.
    """

    workflow_name: str = Field(
        description="Logical workflow template name.",
        min_length=1,
    )
    workflow_version: str = Field(
        default="1",
        description="Version of the workflow template to compile.",
    )
    project_id: str = Field(description="Project within which the run is scoped.")
    objective: str = Field(
        description="Human-readable task goal.",
        min_length=1,
        max_length=2000,
    )
    success_criteria: str = Field(
        description="Observable criteria that decide whether the task succeeds.",
        min_length=1,
        max_length=2000,
    )
    risk_statement: str = Field(
        description="Known risks, limits, or required human oversight.",
        min_length=1,
        max_length=2000,
    )
    object_refs: list[str] = Field(
        default_factory=list,
        description="Object identifiers the run is authorized to access.",
    )
    memory_slice_refs: list[str] = Field(
        default_factory=list,
        description="Memory slice identifiers compiled for this run.",
    )
    domain_pack_refs: list[str] = Field(
        default_factory=list,
        description="Domain packs the run must respect.",
    )


class WorkOrderConfirmRequest(BaseModel):
    """Explicit user confirmation that starts the compiled run.

    The ``confirmed`` field is required (no default) so that an empty request
    body is rejected with a clear validation error instead of silently accepted.
    """

    confirmed: bool = Field(
        description="Must be true to leave the draft state — omitting this field "
        "is rejected so that accidental confirmation cannot happen.",
    )


class RunContextEnvelope(BaseModel):
    """Immutable execution context carried by a run and every node.

    The envelope binds the run to a subject, tenant, project, object domain,
    authorization snapshot, key epoch, and declared object scope. It is the source
    of truth for refresh, recovery, and audit; it does not depend on chat history.
    """

    run_id: str = Field(description="Stable run identifier.")
    account_id: str = Field(description="Authenticated account that owns the run.")
    tenant_id: str | None = Field(
        default=None,
        description="Institution/organization tenant when applicable.",
    )
    project_id: str = Field(description="Project within which the run is scoped.")
    workflow_name: str = Field(description="Compiled workflow template name.")
    workflow_version: str = Field(description="Compiled workflow template version.")
    object_domain: ObjectDomain = Field(
        default=ObjectDomain.PERSONAL_VAULT,
        description="Authority domain in which the run executes.",
    )
    authorization_snapshot: str = Field(
        default="authz-1.0",
        description="Authorization policy version snapshot at compile time.",
    )
    key_epoch: str = Field(
        default="epoch-0",
        description="Key epoch under which run secrets and capsules are bound.",
    )
    object_refs: list[str] = Field(
        default_factory=list,
        description="Authorized object identifiers.",
    )
    memory_slice_refs: list[str] = Field(
        default_factory=list,
        description="Memory slice identifiers compiled for this run.",
    )
    domain_pack_refs: list[str] = Field(
        default_factory=list,
        description="Domain packs the run must respect.",
    )
    submitted_at: datetime = Field(description="When the WorkOrder was first submitted.")
    confirmed_at: datetime | None = Field(
        default=None,
        description="When the user confirmed the WorkOrder and compilation completed.",
    )
    terminal_states: list[WorkflowRunStatus] = Field(
        default_factory=list,
        description="States that legally end this workflow.",
    )


class NodeProgress(BaseModel):
    """Progress of a single workflow node as shown on the task stage."""

    node_id: str = Field(description="Stable node identifier within the workflow.")
    node_name: str = Field(description="Human-readable node name.")
    status: NodeStatus = Field(description="Current node status.")
    started_at: datetime | None = Field(default=None)
    completed_at: datetime | None = Field(default=None)
    output_ref: str | None = Field(
        default=None,
        description="Reference to the typed artifact produced by the node, if any.",
    )
    failure_reason: str | None = Field(default=None)


class HumanTodoItem(BaseModel):
    """A named human decision attached to a run."""

    todo_id: str = Field(description="Stable todo identifier.")
    title: str = Field(description="Short title shown in the task stage.")
    description: str = Field(description="Detailed explanation of what is needed.")
    status: HumanTodoStatus = Field(description="Current todo status.")
    created_at: datetime = Field(description="When the todo was created.")
    resolved_at: datetime | None = Field(default=None)
    resolution: str | None = Field(
        default=None,
        description="How the todo was resolved.",
    )


class RunProjection(BaseModel):
    """Task-stage projection of a workflow run.

    It separates workflow execution status from artifact trust status and
    publish eligibility, so that users do not confuse "finished running" with
    "scientifically approved" or "ready to publish".
    """

    run_id: str = Field(description="Stable run identifier.")
    project_id: str = Field(description="Project within which the run is scoped.")
    workflow_name: str = Field(description="Compiled workflow template name.")
    workflow_version: str = Field(description="Compiled workflow template version.")
    run_status: WorkflowRunStatus = Field(description="Workflow execution state.")
    artifact_trust_status: ArtifactTrustStatus = Field(
        description="Scientific trust state of the run's primary artifact."
    )
    publish_eligible: bool = Field(
        description="Whether all current conditions allow publishing the artifact."
    )
    objective: str = Field(description="Confirmed task goal.")
    success_criteria: str = Field(description="Confirmed success criteria.")
    risk_statement: str = Field(description="Confirmed risk statement.")
    current_node_id: str | None = Field(
        default=None,
        description="Node currently executing or waiting for input.",
    )
    nodes: list[NodeProgress] = Field(
        default_factory=list,
        description="All nodes in the compiled workflow.",
    )
    human_todos: list[HumanTodoItem] = Field(
        default_factory=list,
        description="Open and resolved human decisions for the run.",
    )
    run_started_at: datetime | None = Field(default=None)
    run_ended_at: datetime | None = Field(default=None)
    cancel_reason: str | None = Field(default=None)
    context_envelope: RunContextEnvelope = Field(
        description="Immutable execution context for refresh and audit."
    )


class WorkflowErrorResponse(BaseModel):
    """Uniform workflow error response."""

    error: str = Field(description="Stable error code.")
    message: str = Field(description="Human-readable, non-leaking message.")
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Opaque detail safe for logging; must not expose internal state.",
    )
