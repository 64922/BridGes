"""Module-interface tests for the profile assertion governance lifecycle.

The seam under test: an authenticated user can confirm, modify, freeze, delete,
export and roll back evidence-backed profile assertions. Governance operations
must propagate to compiled memory slices, block new recall, produce immutable
invalidation plans for cache/index/run downstreams, and emit privacy-preserving
audit events that do not retain deleted body.
"""

from __future__ import annotations

from typing import Any

import pytest

from bridges.contracts.invalidation import (
    AffectedDownstream,
    InvalidationEventType,
    InvalidationState,
)
from bridges.contracts.observability import AuditAction
from bridges.contracts.profiles import (
    AssertionStatus,
    CandidateDecision,
    CandidateReviewStatus,
    DecisionType,
    ProfileObservationCreateRequest,
    ProfileSensitivityClass,
    ProfileSignalKind,
    ProfileSourceType,
    SliceStatus,
)
from bridges.invalidation import InvalidationService
from bridges.observability.service import ObservabilityService
from bridges.profiles import (
    InMemoryProfileRepository,
    ProfileError,
    ProfileService,
)


@pytest.fixture
def repository() -> InMemoryProfileRepository:
    return InMemoryProfileRepository()


@pytest.fixture
def invalidation_service() -> InvalidationService:
    service = InvalidationService()
    _register_generic_resolvers(service)
    return service


@pytest.fixture
def observability_service() -> ObservabilityService:
    return ObservabilityService()


@pytest.fixture
def service(
    repository: InMemoryProfileRepository,
    invalidation_service: InvalidationService,
    observability_service: ObservabilityService,
) -> ProfileService:
    profile_service = ProfileService(
        repository=repository,
        invalidation_service=invalidation_service,
        observability_service=observability_service,
    )

    def _profile_resolver(event: Any) -> list[AffectedDownstream]:
        affected: list[AffectedDownstream] = []
        for slice_ in profile_service.find_slices_for_assertion(
            event.object_ref.owner_id, event.object_ref.object_id
        ):
            affected.append(
                AffectedDownstream(
                    downstream_id=f"memory_slice:{slice_.slice_id}",
                    downstream_type="memory_slice",
                    object_refs=[slice_.slice_id],
                    scope_envelope=event.scope_envelope,
                    action="revoke",
                )
            )
        return affected

    invalidation_service.register_impact_resolver(
        "profile_assertion", _profile_resolver
    )
    return profile_service


@pytest.fixture
def alice_id() -> str:
    return "account-alice"


@pytest.fixture
def bob_id() -> str:
    return "account-bob"


def _register_generic_resolvers(service: InvalidationService) -> None:
    """Register the generic downstream resolvers used by T011.

    Module tests do not run the full app factory, so the same resolvers are
    attached directly to keep invalidation-plan assertions deterministic.
    """

    def _cache_resolver(event: Any) -> list[AffectedDownstream]:
        return [
            AffectedDownstream(
                downstream_id=f"cache:{event.object_ref.object_id}",
                downstream_type="cache",
                object_refs=[event.object_ref.object_id],
                scope_envelope=event.scope_envelope,
                action="invalidate",
            )
        ]

    def _index_resolver(event: Any) -> list[AffectedDownstream]:
        return [
            AffectedDownstream(
                downstream_id=f"index:{event.object_ref.object_id}",
                downstream_type="index_projection",
                object_refs=[event.object_ref.object_id],
                scope_envelope=event.scope_envelope,
                action="revalidate",
            )
        ]

    def _run_resolver(event: Any) -> list[AffectedDownstream]:
        return [
            AffectedDownstream(
                downstream_id=f"run:{event.object_ref.object_id}",
                downstream_type="workflow_run",
                object_refs=[event.object_ref.object_id],
                scope_envelope=event.scope_envelope,
                action="block_new_use",
            )
        ]

    service.register_impact_resolver("cache", _cache_resolver)
    service.register_impact_resolver("index_projection", _index_resolver)
    service.register_impact_resolver("workflow_run", _run_resolver)


def _explicit_observation(owner: str, content: str) -> ProfileObservationCreateRequest:
    return ProfileObservationCreateRequest(
        owner_account_id=owner,
        source_type=ProfileSourceType.EXPLICIT_STATEMENT,
        source_ref="conversation-1",
        source_span_or_event="user-message-1",
        scene="quick_check",
        purpose="expression_preference",
        observed_content=content,
        signal_kind=ProfileSignalKind.PREFERENCE,
        extractor_and_version="rule-extractor-1",
        reliability_factors=["explicit_statement"],
        sensitivity_class=ProfileSensitivityClass.PREFERENCE,
        retention_policy="account_lifetime",
    )


