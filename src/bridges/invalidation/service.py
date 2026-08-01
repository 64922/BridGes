"""Invalidation, tombstone, impact-set and revalidation domain service.

The InvalidationService is the single place where objects are marked as revoked
or deleted, where the immutable history of those events is retained, and where
scope-correct impact sets are propagated to downstream consumers through an
outbox. Domain modules may extend impact resolution and revalidation handling,
but cannot change the core rules:

1. Tombstone always takes precedence over ordinary invalidation events.
2. Invalidation history is append-only and never overwritten or erased.
3. Unknown or mismatched invalidation state fails closed.
4. Outbox propagation and revalidation are idempotent and scope-bound.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from bridges.contracts.identity import AuthMethod, SubjectContext
from bridges.contracts.invalidation import (
    AffectedDownstream,
    ImpactResolver,
    ImpactSet,
    InvalidationCheckResult,
    InvalidationEvent,
    InvalidationEventType,
    InvalidationPlan,
    InvalidationState,
    OutboxEntry,
    OutboxStatus,
    RevalidationHandler,
    RevalidationResult,
    RevalidationSchedule,
    RevalidationStatus,
    Tombstone,
)
from bridges.contracts.projects import ObjectDomain, ObjectRef
from bridges.contracts.scope import ScopeAction, ScopeEnvelope, ScopeIsolationError
from bridges.scope import ScopeEnforcer


class InvalidationError(Exception):
    """Domain exception for invalidation failures.

    The message is safe to expose to callers; it never leaks whether an object
    exists or belongs to another account.
    """


@dataclass
class _ObjectLog:
    """Append-only invalidation history for one object reference."""

    events: list[InvalidationEvent] = None  # type: ignore[assignment]
    tombstone: Tombstone | None = None

    def __post_init__(self) -> None:
        if self.events is None:
            self.events = []


class InvalidationService:
    """Core invalidation service enforcing immutable history and tombstone priority.

    Not every invalidation event revokes the object itself. Some events (e.g.
    source version superseded) only notify downstream consumers that they must
    revalidate; the object remains active for new reads. Revoking event types
    are those that make ``require_active`` fail closed.
    """

    _REVOKING_EVENT_TYPES: set[InvalidationEventType] = {
        InvalidationEventType.REVOKE,
        InvalidationEventType.KEY_EPOCH_ROLLOVER,
        InvalidationEventType.POLICY_VERSION_CHANGE,
        InvalidationEventType.SOURCE_RETRACTED,
        InvalidationEventType.SOURCE_STATUS_UNKNOWN,
    }

    def __init__(self, scope_enforcer: ScopeEnforcer | None = None) -> None:
        self._scope_enforcer = scope_enforcer or ScopeEnforcer()
        self._logs: dict[str, _ObjectLog] = {}
        self._impact_sets: dict[str, ImpactSet] = {}
        self._outbox: dict[str, OutboxEntry] = {}
        self._schedules: dict[str, RevalidationSchedule] = {}
        self._impact_resolvers: dict[str, ImpactResolver] = {}
        self._revalidation_handlers: dict[str, RevalidationHandler] = {}

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def _object_key(self, object_ref: ObjectRef) -> str:
        """Stable key for the in-memory invalidation log.

        The key includes the domain, owner and object id but not the optimistic
        concurrency version, so invalidation follows the object across versions.
        """
        return f"{object_ref.domain.value}:{object_ref.owner_id}:{object_ref.object_id}"

    def _log(self, object_ref: ObjectRef) -> _ObjectLog:
        key = self._object_key(object_ref)
        if key not in self._logs:
            self._logs[key] = _ObjectLog()
        return self._logs[key]

    def _next_sequence(self, object_ref: ObjectRef) -> int:
        log = self._log(object_ref)
        sequences = [e.sequence for e in log.events]
        if log.tombstone is not None:
            sequences.append(log.tombstone.sequence)
        return max(sequences, default=0) + 1

    def _authorize(self, subject: SubjectContext, object_ref: ObjectRef) -> ScopeEnvelope:
        """Authorize the subject to invalidate/delete the object.

        Fails closed on any scope mismatch.
        """
        try:
            return self._scope_enforcer.authorize(subject, ScopeAction.DELETE, object_ref)
        except ScopeIsolationError as exc:
            raise InvalidationError(str(exc)) from exc

    def _compile_scope(self, subject: SubjectContext, object_ref: ObjectRef) -> ScopeEnvelope:
        return self._scope_enforcer.compile_scope(
            subject,
            tenant_id=None,
            project_id=object_ref.owner_id
            if object_ref.domain == ObjectDomain.SHARED_PROJECT
            else None,
            object_domain=object_ref.domain,
            purpose="invalidation",
            requested_object_refs=[object_ref],
        )

    def record_invalidation_event(
        self,
        subject: SubjectContext,
        object_ref: ObjectRef,
        event_type: InvalidationEventType,
        reason: str,
        *,
        scope_envelope: ScopeEnvelope | None = None,
        caused_by_event_id: str | None = None,
    ) -> InvalidationEvent:
        """Record an immutable invalidation event for an object.

        The event is appended to the object's log. If the event type is DELETE, a
        tombstone is also recorded automatically.
        """
        scope = scope_envelope or self._authorize(subject, object_ref)

        event_id = secrets.token_urlsafe(16)
        event = InvalidationEvent(
            event_id=event_id,
            event_type=event_type,
            object_ref=object_ref,
            subject=subject,
            scope_envelope=scope,
            reason=reason,
            authorization_version=scope.authorization_version,
            key_epoch=scope.key_epoch,
            caused_by_event_id=caused_by_event_id,
            sequence=self._next_sequence(object_ref),
            occurred_at=self._now(),
        )

        log = self._log(object_ref)
        log.events.append(event)

        if event_type == InvalidationEventType.DELETE:
            self._record_tombstone_from_event(event, scope)

        return event

    def record_tombstone(
        self,
        subject: SubjectContext,
        object_ref: ObjectRef,
        reason: str,
        *,
        scope_envelope: ScopeEnvelope | None = None,
    ) -> tuple[InvalidationEvent, Tombstone]:
        """Record a deletion event and its tombstone atomically.

        This is the explicit deletion API. It writes the invalidation event
        first, then the tombstone, so the log always shows what happened before
        the object is hidden.
        """
        event = self.record_invalidation_event(
            subject,
            object_ref,
            InvalidationEventType.DELETE,
            reason,
            scope_envelope=scope_envelope,
        )
        # The event already created the tombstone for DELETE events; retrieve it.
        tombstone = self._log(object_ref).tombstone
        if tombstone is None:
            raise InvalidationError("删除事件未生成墓碑，状态不一致。")
        return event, tombstone

    def _record_tombstone_from_event(
        self, event: InvalidationEvent, scope: ScopeEnvelope
    ) -> Tombstone:
        """Create a tombstone from a DELETE event.

        This is an internal helper that preserves the append-only invariant: the
        invalidation event is already in the log before the tombstone is created.
        """
        tombstone = Tombstone(
            tombstone_id=secrets.token_urlsafe(16),
            object_ref=event.object_ref,
            event_id=event.event_id,
            subject=event.subject,
            scope_envelope=scope,
            reason=event.reason,
            authorization_version=event.authorization_version,
            key_epoch=event.key_epoch,
            deleted_at=event.occurred_at,
            sequence=event.sequence,
        )
        self._log(event.object_ref).tombstone = tombstone
        return tombstone

    def check_state(self, object_ref: ObjectRef) -> InvalidationCheckResult:
        """Return the current invalidation state of an object.

        Tombstone takes precedence over ordinary events. If the object's log is
        unknown or the scope cannot be verified, the service fails closed by
        treating the object as revoked.
        """
        log = self._log(object_ref)

        if log.tombstone is not None:
            return InvalidationCheckResult(
                object_ref=object_ref,
                state=InvalidationState.TOMBSTONED,
                effective_event_id=log.tombstone.event_id,
                effective_sequence=log.tombstone.sequence,
                reason=log.tombstone.reason,
            )

        # Find the most recent event that actually revokes the object. Events such
        # as SOURCE_VERSION_SUPERSEDED only trigger downstream revalidation and do
        # not block new reads of the object itself.
        revoking_events = [
            e
            for e in log.events
            if e.event_type in self._REVOKING_EVENT_TYPES
        ]
        if revoking_events:
            latest = max(revoking_events, key=lambda e: e.sequence)
            return InvalidationCheckResult(
                object_ref=object_ref,
                state=InvalidationState.REVOKED,
                effective_event_id=latest.event_id,
                effective_sequence=latest.sequence,
                reason=latest.reason,
            )

        return InvalidationCheckResult(
            object_ref=object_ref,
            state=InvalidationState.ACTIVE,
            effective_event_id=None,
            effective_sequence=0,
            reason=None,
        )

    def require_active(self, object_ref: ObjectRef) -> None:
        """Fail closed if the object is revoked or tombstoned.

        This is the guard that new reads, new runs, cache lookups and index
        rebuilds must call before using an object.
        """
        result = self.check_state(object_ref)
        if result.state == InvalidationState.TOMBSTONED:
            raise InvalidationError("对象已被删除，无法继续使用。")
        if result.state == InvalidationState.REVOKED:
            raise InvalidationError("对象已失效，无法继续使用。")

    def register_impact_resolver(
        self, downstream_type: str, resolver: ImpactResolver
    ) -> None:
        """Register a domain impact resolver for a downstream family.

        Resolvers are called in arbitrary order when building an impact set. Each
        resolver must return downstreams scoped to the event's scope; the service
        rejects any downstream that does not match.
        """
        self._impact_resolvers[downstream_type] = resolver

    def register_revalidation_handler(
        self, revalidation_type: str, handler: RevalidationHandler
    ) -> None:
        """Register a domain revalidation handler for a revalidation kind."""
        self._revalidation_handlers[revalidation_type] = handler

    def _get_event(self, event_id: str) -> InvalidationEvent:
        for log in self._logs.values():
            for event in log.events:
                if event.event_id == event_id:
                    return event
        raise InvalidationError("失效事件不存在。")

    def build_impact_set(self, event_id: str) -> ImpactSet:
        """Build a scope-correct impact set from an invalidation event.

        Every registered impact resolver contributes affected downstreams. The
        impact set is stored and can be replayed idempotently.
        """
        event = self._get_event(event_id)
        affected: list[AffectedDownstream] = []

        for resolver in self._impact_resolvers.values():
            for downstream in resolver(event):
                # Enforce scope correctness: downstream must not expand scope.
                if downstream.scope_envelope.account_id != event.scope_envelope.account_id:
                    raise InvalidationError("影响集不能扩大作用域。")
                affected.append(downstream)

        impact_set = ImpactSet(
            impact_set_id=secrets.token_urlsafe(16),
            trigger_event_id=event_id,
            object_ref=event.object_ref,
            affected_downstreams=affected,
            scope_envelope=event.scope_envelope,
            created_at=self._now(),
        )
        self._impact_sets[impact_set.impact_set_id] = impact_set
        return impact_set

    def plan_invalidation(
        self,
        event_id: str,
        *,
        revalidation_delay: timedelta = timedelta(seconds=0),
    ) -> InvalidationPlan:
        """Produce a complete InvalidationPlan from an event.

        The plan includes the event, optional tombstone, impact set, outbox
        entries and revalidation schedules. History is always written before any
        outbox entry is created.
        """
        event = self._get_event(event_id)
        state = self.check_state(event.object_ref)
        if state.effective_event_id != event_id and state.state != InvalidationState.ACTIVE:
            # The event is not the effective event; do not produce a plan from a
            # superseded event.
            raise InvalidationError("该事件已被后续事件覆盖，无法生成计划。")

        impact_set = self.build_impact_set(event_id)
        tombstone = self._log(event.object_ref).tombstone

        outbox_entries: list[OutboxEntry] = []
        revalidation_schedules: list[RevalidationSchedule] = []
        now = self._now()

        for downstream in impact_set.affected_downstreams:
            entry_id = secrets.token_urlsafe(16)
            idempotency_key = f"{event_id}:{downstream.downstream_type}:{downstream.downstream_id}"

            # Deduplicate: skip if an entry with the same idempotency_key already exists.
            existing_entry = next(
                (e for e in self._outbox.values() if e.idempotency_key == idempotency_key),
                None,
            )
            if existing_entry is not None:
                outbox_entries.append(existing_entry)
                continue

            entry = OutboxEntry(
                entry_id=entry_id,
                destination=downstream.downstream_type,
                payload={
                    "trigger_event_id": event_id,
                    "object_ref": event.object_ref.model_dump(),
                    "action": downstream.action,
                    "object_refs": downstream.object_refs,
                    "details": downstream.details,
                },
                scope_envelope=downstream.scope_envelope,
                idempotency_key=idempotency_key,
                status=OutboxStatus.PENDING,
                created_at=now,
            )
            self._outbox[entry.entry_id] = entry
            outbox_entries.append(entry)

            # Schedule a revalidation for any downstream that asks for it.
            if downstream.action == "revalidate":
                schedule = self.schedule_revalidation(
                    object_ref=event.object_ref,
                    revalidation_type=downstream.downstream_type,
                    due_at=now + revalidation_delay,
                    scope_envelope=downstream.scope_envelope,
                )
                revalidation_schedules.append(schedule)

        plan = InvalidationPlan(
            plan_id=secrets.token_urlsafe(16),
            trigger_event_id=event_id,
            event=event,
            tombstone=tombstone,
            impact_set=impact_set,
            outbox_entries=outbox_entries,
            revalidation_schedules=revalidation_schedules,
            created_at=now,
        )
        return plan

    def process_outbox(self, destination: str) -> list[OutboxEntry]:
        """Return and mark as delivered all pending outbox entries for a destination.

        In a persistent deployment this would hand entries to a transactional
        outbox publisher; the in-memory adapter simulates delivery and preserves
        idempotency through idempotency_key.
        """
        now = self._now()
        delivered: list[OutboxEntry] = []
        for entry in self._outbox.values():
            if entry.destination == destination and entry.status == OutboxStatus.PENDING:
                entry.status = OutboxStatus.DELIVERED
                entry.delivered_at = now
                delivered.append(entry)
        return delivered

    def schedule_revalidation(
        self,
        object_ref: ObjectRef,
        revalidation_type: str,
        due_at: datetime,
        *,
        scope_envelope: ScopeEnvelope | None = None,
    ) -> RevalidationSchedule:
        """Schedule an idempotent revalidation.

        Duplicate schedules for the same object, type and scope are deduplicated
        by idempotency_key.
        """
        scope = scope_envelope or self._compile_scope(
            SubjectContext(
                account_id=object_ref.owner_id,
                session_id="invalidation-service",
                auth_method=AuthMethod.SERVICE,
            ),
            object_ref,
        )
        schedule_id = secrets.token_urlsafe(16)
        idempotency_key = (
            f"reval:{object_ref.domain.value}:{object_ref.owner_id}:"
            f"{object_ref.object_id}:{revalidation_type}:{scope.account_id}"
        )

        # Deduplicate by idempotency key.
        for existing in self._schedules.values():
            if existing.idempotency_key == idempotency_key:
                return existing

        schedule = RevalidationSchedule(
            schedule_id=schedule_id,
            object_ref=object_ref,
            revalidation_type=revalidation_type,
            due_at=due_at,
            idempotency_key=idempotency_key,
            status=RevalidationStatus.SCHEDULED,
            scope_envelope=scope,
            created_at=self._now(),
        )
        self._schedules[schedule.schedule_id] = schedule
        return schedule

    def run_revalidation(
        self, schedule_id: str, handler: RevalidationHandler | None = None
    ) -> RevalidationResult:
        """Execute a scheduled revalidation.

        If a handler is not provided explicitly, the registered handler for the
        schedule's revalidation_type is used. Missing handlers fail closed.
        """
        schedule = self._schedules.get(schedule_id)
        if schedule is None:
            raise InvalidationError("重验证计划不存在。")

        if handler is None:
            handler = self._revalidation_handlers.get(schedule.revalidation_type)
        if handler is None:
            result = RevalidationResult(
                schedule_id=schedule_id,
                success=False,
                reason="没有为该重验证类型注册处理器。",
            )
            schedule.status = RevalidationStatus.FAILED
            return result

        result = handler(schedule)
        schedule.status = (
            RevalidationStatus.COMPLETED if result.success else RevalidationStatus.FAILED
        )
        schedule.completed_at = self._now()
        return result

    def list_events(self, object_ref: ObjectRef) -> list[InvalidationEvent]:
        """Return the immutable invalidation event history for an object."""
        return list(self._log(object_ref).events)

    def get_tombstone(self, object_ref: ObjectRef) -> Tombstone | None:
        """Return the tombstone for an object, if any."""
        return self._log(object_ref).tombstone

    def get_impact_set(self, impact_set_id: str) -> ImpactSet | None:
        """Return a previously built impact set."""
        return self._impact_sets.get(impact_set_id)

    def get_outbox_entry(self, entry_id: str) -> OutboxEntry | None:
        """Return a single outbox entry."""
        return self._outbox.get(entry_id)

    def get_revalidation_schedule(self, schedule_id: str) -> RevalidationSchedule | None:
        """Return a single revalidation schedule."""
        return self._schedules.get(schedule_id)
