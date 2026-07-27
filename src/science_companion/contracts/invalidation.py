"""Invalidation, tombstone, impact-set and revalidation contracts.

These models define the cross-cutting foundation for revoking or deleting objects
and propagating the effects to downstream consumers (cache, index, runs,
projections, vault capsules). They are the authoritative shape of the
InvalidationEvent, Tombstone, ImpactSet, InvalidationPlan, Outbox propagation and
revalidation scheduling contracts.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from science_companion.contracts.identity import SubjectContext
from science_companion.contracts.projects import ObjectRef
from science_companion.contracts.scope import ScopeEnvelope


class InvalidationEventType(str, Enum):
    """Kinds of invalidation that can affect an object."""

    REVOKE = "revoke"
    DELETE = "delete"
    KEY_EPOCH_ROLLOVER = "key_epoch_rollover"
    POLICY_VERSION_CHANGE = "policy_version_change"
    SOURCE_RETRACTED = "source_retracted"


def _immutable_model() -> dict[str, bool]:
    """Return shared model_config that enforces frozen (immutable) semantics.

    Sets frozen=True so that InvalidationEvent, Tombstone and related models
    truly enforce the append-only / never-overwritten contract at the Python
    level. Accidental mutation raises a TypeError instead of silently passing.
    """
    return {"frozen": True}


class InvalidationState(str, Enum):
    """Current invalidation state of an object."""

    ACTIVE = "active"
    REVOKED = "revoked"
    TOMBSTONED = "tombstoned"


class OutboxStatus(str, Enum):
    """Delivery status of an outbox entry."""

    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"


class RevalidationStatus(str, Enum):
    """Lifecycle status of a scheduled revalidation."""

    SCHEDULED = "scheduled"
    COMPLETED = "completed"
    FAILED = "failed"


class InvalidationEvent(BaseModel):
    """Immutable, versioned record that an object has been invalidated.

    The event binds the subject, scope, reason, authorization version and key
    epoch so that later readers can reconstruct the exact context that caused the
    invalidation. Events are append-only and never overwritten.
    """

    model_config = _immutable_model()

    event_id: str = Field(description="Stable event identifier.")
    event_type: InvalidationEventType = Field(description="Kind of invalidation.")
    object_ref: ObjectRef = Field(description="Object that was invalidated.")
    subject: SubjectContext = Field(description="Subject that issued the invalidation.")
    scope_envelope: ScopeEnvelope = Field(description="Scope envelope at the time of invalidation.")
    reason: str = Field(description="Human-readable reason for the invalidation.")
    authorization_version: str = Field(description="Authorization policy version in effect.")
    key_epoch: str = Field(description="Key epoch in effect.")
    caused_by_event_id: str | None = Field(
        default=None,
        description="Prior event that caused this follow-up invalidation, if any.",
    )
    sequence: int = Field(
        default=1,
        ge=1,
        description="Monotonic sequence number for this object's invalidation log.",
    )
    occurred_at: datetime = Field(description="When the event was recorded.")


class Tombstone(BaseModel):
    """Immutable deletion marker for an object.

    A tombstone takes precedence over ordinary invalidation events: once a
    tombstone exists, the object is permanently deleted from the active view and
    history is retained rather than erased.
    """

    model_config = _immutable_model()

    tombstone_id: str = Field(description="Stable tombstone identifier.")
    object_ref: ObjectRef = Field(description="Object that was deleted.")
    event_id: str = Field(description="Invalidation event that produced this tombstone.")
    subject: SubjectContext = Field(description="Subject that issued the deletion.")
    scope_envelope: ScopeEnvelope = Field(description="Scope envelope at the time of deletion.")
    reason: str = Field(description="Human-readable reason for deletion.")
    authorization_version: str = Field(description="Authorization policy version in effect.")
    key_epoch: str = Field(description="Key epoch in effect.")
    deleted_at: datetime = Field(description="When the tombstone was recorded.")
    sequence: int = Field(
        default=1,
        ge=1,
        description="Sequence number of the deletion event in the object's log.",
    )


class InvalidationCheckResult(BaseModel):
    """Result of checking the current invalidation state of an object."""

    model_config = _immutable_model()

    object_ref: ObjectRef = Field(description="Object that was checked.")
    state: InvalidationState = Field(description="Effective state.")
    effective_event_id: str | None = Field(
        default=None,
        description="Event or tombstone that produced the state.",
    )
    effective_sequence: int = Field(
        default=0,
        description="Sequence number of the effective event or tombstone.",
    )
    reason: str | None = Field(default=None, description="Reason when not active.")


class AffectedDownstream(BaseModel):
    """One downstream consumer that must react to an invalidation event."""

    model_config = _immutable_model()

    downstream_id: str = Field(description="Stable downstream identifier.")
    downstream_type: str = Field(
        description="Logical downstream family, e.g. cache, index, run, vault_capsule."
    )
    object_refs: list[str] = Field(
        default_factory=list,
        description="Object identifiers within this downstream that are affected.",
    )
    scope_envelope: ScopeEnvelope = Field(description="Scope under which the downstream operates.")
    action: str = Field(
        description="Required action, e.g. invalidate, block_new_use, revalidate, notify."
    )
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Domain-specific details safe for the outbox payload.",
    )


class ImpactSet(BaseModel):
    """Scope-correct set of downstream consumers affected by an invalidation."""

    model_config = _immutable_model()

    impact_set_id: str = Field(description="Stable impact set identifier.")
    trigger_event_id: str = Field(description="Invalidation event that produced the set.")
    object_ref: ObjectRef = Field(description="Object whose invalidation triggered the set.")
    affected_downstreams: list[AffectedDownstream] = Field(
        default_factory=list,
        description="Downstream consumers that must react.",
    )
    scope_envelope: ScopeEnvelope = Field(description="Scope under which the impact set was built.")
    created_at: datetime = Field(description="When the impact set was built.")


class OutboxEntry(BaseModel):
    """Idempotent propagation unit from an impact set to a downstream consumer.

    Each entry is addressed to a single destination, carries the subset of the
    impact set relevant to that destination, and uses a deterministic
    idempotency key so failed deliveries can be replayed without expanding scope.
    """

    entry_id: str = Field(description="Stable outbox entry identifier.")
    destination: str = Field(description="Downstream type that should receive this entry.")
    payload: dict[str, Any] = Field(description="Domain-specific payload for the destination.")
    scope_envelope: ScopeEnvelope = Field(description="Scope under which the entry was created.")
    idempotency_key: str = Field(
        description="Deterministic key used to deduplicate replays."
    )
    status: OutboxStatus = Field(default=OutboxStatus.PENDING)
    created_at: datetime = Field(description="When the entry was created.")
    delivered_at: datetime | None = Field(default=None)
    failure_count: int = Field(default=0, ge=0)


class RevalidationSchedule(BaseModel):
    """Scheduled, idempotent revalidation of an invalidated object or its effects.

    Revalidations are bound to a scope and use an idempotency key so that retries
    do not spawn duplicate work or leak across accounts.
    """

    schedule_id: str = Field(description="Stable schedule identifier.")
    object_ref: ObjectRef = Field(description="Object to revalidate.")
    revalidation_type: str = Field(description="Logical revalidation kind, e.g. index, claim_graph.")
    due_at: datetime = Field(description="Earliest time the revalidation should run.")
    idempotency_key: str = Field(description="Deterministic key for deduplication.")
    status: RevalidationStatus = Field(default=RevalidationStatus.SCHEDULED)
    scope_envelope: ScopeEnvelope = Field(description="Scope under which the revalidation runs.")
    created_at: datetime = Field(description="When the schedule was created.")
    completed_at: datetime | None = Field(default=None)


class RevalidationResult(BaseModel):
    """Result of running one revalidation handler."""

    model_config = _immutable_model()

    schedule_id: str = Field(description="Schedule that was executed.")
    success: bool = Field(description="Whether the revalidation completed successfully.")
    reason: str | None = Field(default=None, description="Human-readable result reason.")
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Opaque details safe for logging and audit."
    )


class InvalidationPlan(BaseModel):
    """Complete plan produced from an invalidation event.

    A plan bundles the event, the optional tombstone, the impact set, the outbox
    entries for downstream propagation, and the revalidation schedules. It is
    created in a single unit of work so that history is always written before
    propagation is attempted.
    """

    model_config = _immutable_model()

    plan_id: str = Field(description="Stable plan identifier.")
    trigger_event_id: str = Field(description="Invalidation event that produced the plan.")
    event: InvalidationEvent = Field(description="Triggering invalidation event.")
    tombstone: Tombstone | None = Field(default=None, description="Tombstone if the event was a deletion.")
    impact_set: ImpactSet = Field(description="Resolved downstream impact set.")
    outbox_entries: list[OutboxEntry] = Field(
        default_factory=list,
        description="Outbox entries derived from the impact set."
    )
    revalidation_schedules: list[RevalidationSchedule] = Field(
        default_factory=list,
        description="Revalidation schedules derived from the impact set."
    )
    created_at: datetime = Field(description="When the plan was created.")


@runtime_checkable
class ImpactResolver(Protocol):
    """Extension point for domain modules to resolve downstream impact.

    Domain modules register an ImpactResolver for a downstream family (e.g.
    cache, index, claim_graph, vault_capsule). The invalidation service calls
    every resolver when building an ImpactSet. Resolvers may not alter the
    tombstone priority, history retention or failure-lockout lower bound enforced
    by the core service.
    """

    def __call__(self, event: InvalidationEvent) -> list[AffectedDownstream]:
        """Return affected downstreams for the given invalidation event."""
        ...


@runtime_checkable
class RevalidationHandler(Protocol):
    """Extension point for domain modules to execute scheduled revalidations.

    Handlers are invoked by the invalidation service when a scheduled
    revalidation is due. They must be pure side-effect functions that return a
    RevalidationResult; the service records the result and handles retries.
    """

    def __call__(self, schedule: RevalidationSchedule) -> RevalidationResult:
        """Execute the revalidation and return a structured result."""
        ...
