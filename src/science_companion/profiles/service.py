"""Profile observation-candidate domain service."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime

from science_companion.contracts.identity import AuthMethod, SubjectContext
from science_companion.contracts.profiles import (
    AssertionStatus,
    CandidateDecision,
    CandidateReviewStatus,
    CandidateStabilityState,
    DecisionType,
    HumanDecision,
    ObservationStatus,
    ProfileAssertion,
    ProfileCandidate,
    ProfileObservation,
    ProfileObservationCreateRequest,
    ProfileSensitivityClass,
    ProfileSignalKind,
    ProfileSlice,
    ProfileSliceItem,
)
from science_companion.profiles.adapters import ProfileError
from science_companion.profiles.ports import ProfileRepository
from science_companion.scope import ScopeEnforcer

_AUTO_DISCARD_SIGNAL_KINDS: set[ProfileSignalKind] = {
    ProfileSignalKind.TRANSIENT_EMOTION,
    ProfileSignalKind.ROLE_PLAY,
    ProfileSignalKind.THIRD_PARTY_STORY,
    ProfileSignalKind.SENSITIVE_IDENTITY,
}

_PROHIBITED_SENSITIVITY: set[ProfileSensitivityClass] = {
    ProfileSensitivityClass.PROHIBITED,
}


def _now() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return secrets.token_urlsafe(16)


def _hash_observation(request: ProfileObservationCreateRequest) -> str:
    payload = "|".join(
        [
            request.observed_content,
            request.source_ref,
            request.source_span_or_event,
            request.scene,
            request.purpose,
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ProfileService:
    """Application service for the profile observation-candidate loop."""

    def __init__(
        self,
        repository: ProfileRepository,
        scope_enforcer: ScopeEnforcer | None = None,
    ) -> None:
        self._repository = repository
        self._scope_enforcer = scope_enforcer or ScopeEnforcer()

    def _subject(self, account_id: str) -> SubjectContext:
        return SubjectContext(
            account_id=account_id,
            session_id="service-session",
            auth_method=AuthMethod.PASSWORD,
        )

    def record_observation(
        self, request: ProfileObservationCreateRequest
    ) -> ProfileObservation:
        """Record a traceable atomic observation.

        Observations carrying transient emotions, role-play, third-party stories
        or sensitive/prohibited identity inferences are discarded immediately and
        cannot support candidate profiles.
        """
        now = _now()
        status = ObservationStatus.ACTIVE
        if (
            request.signal_kind in _AUTO_DISCARD_SIGNAL_KINDS
            or request.sensitivity_class in _PROHIBITED_SENSITIVITY
        ):
            status = ObservationStatus.DISCARDED

        observation = ProfileObservation(
            observation_id=_new_id(),
            owner_account_id=request.owner_account_id,
            project_id=request.project_id,
            source_type=request.source_type,
            source_ref=request.source_ref,
            source_span_or_event=request.source_span_or_event,
            scene=request.scene,
            purpose=request.purpose,
            observed_content=request.observed_content,
            signal_kind=request.signal_kind,
            extractor_and_version=request.extractor_and_version,
            model_rationale=request.model_rationale,
            reliability_factors=list(request.reliability_factors),
            sensitivity_class=request.sensitivity_class,
            retention_policy=request.retention_policy,
            authorization_version=request.authorization_version,
            content_hash=_hash_observation(request),
            status=status,
            created_at=now,
            updated_at=now,
        )
        return self._repository.save_observation(observation)

    def get_observation(self, account_id: str, observation_id: str) -> ProfileObservation:
        """Return an observation if the account owns it."""
        return self._repository.get_observation(account_id, observation_id)

    def list_observations(self, account_id: str) -> list[ProfileObservation]:
        """List observations for the account."""
        return self._repository.list_observations(account_id)

    def propose_candidate(
        self,
        account_id: str,
        *,
        canonical_dimension: str,
        value_or_rule: str,
        applicable_scenes: list[str] | None = None,
        non_applicable_scenes: list[str] | None = None,
        supporting_observation_ids: list[str],
        contradicting_observation_ids: list[str] | None = None,
        evidence_summary: str = "",
        authorization_scope: str = "general",
        promotion_policy_version: str = "promotion-1.0",
        expires_at: datetime | None = None,
    ) -> ProfileCandidate:
        """Propose a candidate profile from supporting observations.

        Candidates built on discarded observations are rejected deterministically.
        """
        for observation_id in supporting_observation_ids:
            observation = self._repository.get_observation(account_id, observation_id)
            if observation.status == ObservationStatus.DISCARDED:
                raise ProfileError(
                    "不能从已丢弃的观察构建候选画像。"
                )
            if (
                observation.signal_kind in _AUTO_DISCARD_SIGNAL_KINDS
                or observation.sensitivity_class in _PROHIBITED_SENSITIVITY
            ):
                raise ProfileError(
                    "单次情绪、示例人物或敏感身份推断不能形成候选画像。"
                )

        now = _now()
        candidate = ProfileCandidate(
            candidate_id=_new_id(),
            owner_account_id=account_id,
            canonical_dimension=canonical_dimension,
            value_or_rule=value_or_rule,
            applicable_scenes=list(applicable_scenes or []),
            non_applicable_scenes=list(non_applicable_scenes or []),
            supporting_observation_ids=list(supporting_observation_ids),
            contradicting_observation_ids=list(contradicting_observation_ids or []),
            evidence_summary=evidence_summary,
            authorization_scope=authorization_scope,
            promotion_policy_version=promotion_policy_version,
            review_status=CandidateReviewStatus.PROPOSED,
            stability_state=CandidateStabilityState.CANDIDATE,
            proposed_at=now,
            updated_at=now,
            expires_at=expires_at,
        )
        return self._repository.save_candidate(candidate)

    def get_candidate(self, account_id: str, candidate_id: str) -> ProfileCandidate:
        """Return a candidate if the account owns it."""
        return self._repository.get_candidate(account_id, candidate_id)

    def list_candidates(self, account_id: str) -> list[ProfileCandidate]:
        """List candidates for the account."""
        return self._repository.list_candidates(account_id)

    def _can_promote(self, candidate: ProfileCandidate) -> bool:
        """Deterministic promotion gate before an accepted candidate becomes active.

        A candidate must be in PROPOSED state — already-ACCEPTED, MODIFIED, REJECTED,
        CONFLICTED, or DELETED candidates cannot be promoted again.
        """
        return (
            candidate.review_status == CandidateReviewStatus.PROPOSED
            and candidate.stability_state != CandidateStabilityState.DELETED
        )

    def _promote_candidate(
        self, candidate: ProfileCandidate, value_or_rule: str, applicable_scenes: list[str]
    ) -> ProfileAssertion:
        """Promote a candidate to an active assertion, creating a new version."""
        now = _now()
        assertion = ProfileAssertion(
            assertion_id=_new_id(),
            owner_account_id=candidate.owner_account_id,
            canonical_dimension=candidate.canonical_dimension,
            value_or_rule=value_or_rule,
            applicable_scenes=list(applicable_scenes),
            supporting_observation_ids=list(candidate.supporting_observation_ids),
            contradicting_observation_ids=list(candidate.contradicting_observation_ids),
            authorization_scope=candidate.authorization_scope,
            status=AssertionStatus.ACTIVE,
            promoted_from_candidate_id=candidate.candidate_id,
            version=1,
            created_at=now,
            updated_at=now,
        )
        return self._repository.save_assertion(assertion)

    def decide_candidate(
        self,
        account_id: str,
        candidate_id: str,
        decision: CandidateDecision,
    ) -> ProfileCandidate:
        """Record a human decision on a candidate and, if accepted, promote it."""
        candidate = self._repository.get_candidate(account_id, candidate_id)

        now = _now()
        human_decision = HumanDecision(
            decision_id=_new_id(),
            candidate_id=candidate_id,
            account_id=account_id,
            decision=decision.decision,
            reason=decision.reason,
            modified_value_or_rule=decision.modified_value_or_rule,
            modified_applicable_scenes=decision.modified_applicable_scenes,
            created_at=now,
        )
        candidate.human_decision = human_decision
        candidate.updated_at = now

        if decision.decision == DecisionType.ACCEPT:
            if not self._can_promote(candidate):
                raise ProfileError("该候选画像当前无法晋升为稳定画像。")
            value = candidate.value_or_rule
            scenes = list(candidate.applicable_scenes)
            self._promote_candidate(candidate, value, scenes)
            candidate.review_status = CandidateReviewStatus.ACCEPTED
            candidate.stability_state = CandidateStabilityState.ACTIVE
        elif decision.decision == DecisionType.MODIFY:
            value = decision.modified_value_or_rule or candidate.value_or_rule
            scenes = (
                decision.modified_applicable_scenes
                if decision.modified_applicable_scenes is not None
                else list(candidate.applicable_scenes)
            )
            if not self._can_promote(candidate):
                raise ProfileError("该候选画像当前无法晋升为稳定画像。")
            self._promote_candidate(candidate, value, scenes)
            candidate.value_or_rule = value
            candidate.applicable_scenes = list(scenes)
            candidate.review_status = CandidateReviewStatus.MODIFIED
            candidate.stability_state = CandidateStabilityState.ACTIVE
        elif decision.decision == DecisionType.REJECT:
            candidate.review_status = CandidateReviewStatus.REJECTED
            candidate.stability_state = CandidateStabilityState.CANDIDATE
        else:
            raise ProfileError("未知决定类型。")

        return self._repository.save_candidate(candidate)

    def list_assertions(self, account_id: str) -> list[ProfileAssertion]:
        """List promoted profile assertions for the account."""
        return self._repository.list_assertions(account_id)

    def compile_memory_slice(
        self, account_id: str, *, purpose: str, run_id: str
    ) -> ProfileSlice:
        """Compile the minimal, authorized profile slice for a run.

        Only active assertions whose applicable scenes cover the purpose are
        included. Candidates in any non-accepted state are explicitly excluded so
        they can never be treated as stable facts.
        """
        now = _now()
        included: list[ProfileSliceItem] = []
        excluded_candidate_ids: list[str] = []
        exclusion_reasons: dict[str, str] = {}

        for assertion in self._repository.list_assertions(account_id):
            if assertion.status != AssertionStatus.ACTIVE:
                continue
            if purpose in assertion.applicable_scenes or not assertion.applicable_scenes:
                included.append(
                    ProfileSliceItem(
                        assertion_id=assertion.assertion_id,
                        dimension=assertion.canonical_dimension,
                        value_or_rule=assertion.value_or_rule,
                        inclusion_reason=f"Active assertion applicable to purpose '{purpose}'.",
                    )
                )

        for candidate in self._repository.list_candidates(account_id):
            if candidate.review_status not in {
                CandidateReviewStatus.ACCEPTED,
                CandidateReviewStatus.MODIFIED,
            }:
                excluded_candidate_ids.append(candidate.candidate_id)
                status_value = candidate.review_status.value
                exclusion_reasons[candidate.candidate_id] = (
                    f"Candidate status '{status_value}' cannot be used as a stable fact."
                )

        return ProfileSlice(
            slice_id=_new_id(),
            run_id=run_id,
            purpose=purpose,
            included_items=included,
            excluded_candidate_ids=excluded_candidate_ids,
            exclusion_reasons=exclusion_reasons,
            compiled_at=now,
        )
