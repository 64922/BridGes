"""Module-interface tests for the invalidation, tombstone and impact foundation.

The seam under test: an authenticated subject records an immutable invalidation
event or tombstone for an object; the object state is checked before new reads
and runs; scope-correct impact sets are propagated idempotently through an
outbox; and revalidations are scheduled and executed without expanding scope.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from science_companion.contracts.identity import AuthMethod, SubjectContext
from science_companion.contracts.projects import ObjectDomain, ObjectRef
from science_companion.contracts.invalidation import (
    AffectedDownstream,
    InvalidationEventType,
    InvalidationState,
    OutboxStatus,
    RevalidationResult,
    RevalidationStatus,
)
from science_companion.invalidation import InvalidationError, InvalidationService


@pytest.fixture
def enforcer() -> InvalidationService:
    return InvalidationService()


@pytest.fixture
def alice() -> SubjectContext:
    return SubjectContext(
        account_id="account-alice",
        session_id="session-alice",
        auth_method=AuthMethod.PASSWORD,
    )


@pytest.fixture
def bob() -> SubjectContext:
    return SubjectContext(
        account_id="account-bob",
        session_id="session-bob",
        auth_method=AuthMethod.PASSWORD,
    )


@pytest.fixture
def object_ref() -> ObjectRef:
    return ObjectRef(
        domain=ObjectDomain.PERSONAL_VAULT,
        owner_id="account-alice",
        object_id="object-1",
        version=1,
    )


def test_record_event_is_immutable_and_bound_to_scope(
    enforcer: InvalidationService, alice: SubjectContext, object_ref: ObjectRef
) -> None:
    event = enforcer.record_invalidation_event(
        alice,
        object_ref,
        InvalidationEventType.REVOKE,
        "用户撤权",
    )

    assert event.event_id
    assert event.event_type == InvalidationEventType.REVOKE
    assert event.object_ref == object_ref
    assert event.subject == alice
    assert event.scope_envelope.account_id == alice.account_id
    assert event.scope_envelope.object_domain == ObjectDomain.PERSONAL_VAULT
    assert event.reason == "用户撤权"
    assert event.authorization_version
    assert event.key_epoch
    assert event.sequence == 1
    assert event.occurred_at <= datetime.now(timezone.utc)


def test_tombstone_takes_precedence_over_revocation(
    enforcer: InvalidationService, alice: SubjectContext, object_ref: ObjectRef
) -> None:
    enforcer.record_invalidation_event(
        alice, object_ref, InvalidationEventType.REVOKE, "先失效"
    )
    event, tombstone = enforcer.record_tombstone(alice, object_ref, "再删除")

    state = enforcer.check_state(object_ref)
    assert state.state == InvalidationState.TOMBSTONED
    assert state.effective_event_id == event.event_id
    assert tombstone.event_id == event.event_id
    assert tombstone.sequence == event.sequence


def test_history_is_append_only(
    enforcer: InvalidationService, alice: SubjectContext, object_ref: ObjectRef
) -> None:
    first = enforcer.record_invalidation_event(
        alice, object_ref, InvalidationEventType.REVOKE, "第一次撤权"
    )
    second = enforcer.record_invalidation_event(
        alice, object_ref, InvalidationEventType.REVOKE, "第二次撤权"
    )

    history = enforcer.list_events(object_ref)
    assert len(history) == 2
    assert history[0].event_id == first.event_id
    assert history[1].event_id == second.event_id
    assert history[1].sequence > history[0].sequence


def test_active_object_passes_require_active(
    enforcer: InvalidationService, object_ref: ObjectRef
) -> None:
    result = enforcer.check_state(object_ref)
    assert result.state == InvalidationState.ACTIVE
    assert result.effective_sequence == 0
    # Should not raise.
    enforcer.require_active(object_ref)


def test_revoked_object_blocks_new_use(
    enforcer: InvalidationService, alice: SubjectContext, object_ref: ObjectRef
) -> None:
    enforcer.record_invalidation_event(
        alice, object_ref, InvalidationEventType.REVOKE, "撤权"
    )

    with pytest.raises(InvalidationError, match="对象已失效"):
        enforcer.require_active(object_ref)


def test_tombstoned_object_blocks_new_use(
    enforcer: InvalidationService, alice: SubjectContext, object_ref: ObjectRef
) -> None:
    enforcer.record_tombstone(alice, object_ref, "删除")

    with pytest.raises(InvalidationError, match="对象已被删除"):
        enforcer.require_active(object_ref)


def test_cross_account_invalidation_is_rejected(
    enforcer: InvalidationService, bob: SubjectContext, object_ref: ObjectRef
) -> None:
    with pytest.raises(InvalidationError, match="访问权限"):
        enforcer.record_invalidation_event(
            bob, object_ref, InvalidationEventType.REVOKE, "越权撤权"
        )


def test_cross_account_tombstone_is_rejected(
    enforcer: InvalidationService, bob: SubjectContext, object_ref: ObjectRef
) -> None:
    with pytest.raises(InvalidationError, match="访问权限"):
        enforcer.record_tombstone(bob, object_ref, "越权删除")


class TestImpactSetAndOutbox:
    def test_build_impact_set_calls_registered_resolvers(
        self,
        enforcer: InvalidationService,
        alice: SubjectContext,
        object_ref: ObjectRef,
    ) -> None:
        def cache_resolver(event: Any) -> list[AffectedDownstream]:
            return [
                AffectedDownstream(
                    downstream_id="cache-1",
                    downstream_type="cache",
                    object_refs=[event.object_ref.object_id],
                    scope_envelope=event.scope_envelope,
                    action="invalidate",
                )
            ]

        enforcer.register_impact_resolver("cache", cache_resolver)
        event = enforcer.record_invalidation_event(
            alice, object_ref, InvalidationEventType.REVOKE, "撤权"
        )

        impact_set = enforcer.build_impact_set(event.event_id)
        assert impact_set.trigger_event_id == event.event_id
        assert len(impact_set.affected_downstreams) == 1
        downstream = impact_set.affected_downstreams[0]
        assert downstream.downstream_type == "cache"
        assert downstream.action == "invalidate"
        assert downstream.scope_envelope.account_id == alice.account_id

    def test_impact_set_rejects_scope_expansion(
        self,
        enforcer: InvalidationService,
        alice: SubjectContext,
        bob: SubjectContext,
        object_ref: ObjectRef,
    ) -> None:
        event = enforcer.record_invalidation_event(
            alice, object_ref, InvalidationEventType.REVOKE, "撤权"
        )
        from science_companion.contracts.scope import ScopeEnvelope

        bob_scope = ScopeEnvelope(account_id=bob.account_id)

        def bad_resolver(event: Any) -> list[AffectedDownstream]:
            return [
                AffectedDownstream(
                    downstream_id="bad",
                    downstream_type="cache",
                    object_refs=[event.object_ref.object_id],
                    scope_envelope=bob_scope,
                    action="invalidate",
                )
            ]

        enforcer.register_impact_resolver("cache", bad_resolver)
        with pytest.raises(InvalidationError, match="不能扩大作用域"):
            enforcer.build_impact_set(event.event_id)

    def test_plan_invalidation_creates_outbox_and_revalidation(
        self,
        enforcer: InvalidationService,
        alice: SubjectContext,
        object_ref: ObjectRef,
    ) -> None:
        def index_resolver(event: Any) -> list[AffectedDownstream]:
            return [
                AffectedDownstream(
                    downstream_id="index-1",
                    downstream_type="index_projection",
                    object_refs=[event.object_ref.object_id],
                    scope_envelope=event.scope_envelope,
                    action="revalidate",
                )
            ]

        enforcer.register_impact_resolver("index_projection", index_resolver)
        event = enforcer.record_invalidation_event(
            alice, object_ref, InvalidationEventType.SOURCE_RETRACTED, "来源撤回"
        )

        plan = enforcer.plan_invalidation(event.event_id)
        assert plan.event.event_id == event.event_id
        assert plan.impact_set.impact_set_id
        assert len(plan.outbox_entries) == 1
        assert len(plan.revalidation_schedules) == 1

        entry = plan.outbox_entries[0]
        assert entry.destination == "index_projection"
        assert entry.status == OutboxStatus.PENDING
        assert entry.idempotency_key == f"{event.event_id}:index_projection:index-1"

        schedule = plan.revalidation_schedules[0]
        assert schedule.revalidation_type == "index_projection"
        assert schedule.status == RevalidationStatus.SCHEDULED
        assert schedule.scope_envelope.account_id == alice.account_id

    def test_outbox_process_is_idempotent(
        self,
        enforcer: InvalidationService,
        alice: SubjectContext,
        object_ref: ObjectRef,
    ) -> None:
        def cache_resolver(event: Any) -> list[AffectedDownstream]:
            return [
                AffectedDownstream(
                    downstream_id="cache-1",
                    downstream_type="cache",
                    object_refs=[event.object_ref.object_id],
                    scope_envelope=event.scope_envelope,
                    action="invalidate",
                )
            ]

        enforcer.register_impact_resolver("cache", cache_resolver)
        event = enforcer.record_invalidation_event(
            alice, object_ref, InvalidationEventType.REVOKE, "撤权"
        )
        enforcer.plan_invalidation(event.event_id)

        first = enforcer.process_outbox("cache")
        second = enforcer.process_outbox("cache")
        assert len(first) == 1
        assert first[0].status == OutboxStatus.DELIVERED
        assert second == []


class TestRevalidation:
    def test_revalidation_schedule_is_deduplicated(
        self,
        enforcer: InvalidationService,
        object_ref: ObjectRef,
    ) -> None:
        due = datetime.now(timezone.utc) + timedelta(hours=1)
        first = enforcer.schedule_revalidation(
            object_ref, "index_projection", due
        )
        second = enforcer.schedule_revalidation(
            object_ref, "index_projection", due
        )
        assert first.schedule_id == second.schedule_id
        assert first.idempotency_key == second.idempotency_key

    def test_run_revalidation_with_handler(
        self,
        enforcer: InvalidationService,
        object_ref: ObjectRef,
    ) -> None:
        def handler(schedule: Any) -> RevalidationResult:
            return RevalidationResult(
                schedule_id=schedule.schedule_id,
                success=True,
                reason="ok",
            )

        enforcer.register_revalidation_handler("index_projection", handler)
        due = datetime.now(timezone.utc) + timedelta(hours=1)
        schedule = enforcer.schedule_revalidation(
            object_ref, "index_projection", due
        )

        result = enforcer.run_revalidation(schedule.schedule_id)
        assert result.success is True
        updated = enforcer.get_revalidation_schedule(schedule.schedule_id)
        assert updated is not None
        assert updated.status == RevalidationStatus.COMPLETED

    def test_run_revalidation_without_handler_fails_closed(
        self,
        enforcer: InvalidationService,
        object_ref: ObjectRef,
    ) -> None:
        due = datetime.now(timezone.utc) + timedelta(hours=1)
        schedule = enforcer.schedule_revalidation(
            object_ref, "unknown_type", due
        )

        result = enforcer.run_revalidation(schedule.schedule_id)
        assert result.success is False
        updated = enforcer.get_revalidation_schedule(schedule.schedule_id)
        assert updated is not None
        assert updated.status == RevalidationStatus.FAILED