def _accept_candidate(
    service: ProfileService, account_id: str, observation_id: str
) -> str:
    candidate = service.propose_candidate(
        account_id,
        canonical_dimension="expression_brevity",
        value_or_rule="prefer_short_answers",
        applicable_scenes=["quick_check"],
        supporting_observation_ids=[observation_id],
    )
    service.decide_candidate(
        account_id,
        candidate.candidate_id,
        CandidateDecision(decision=DecisionType.ACCEPT, reason="Confirmed."),
    )
    assertions = service.list_assertions(account_id)
    assert len(assertions) == 1
    return assertions[0].assertion_id


def _compile_slice(
    service: ProfileService, account_id: str, run_id: str
) -> list[str]:
    slice_ = service.compile_memory_slice(
        account_id, purpose="quick_check", run_id=run_id
    )
    return [item.value_or_rule for item in slice_.included_items]


class TestAssertionFreeze:
    def test_freeze_excludes_assertion_from_new_slices(
        self,
        service: ProfileService,
        alice_id: str,
    ) -> None:
        obs = service.record_observation(_explicit_observation(alice_id, "Prefer short."))
        assertion_id = _accept_candidate(service, alice_id, obs.observation_id)

        before = _compile_slice(service, alice_id, "run-before-freeze")
        assert before == ["prefer_short_answers"]

        frozen = service.freeze_assertion(alice_id, assertion_id, "No longer relevant.")
        assert frozen.status == AssertionStatus.FROZEN
        assert frozen.version == 2

        after = _compile_slice(service, alice_id, "run-after-freeze")
        assert after == []

    def test_freeze_revokes_existing_slices(
        self,
        service: ProfileService,
        alice_id: str,
    ) -> None:
        obs = service.record_observation(_explicit_observation(alice_id, "Prefer short."))
        assertion_id = _accept_candidate(service, alice_id, obs.observation_id)
        slice_ = service.compile_memory_slice(
            alice_id, purpose="quick_check", run_id="run-frozen-slice"
        )

        service.freeze_assertion(alice_id, assertion_id, "No longer relevant.")

        revoked = service.get_slice(alice_id, slice_.slice_id)
        assert revoked.status == SliceStatus.REVOKED
        assert "frozen" in (revoked.invalidation_reason or "").lower()

    def test_freeze_creates_version_snapshot(
        self,
        service: ProfileService,
        alice_id: str,
    ) -> None:
        obs = service.record_observation(_explicit_observation(alice_id, "Prefer short."))
        assertion_id = _accept_candidate(service, alice_id, obs.observation_id)

        service.freeze_assertion(alice_id, assertion_id, "No longer relevant.")

        history = service._repository.list_assertion_versions(alice_id, assertion_id)
        assert len(history) == 1
        assert history[0].status == AssertionStatus.ACTIVE
        assert history[0].value_or_rule == "prefer_short_answers"


class TestAssertionModify:
    def test_modify_updates_value_and_excludes_old_value(
        self,
        service: ProfileService,
        alice_id: str,
    ) -> None:
        obs = service.record_observation(_explicit_observation(alice_id, "Prefer short."))
        assertion_id = _accept_candidate(service, alice_id, obs.observation_id)

        before = _compile_slice(service, alice_id, "run-before-modify")
        assert before == ["prefer_short_answers"]

        modified = service.modify_assertion(
            alice_id,
            assertion_id,
            value_or_rule="prefer_detailed_answers",
            applicable_scenes=["deep_research"],
            reason="Preference changed after review.",
        )
        assert modified.value_or_rule == "prefer_detailed_answers"
        assert modified.applicable_scenes == ["deep_research"]
        assert modified.version == 2

        quick = _compile_slice(service, alice_id, "run-after-modify-quick")
        assert quick == []
        deep = service.compile_memory_slice(
            alice_id, purpose="deep_research", run_id="run-after-modify-deep"
        )
        assert [item.value_or_rule for item in deep.included_items] == [
            "prefer_detailed_answers"
        ]

    def test_modify_preserves_history(
        self,
        service: ProfileService,
        alice_id: str,
    ) -> None:
        obs = service.record_observation(_explicit_observation(alice_id, "Prefer short."))
        assertion_id = _accept_candidate(service, alice_id, obs.observation_id)

        service.modify_assertion(
            alice_id,
            assertion_id,
            value_or_rule="prefer_detailed_answers",
            applicable_scenes=["deep_research"],
            reason="Preference changed.",
        )

        history = service._repository.list_assertion_versions(alice_id, assertion_id)
        assert len(history) == 1
        assert history[0].value_or_rule == "prefer_short_answers"
        assert history[0].applicable_scenes == ["quick_check"]


