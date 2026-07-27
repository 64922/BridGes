"""Module-interface tests for the profile observation-candidate loop.

The seam under test: lawful user signals become traceable observations;
observations support explainable candidate profiles; candidates cannot be used as
stable facts until the user accepts them (and even then only after deterministic
promotion checks). Single emotions, role-play, third-party stories and sensitive
identity inferences are discarded and never promoted.
"""

from __future__ import annotations

import pytest

from science_companion.contracts.profiles import (
    AssertionStatus,
    CandidateDecision,
    CandidateReviewStatus,
    DecisionType,
    ObservationStatus,
    ProfileCandidate,
    ProfileObservationCreateRequest,
    ProfileSensitivityClass,
    ProfileSignalKind,
    ProfileSourceType,
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


def _explicit_preference_observation(
    owner: str, content: str = "I prefer short answers when I am in a hurry."
) -> ProfileObservationCreateRequest:
    return ProfileObservationCreateRequest(
        owner_account_id=owner,
        source_type=ProfileSourceType.EXPLICIT_STATEMENT,
        source_ref="conversation-1",
        source_span_or_event="user-message-7",
        scene="quick_check",
        purpose="expression_preference",
        observed_content=content,
        signal_kind=ProfileSignalKind.PREFERENCE,
        extractor_and_version="rule-extractor-1",
        reliability_factors=["explicit_statement", "context_qualified"],
        sensitivity_class=ProfileSensitivityClass.PREFERENCE,
        retention_policy="account_lifetime",
    )


class TestObservationRecording:
    def test_observation_retains_source_scene_and_content_hash(
        self, service: ProfileService, alice_id: str
    ) -> None:
        request = _explicit_preference_observation(alice_id)
        observation = service.record_observation(request)

        assert observation.observation_id
        assert observation.owner_account_id == alice_id
        assert observation.source_type == ProfileSourceType.EXPLICIT_STATEMENT
        assert observation.source_ref == "conversation-1"
        assert observation.source_span_or_event == "user-message-7"
        assert observation.scene == "quick_check"
        assert observation.purpose == "expression_preference"
        assert observation.observed_content == request.observed_content
        assert observation.signal_kind == ProfileSignalKind.PREFERENCE
        assert observation.content_hash
        assert observation.status == ObservationStatus.ACTIVE

    def test_transient_emotion_observation_is_discarded(
        self, service: ProfileService, alice_id: str
    ) -> None:
        request = ProfileObservationCreateRequest(
            owner_account_id=alice_id,
            source_type=ProfileSourceType.SYSTEM_INFERENCE,
            source_ref="conversation-2",
            source_span_or_event="user-message-3",
            scene="frustrated_moment",
            purpose="adaptation_signal",
            observed_content="User seems annoyed today.",
            signal_kind=ProfileSignalKind.TRANSIENT_EMOTION,
            extractor_and_version="emotion-detector-1",
            reliability_factors=["single_turn"],
            sensitivity_class=ProfileSensitivityClass.SENSITIVE,
            retention_policy="session_only",
        )
        observation = service.record_observation(request)

        assert observation.status == ObservationStatus.DISCARDED
        assert observation.signal_kind == ProfileSignalKind.TRANSIENT_EMOTION

    def test_sensitive_identity_observation_is_discarded(
        self, service: ProfileService, alice_id: str
    ) -> None:
        request = ProfileObservationCreateRequest(
            owner_account_id=alice_id,
            source_type=ProfileSourceType.SYSTEM_INFERENCE,
            source_ref="conversation-3",
            source_span_or_event="user-message-1",
            scene="hypothetical_disclosure",
            purpose="health_adaptation",
            observed_content="User might have a specific medical condition.",
            signal_kind=ProfileSignalKind.SENSITIVE_IDENTITY,
            extractor_and_version="identity-inferrer-1",
            reliability_factors=["model_guess"],
            sensitivity_class=ProfileSensitivityClass.PROHIBITED,
            retention_policy="do_not_retain",
        )
        observation = service.record_observation(request)

        assert observation.status == ObservationStatus.DISCARDED
        assert observation.sensitivity_class == ProfileSensitivityClass.PROHIBITED

    def test_role_play_observation_is_discarded(
        self, service: ProfileService, alice_id: str
    ) -> None:
        request = ProfileObservationCreateRequest(
            owner_account_id=alice_id,
            source_type=ProfileSourceType.SYSTEM_INFERENCE,
            source_ref="conversation-4",
            source_span_or_event="user-message-12",
            scene="roleplay_scenario",
            purpose="adaptation_signal",
            observed_content="User pretended to be a historical figure.",
            signal_kind=ProfileSignalKind.ROLE_PLAY,
            extractor_and_version="scanner-1",
            reliability_factors=["single_turn"],
            sensitivity_class=ProfileSensitivityClass.PREFERENCE,
            retention_policy="session_only",
        )
        observation = service.record_observation(request)

        assert observation.status == ObservationStatus.DISCARDED
        assert observation.signal_kind == ProfileSignalKind.ROLE_PLAY

    def test_third_party_story_observation_is_discarded(
        self, service: ProfileService, alice_id: str
    ) -> None:
        request = ProfileObservationCreateRequest(
            owner_account_id=alice_id,
            source_type=ProfileSourceType.SYSTEM_INFERENCE,
            source_ref="conversation-5",
            source_span_or_event="user-message-8",
            scene="storytelling",
            purpose="adaptation_signal",
            observed_content="User told a story about their friend.",
            signal_kind=ProfileSignalKind.THIRD_PARTY_STORY,
            extractor_and_version="scanner-1",
            reliability_factors=["single_turn"],
            sensitivity_class=ProfileSensitivityClass.PREFERENCE,
            retention_policy="session_only",
        )
        observation = service.record_observation(request)

        assert observation.status == ObservationStatus.DISCARDED
        assert observation.signal_kind == ProfileSignalKind.THIRD_PARTY_STORY


class TestCandidateProposal:
    def test_candidate_preserves_supporting_observations_and_evidence(
        self, service: ProfileService, alice_id: str
    ) -> None:
        obs = service.record_observation(_explicit_preference_observation(alice_id))
        candidate = service.propose_candidate(
            alice_id,
            canonical_dimension="expression_brevity",
            value_or_rule="prefer_short_answers_when_hurried",
            applicable_scenes=["quick_check", "mobile_on_the_go"],
            non_applicable_scenes=["deep_research_discussion"],
            supporting_observation_ids=[obs.observation_id],
            contradicting_observation_ids=[],
            evidence_summary="User explicitly stated a scene-qualified preference.",
            authorization_scope="expression_preference",
        )

        assert isinstance(candidate, ProfileCandidate)
        assert candidate.owner_account_id == alice_id
        assert candidate.canonical_dimension == "expression_brevity"
        assert candidate.supporting_observation_ids == [obs.observation_id]
        assert candidate.applicable_scenes == ["quick_check", "mobile_on_the_go"]
        assert candidate.review_status == CandidateReviewStatus.PROPOSED
        assert candidate.human_decision is None

    def test_candidate_cannot_be_proposed_from_discarded_observation(
        self, service: ProfileService, alice_id: str
    ) -> None:
        request = ProfileObservationCreateRequest(
            owner_account_id=alice_id,
            source_type=ProfileSourceType.SYSTEM_INFERENCE,
            source_ref="conversation-2",
            source_span_or_event="user-message-3",
            scene="frustrated_moment",
            purpose="adaptation_signal",
            observed_content="User seems annoyed today.",
            signal_kind=ProfileSignalKind.TRANSIENT_EMOTION,
            extractor_and_version="emotion-detector-1",
            reliability_factors=["single_turn"],
            sensitivity_class=ProfileSensitivityClass.SENSITIVE,
            retention_policy="session_only",
        )
        obs = service.record_observation(request)

        with pytest.raises(ProfileError, match="丢弃"):
            service.propose_candidate(
                alice_id,
                canonical_dimension="mood",
                value_or_rule="user_is_often_annoyed",
                applicable_scenes=["all"],
                supporting_observation_ids=[obs.observation_id],
            )


class TestCandidateHumanDecision:
    def test_accepted_candidate_becomes_active_assertion(
        self, service: ProfileService, alice_id: str
    ) -> None:
        obs = service.record_observation(_explicit_preference_observation(alice_id))
        candidate = service.propose_candidate(
            alice_id,
            canonical_dimension="expression_brevity",
            value_or_rule="prefer_short_answers_when_hurried",
            applicable_scenes=["quick_check"],
            supporting_observation_ids=[obs.observation_id],
        )

        decided = service.decide_candidate(
            alice_id,
            candidate.candidate_id,
            CandidateDecision(
                decision=DecisionType.ACCEPT,
                reason="Confirmed in profile review center.",
            ),
        )

        assert decided.review_status == CandidateReviewStatus.ACCEPTED
        assert decided.human_decision is not None
        assert decided.human_decision.decision == DecisionType.ACCEPT
        assertions = service.list_assertions(alice_id)
        assert len(assertions) == 1
        assert assertions[0].canonical_dimension == "expression_brevity"
        assert assertions[0].status == AssertionStatus.ACTIVE
        assert assertions[0].promoted_from_candidate_id == candidate.candidate_id

    def test_rejected_candidate_does_not_create_assertion(
        self, service: ProfileService, alice_id: str
    ) -> None:
        obs = service.record_observation(_explicit_preference_observation(alice_id))
        candidate = service.propose_candidate(
            alice_id,
            canonical_dimension="expression_brevity",
            value_or_rule="prefer_short_answers_when_hurried",
            applicable_scenes=["quick_check"],
            supporting_observation_ids=[obs.observation_id],
        )

        decided = service.decide_candidate(
            alice_id,
            candidate.candidate_id,
            CandidateDecision(
                decision=DecisionType.REJECT,
                reason="Not representative of my usual preference.",
            ),
        )

        assert decided.review_status == CandidateReviewStatus.REJECTED
        assert service.list_assertions(alice_id) == []

    def test_modified_candidate_creates_assertion_with_modified_value(
        self, service: ProfileService, alice_id: str
    ) -> None:
        obs = service.record_observation(_explicit_preference_observation(alice_id))
        candidate = service.propose_candidate(
            alice_id,
            canonical_dimension="expression_brevity",
            value_or_rule="prefer_short_answers_when_hurried",
            applicable_scenes=["quick_check"],
            supporting_observation_ids=[obs.observation_id],
        )

        decided = service.decide_candidate(
            alice_id,
            candidate.candidate_id,
            CandidateDecision(
                decision=DecisionType.MODIFY,
                reason="Only applies to workday mornings.",
                modified_value_or_rule="prefer_short_answers_on_workday_mornings",
                modified_applicable_scenes=["workday_morning"],
            ),
        )

        assert decided.review_status == CandidateReviewStatus.MODIFIED
        assert decided.value_or_rule == "prefer_short_answers_on_workday_mornings"
        assert decided.applicable_scenes == ["workday_morning"]
        assertions = service.list_assertions(alice_id)
        assert len(assertions) == 1
        assert assertions[0].value_or_rule == "prefer_short_answers_on_workday_mornings"
        assert assertions[0].applicable_scenes == ["workday_morning"]

    def test_user_sees_decision_rationale(
        self, service: ProfileService, alice_id: str
    ) -> None:
        obs = service.record_observation(_explicit_preference_observation(alice_id))
        candidate = service.propose_candidate(
            alice_id,
            canonical_dimension="expression_brevity",
            value_or_rule="prefer_short_answers_when_hurried",
            applicable_scenes=["quick_check"],
            supporting_observation_ids=[obs.observation_id],
        )

        decided = service.decide_candidate(
            alice_id,
            candidate.candidate_id,
            CandidateDecision(
                decision=DecisionType.REJECT,
                reason="That was a one-off deadline, not a habit.",
            ),
        )

        assert decided.human_decision is not None
        assert decided.human_decision.reason == "That was a one-off deadline, not a habit."

    def test_double_decision_is_rejected(
        self, service: ProfileService, alice_id: str
    ) -> None:
        obs = service.record_observation(_explicit_preference_observation(alice_id))
        candidate = service.propose_candidate(
            alice_id,
            canonical_dimension="expression_brevity",
            value_or_rule="prefer_short_answers_when_hurried",
            applicable_scenes=["quick_check"],
            supporting_observation_ids=[obs.observation_id],
        )
        service.decide_candidate(
            alice_id,
            candidate.candidate_id,
            CandidateDecision(decision=DecisionType.ACCEPT, reason="Confirmed."),
        )

        with pytest.raises(ProfileError, match="无法晋升"):
            service.decide_candidate(
                alice_id,
                candidate.candidate_id,
                CandidateDecision(decision=DecisionType.ACCEPT, reason="Again."),
            )


class TestMemorySliceExcludesCandidates:
    def test_unconfirmed_candidates_are_not_used_as_stable_facts(
        self, service: ProfileService, alice_id: str
    ) -> None:
        obs = service.record_observation(_explicit_preference_observation(alice_id))
        candidate = service.propose_candidate(
            alice_id,
            canonical_dimension="expression_brevity",
            value_or_rule="prefer_short_answers_when_hurried",
            applicable_scenes=["quick_check"],
            supporting_observation_ids=[obs.observation_id],
        )

        slice_ = service.compile_memory_slice(
            alice_id, purpose="quick_check", run_id="run-123"
        )

        assert slice_.run_id == "run-123"
        assert candidate.candidate_id in slice_.excluded_candidate_ids
        assert slice_.included_items == []

    def test_accepted_assertions_are_included_in_slice_when_scene_matches(
        self, service: ProfileService, alice_id: str
    ) -> None:
        obs = service.record_observation(_explicit_preference_observation(alice_id))
        candidate = service.propose_candidate(
            alice_id,
            canonical_dimension="expression_brevity",
            value_or_rule="prefer_short_answers_when_hurried",
            applicable_scenes=["quick_check"],
            supporting_observation_ids=[obs.observation_id],
        )
        service.decide_candidate(
            alice_id,
            candidate.candidate_id,
            CandidateDecision(decision=DecisionType.ACCEPT, reason="Confirmed."),
        )

        slice_ = service.compile_memory_slice(
            alice_id, purpose="quick_check", run_id="run-124"
        )

        assert len(slice_.included_items) == 1
        assert slice_.included_items[0].dimension == "expression_brevity"
        assert slice_.excluded_candidate_ids == []


class TestCrossAccountIsolation:
    def test_one_account_cannot_read_anothers_profile(
        self, service: ProfileService, alice_id: str
    ) -> None:
        obs = service.record_observation(_explicit_preference_observation(alice_id))

        with pytest.raises(ProfileError, match="访问权限"):
            service.get_observation("account-bob", obs.observation_id)

        candidate = service.propose_candidate(
            alice_id,
            canonical_dimension="expression_brevity",
            value_or_rule="prefer_short_answers_when_hurried",
            applicable_scenes=["quick_check"],
            supporting_observation_ids=[obs.observation_id],
        )

        with pytest.raises(ProfileError, match="访问权限"):
            service.get_candidate("account-bob", candidate.candidate_id)

    def test_cross_account_decision_is_rejected(
        self, service: ProfileService, alice_id: str
    ) -> None:
        obs = service.record_observation(_explicit_preference_observation(alice_id))
        candidate = service.propose_candidate(
            alice_id,
            canonical_dimension="expression_brevity",
            value_or_rule="prefer_short_answers_when_hurried",
            applicable_scenes=["quick_check"],
            supporting_observation_ids=[obs.observation_id],
        )

        with pytest.raises(ProfileError, match="访问权限"):
            service.decide_candidate(
                "account-bob",
                candidate.candidate_id,
                CandidateDecision(decision=DecisionType.ACCEPT, reason="Hacked."),
            )
