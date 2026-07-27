"""Module-interface tests for memory slice compilation, explanation and invalidation.

The seam under test: a slice is compiled from active assertions according to task
purpose, project scope, authorization snapshot, key epoch, expiration and
sensitivity class. Models and worker nodes can access only the bound slice, and
users can inspect used, unused and rejected items with reasons. Slices become
unusable when they expire or when the run is cancelled.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from science_companion.contracts.profiles import (
    CandidateDecision,
    DecisionType,
    ProfileObservationCreateRequest,
    ProfileSensitivityClass,
    ProfileSignalKind,
    ProfileSourceType,
    SliceStatus,
)
from science_companion.profiles import (
    InMemoryProfileRepository,
    ProfileError,
    ProfileService,
)


@pytest.fixture
def repository() -> InMemoryProfileRepository:
    return InMemoryProfileRepository()


@pytest.fixture
def service(repository: InMemoryProfileRepository) -> ProfileService:
    return ProfileService(repository)


@pytest.fixture
def alice_id() -> str:
    return "account-alice"


def _observation(
    owner: str,
    content: str,
    signal_kind: ProfileSignalKind = ProfileSignalKind.PREFERENCE,
    sensitivity: ProfileSensitivityClass = ProfileSensitivityClass.PREFERENCE,
) -> ProfileObservationCreateRequest:
    return ProfileObservationCreateRequest(
        owner_account_id=owner,
        source_type=ProfileSourceType.EXPLICIT_STATEMENT,
        source_ref="conversation-1",
        source_span_or_event="user-message-1",
        scene="quick_check",
        purpose="expression_preference",
        observed_content=content,
        signal_kind=signal_kind,
        extractor_and_version="rule-extractor-1",
        reliability_factors=["explicit_statement"],
        sensitivity_class=sensitivity,
        retention_policy="account_lifetime",
    )


def _propose_and_accept(
    service: ProfileService,
    account_id: str,
    observation_id: str,
    dimension: str,
    value: str,
    scenes: list[str],
    sensitivity: ProfileSensitivityClass = ProfileSensitivityClass.PREFERENCE,
    expires_at: datetime | None = None,
) -> None:
    candidate = service.propose_candidate(
        account_id,
        canonical_dimension=dimension,
        value_or_rule=value,
        applicable_scenes=scenes,
        supporting_observation_ids=[observation_id],
        sensitivity_class=sensitivity,
        expires_at=expires_at,
    )
    service.decide_candidate(
        account_id,
        candidate.candidate_id,
        CandidateDecision(decision=DecisionType.ACCEPT, reason="Confirmed."),
    )


class TestSliceCompilationFilters:
    def test_slice_includes_only_active_assertions_matching_purpose(
        self, service: ProfileService, alice_id: str
    ) -> None:
        obs1 = service.record_observation(
            _observation(alice_id, "Prefer short answers.")
        )
        _propose_and_accept(
            service,
            alice_id,
            obs1.observation_id,
            "expression_brevity",
            "short_answers",
            ["quick_check"],
        )

        obs2 = service.record_observation(
            _observation(alice_id, "Prefer detailed explanations.")
        )
        _propose_and_accept(
            service,
            alice_id,
            obs2.observation_id,
            "expression_depth",
            "detailed",
            ["deep_research"],
        )

        slice_ = service.compile_memory_slice(
            alice_id, purpose="quick_check", run_id="run-1"
        )

        assert len(slice_.included_items) == 1
        assert slice_.included_items[0].dimension == "expression_brevity"
        assert len(slice_.unused_items) == 1
        assert slice_.unused_items[0].dimension == "expression_depth"
        assert "quick_check" in slice_.unused_items[0].exclusion_reason

    def test_slice_excludes_expired_assertions(
        self, service: ProfileService, alice_id: str
    ) -> None:
        obs = service.record_observation(
            _observation(alice_id, "Prefer short answers.")
        )
        _propose_and_accept(
            service,
            alice_id,
            obs.observation_id,
            "expression_brevity",
            "short_answers",
            ["quick_check"],
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )

        slice_ = service.compile_memory_slice(
            alice_id, purpose="quick_check", run_id="run-2"
        )

        assert slice_.included_items == []
        assert len(slice_.unused_items) == 1
        assert "expired" in slice_.unused_items[0].exclusion_reason.lower()

    def test_slice_excludes_assertions_outside_sensitivity_allow_list(
        self, service: ProfileService, alice_id: str
    ) -> None:
        obs = service.record_observation(
            _observation(
                alice_id,
                "Prefer short answers.",
                sensitivity=ProfileSensitivityClass.SENSITIVE,
            )
        )
        _propose_and_accept(
            service,
            alice_id,
            obs.observation_id,
            "expression_brevity",
            "short_answers",
            ["quick_check"],
            sensitivity=ProfileSensitivityClass.SENSITIVE,
        )

        slice_ = service.compile_memory_slice(
            alice_id,
            purpose="quick_check",
            run_id="run-3",
            sensitivity_classes=[ProfileSensitivityClass.PREFERENCE],
        )

        assert slice_.included_items == []
        assert len(slice_.unused_items) == 1
        assert "sensitivity" in slice_.unused_items[0].exclusion_reason.lower()

    def test_slice_records_authorization_key_epoch_and_expiration(
        self, service: ProfileService, alice_id: str
    ) -> None:
        obs = service.record_observation(
            _observation(alice_id, "Prefer short answers.")
        )
        _propose_and_accept(
            service,
            alice_id,
            obs.observation_id,
            "expression_brevity",
            "short_answers",
            ["quick_check"],
        )

        slice_ = service.compile_memory_slice(
            alice_id,
            purpose="quick_check",
            run_id="run-4",
            project_id="project-1",
            authorization_version="authz-2.0",
            key_epoch="epoch-1",
            ttl_seconds=600,
        )

        assert slice_.project_id == "project-1"
        assert slice_.authorization_snapshot == "authz-2.0"
        assert slice_.key_epoch == "epoch-1"
        assert slice_.expires_at is not None
        assert slice_.expires_at > datetime.now(UTC)
        assert slice_.expires_at <= datetime.now(UTC) + timedelta(seconds=600)


class TestSliceInspectorShowsReasons:
    def test_slice_includes_rejected_candidates_with_reasons(
        self, service: ProfileService, alice_id: str
    ) -> None:
        obs = service.record_observation(
            _observation(alice_id, "Prefer short answers.")
        )
        candidate = service.propose_candidate(
            alice_id,
            canonical_dimension="expression_brevity",
            value_or_rule="short_answers",
            applicable_scenes=["quick_check"],
            supporting_observation_ids=[obs.observation_id],
        )
        service.decide_candidate(
            alice_id,
            candidate.candidate_id,
            CandidateDecision(decision=DecisionType.REJECT, reason="One-off preference."),
        )

        slice_ = service.compile_memory_slice(
            alice_id, purpose="quick_check", run_id="run-5"
        )

        assert candidate.candidate_id in slice_.excluded_candidate_ids
        assert len(slice_.rejected_items) == 1
        assert slice_.rejected_items[0].candidate_id == candidate.candidate_id
        assert "rejected" in slice_.rejected_items[0].rejection_reason.lower()


class TestSliceAccessControl:
    def test_require_slice_for_run_returns_slice_when_valid(
        self, service: ProfileService, alice_id: str
    ) -> None:
        obs = service.record_observation(
            _observation(alice_id, "Prefer short answers.")
        )
        _propose_and_accept(
            service,
            alice_id,
            obs.observation_id,
            "expression_brevity",
            "short_answers",
            ["quick_check"],
        )
        slice_ = service.compile_memory_slice(
            alice_id, purpose="quick_check", run_id="run-6"
        )

        fetched = service.require_slice_for_run(slice_.slice_id, "run-6")
        assert fetched.slice_id == slice_.slice_id

    def test_require_slice_for_run_fails_when_run_mismatches(
        self, service: ProfileService, alice_id: str
    ) -> None:
        obs = service.record_observation(
            _observation(alice_id, "Prefer short answers.")
        )
        _propose_and_accept(
            service,
            alice_id,
            obs.observation_id,
            "expression_brevity",
            "short_answers",
            ["quick_check"],
        )
        slice_ = service.compile_memory_slice(
            alice_id, purpose="quick_check", run_id="run-7"
        )

        with pytest.raises(ProfileError, match="绑定"):
            service.require_slice_for_run(slice_.slice_id, "other-run")

    def test_require_slice_for_run_fails_when_expired(
        self, service: ProfileService, alice_id: str
    ) -> None:
        obs = service.record_observation(
            _observation(alice_id, "Prefer short answers.")
        )
        _propose_and_accept(
            service,
            alice_id,
            obs.observation_id,
            "expression_brevity",
            "short_answers",
            ["quick_check"],
        )
        slice_ = service.compile_memory_slice(
            alice_id, purpose="quick_check", run_id="run-8", ttl_seconds=-1
        )

        with pytest.raises(ProfileError, match="过期"):
            service.require_slice_for_run(slice_.slice_id, "run-8")

    def test_model_cannot_browse_full_vault_through_slice_access(
        self, service: ProfileService, alice_id: str
    ) -> None:
        obs = service.record_observation(
            _observation(alice_id, "Prefer short answers.")
        )
        _propose_and_accept(
            service,
            alice_id,
            obs.observation_id,
            "expression_brevity",
            "short_answers",
            ["quick_check"],
        )
        service.compile_memory_slice(alice_id, purpose="quick_check", run_id="run-9")

        with pytest.raises(ProfileError, match="不存在"):
            service.require_slice_for_run("nonexistent-slice", "run-9")


class TestSliceInvalidation:
    def test_cancelled_slice_cannot_be_read(
        self, service: ProfileService, alice_id: str
    ) -> None:
        obs = service.record_observation(
            _observation(alice_id, "Prefer short answers.")
        )
        _propose_and_accept(
            service,
            alice_id,
            obs.observation_id,
            "expression_brevity",
            "short_answers",
            ["quick_check"],
        )
        slice_ = service.compile_memory_slice(
            alice_id, purpose="quick_check", run_id="run-10"
        )

        invalidated = service.invalidate_slice(
            alice_id, slice_.slice_id, "run cancelled", SliceStatus.CANCELLED
        )
        assert invalidated.status == SliceStatus.CANCELLED
        assert invalidated.invalidation_reason == "run cancelled"

        with pytest.raises(ProfileError, match="取消"):
            service.require_slice_for_run(slice_.slice_id, "run-10")

    def test_revoked_slice_cannot_be_read(
        self, service: ProfileService, alice_id: str
    ) -> None:
        obs = service.record_observation(
            _observation(alice_id, "Prefer short answers.")
        )
        _propose_and_accept(
            service,
            alice_id,
            obs.observation_id,
            "expression_brevity",
            "short_answers",
            ["quick_check"],
        )
        slice_ = service.compile_memory_slice(
            alice_id, purpose="quick_check", run_id="run-11"
        )

        service.invalidate_slice(
            alice_id, slice_.slice_id, "authorization withdrawn", SliceStatus.REVOKED
        )

        with pytest.raises(ProfileError, match="撤权"):
            service.require_slice_for_run(slice_.slice_id, "run-11")