class TestAssertionDelete:
    def test_delete_writes_tombstone_and_blocks_new_recall(
        self,
        service: ProfileService,
        invalidation_service: InvalidationService,
        alice_id: str,
    ) -> None:
        obs = service.record_observation(_explicit_observation(alice_id, "Prefer short."))
        assertion_id = _accept_candidate(service, alice_id, obs.observation_id)

        before = _compile_slice(service, alice_id, "run-before-delete")
        assert before == ["prefer_short_answers"]

        deleted = service.delete_assertion(alice_id, assertion_id, "User requested deletion.")
        assert deleted.status == AssertionStatus.DELETED
        assert deleted.version == 2

        after = _compile_slice(service, alice_id, "run-after-delete")
        assert after == []

        state = invalidation_service.check_state(
            service._assertion_object_ref(deleted)
        )
        assert state.state == InvalidationState.TOMBSTONED

    def test_delete_creates_invalidation_plan_for_downstreams(
        self,
        service: ProfileService,
        invalidation_service: InvalidationService,
        alice_id: str,
    ) -> None:
        obs = service.record_observation(_explicit_observation(alice_id, "Prefer short."))
        assertion_id = _accept_candidate(service, alice_id, obs.observation_id)
        service.compile_memory_slice(
            alice_id, purpose="quick_check", run_id="run-delete-plan"
        )

        deleted = service.delete_assertion(alice_id, assertion_id, "User requested deletion.")

        # The most recent event for the assertion must be a DELETE and have a plan.
        events = invalidation_service.list_events(
            service._assertion_object_ref(deleted)
        )
        delete_event = [e for e in events if e.event_type == InvalidationEventType.DELETE][-1]
        plan = invalidation_service.plan_invalidation(delete_event.event_id)

        types = {d.downstream_type for d in plan.impact_set.affected_downstreams}
        assert "cache" in types
        assert "index_projection" in types
        assert "workflow_run" in types
        assert "memory_slice" in types

    def test_delete_revokes_existing_slices(
        self,
        service: ProfileService,
        alice_id: str,
    ) -> None:
        obs = service.record_observation(_explicit_observation(alice_id, "Prefer short."))
        assertion_id = _accept_candidate(service, alice_id, obs.observation_id)
        slice_ = service.compile_memory_slice(
            alice_id, purpose="quick_check", run_id="run-delete-slice"
        )

        service.delete_assertion(alice_id, assertion_id, "User requested deletion.")

        revoked = service.get_slice(alice_id, slice_.slice_id)
        assert revoked.status == SliceStatus.REVOKED


class TestAssertionRollback:
    def test_rollback_reverts_to_previous_version(
        self,
        service: ProfileService,
        alice_id: str,
    ) -> None:
        obs = service.record_observation(_explicit_observation(alice_id, "Prefer short."))
        assertion_id = _accept_candidate(service, alice_id, obs.observation_id)
        service.modify_assertion(
            alice_id,
            assertion_id,
            value_or_rule="prefer_detailed_answers",
            applicable_scenes=["deep_research"],
            reason="Preference changed.",
        )

        before = _compile_slice(service, alice_id, "run-before-rollback")
        assert before == []

        rolled = service.rollback_assertion(
            alice_id, assertion_id, to_version=1, reason="Mistake."
        )
        assert rolled.value_or_rule == "prefer_short_answers"
        assert rolled.applicable_scenes == ["quick_check"]
        assert rolled.status == AssertionStatus.ACTIVE
        assert rolled.version == 3

        after = _compile_slice(service, alice_id, "run-after-rollback")
        assert after == ["prefer_short_answers"]

    def test_rollback_preserves_history(
        self,
        service: ProfileService,
        alice_id: str,
    ) -> None:
        obs = service.record_observation(_explicit_observation(alice_id, "Prefer short."))
        assertion_id = _accept_candidate(service, alice_id, obs.observation_id)
        service.modify_assertion(
            alice_id,
            assertion_id,
            value_or_rule="prefer_detailed_answers",
            applicable_scenes=["deep_research"],
            reason="Preference changed.",
        )

        service.rollback_assertion(alice_id, assertion_id, to_version=1, reason="Mistake.")

        history = service._repository.list_assertion_versions(alice_id, assertion_id)
        assert len(history) == 2
        assert history[0].value_or_rule == "prefer_short_answers"
        assert history[1].value_or_rule == "prefer_detailed_answers"


class TestProfileExport:
    def test_export_includes_assertions_and_history(
        self,
        service: ProfileService,
        alice_id: str,
    ) -> None:
        obs = service.record_observation(_explicit_observation(alice_id, "Prefer short."))
        assertion_id = _accept_candidate(service, alice_id, obs.observation_id)
        service.freeze_assertion(alice_id, assertion_id, "No longer relevant.")

        export = service.export_profile_data(alice_id)
        assert export.owner_account_id == alice_id
        assert len(export.assertions) == 1
        assert export.assertions[0].status == AssertionStatus.FROZEN
        assert export.assertions[0].value_or_rule == "prefer_short_answers"
        assert assertion_id in export.history
        assert len(export.history[assertion_id].versions) == 1
        assert export.audit_event_refs

    def test_export_redacts_deleted_assertion_value(
        self,
        service: ProfileService,
        alice_id: str,
    ) -> None:
        obs = service.record_observation(_explicit_observation(alice_id, "Prefer short."))
        assertion_id = _accept_candidate(service, alice_id, obs.observation_id)
        service.delete_assertion(alice_id, assertion_id, "User requested deletion.")

        export = service.export_profile_data(alice_id)
        assert len(export.assertions) == 1
        assert export.assertions[0].status == AssertionStatus.DELETED
        assert export.assertions[0].value_or_rule is None
        assert export.assertions[0].deleted_at is not None

    def test_export_audit_events_do_not_contain_body(
        self,
        service: ProfileService,
        observability_service: ObservabilityService,
        alice_id: str,
    ) -> None:
        obs = service.record_observation(_explicit_observation(alice_id, "Prefer short."))
        assertion_id = _accept_candidate(service, alice_id, obs.observation_id)
        service.delete_assertion(alice_id, assertion_id, "User requested deletion.")

        events = observability_service.list_audit_events(
            account_id=alice_id, action=AuditAction.PROFILE_DELETE
        )
        assert len(events) == 1
        details = events[0].details
        assert "prefer_short_answers" not in str(details)
        assert "content_hash" in details


class TestGovernanceIsolation:
    def test_cross_account_freeze_is_rejected(
        self,
        service: ProfileService,
        alice_id: str,
        bob_id: str,
    ) -> None:
        obs = service.record_observation(_explicit_observation(alice_id, "Prefer short."))
        assertion_id = _accept_candidate(service, alice_id, obs.observation_id)

        with pytest.raises(ProfileError, match="访问权限"):
            service.freeze_assertion(bob_id, assertion_id, "Hacked.")

    def test_cross_account_delete_is_rejected(
        self,
        service: ProfileService,
        alice_id: str,
        bob_id: str,
    ) -> None:
        obs = service.record_observation(_explicit_observation(alice_id, "Prefer short."))
        assertion_id = _accept_candidate(service, alice_id, obs.observation_id)

        with pytest.raises(ProfileError, match="访问权限"):
            service.delete_assertion(bob_id, assertion_id, "Hacked.")


class TestCandidateConfirmation:
    def test_accepted_candidate_is_confirmed_assertion(
        self,
        service: ProfileService,
        alice_id: str,
    ) -> None:
        obs = service.record_observation(_explicit_observation(alice_id, "Prefer short."))
        candidate = service.propose_candidate(
            alice_id,
            canonical_dimension="expression_brevity",
            value_or_rule="prefer_short_answers",
            applicable_scenes=["quick_check"],
            supporting_observation_ids=[obs.observation_id],
        )
        decided = service.decide_candidate(
            alice_id,
            candidate.candidate_id,
            CandidateDecision(decision=DecisionType.ACCEPT, reason="Confirmed."),
        )
        assert decided.review_status == CandidateReviewStatus.ACCEPTED
        assertions = service.list_assertions(alice_id)
        assert len(assertions) == 1
        assert assertions[0].status == AssertionStatus.ACTIVE
