"""Profile observation-candidate domain service."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from bridges.contracts.identity import AuthMethod, SubjectContext
from bridges.contracts.observability import AuditAction, AuditResult
from bridges.contracts.profiles import (
    AssertionStatus,
    CandidateDecision,
    CandidateReviewStatus,
    CandidateStabilityState,
    DecisionType,
    HumanDecision,
    ManualAssertionCreateRequest,
    ObservationStatus,
    ProfileAssertion,
    ProfileAssertionHistory,
    ProfileAssertionVersion,
    ProfileCandidate,
    ProfileDimension,
    ProfileExport,
    ProfileExportAssertion,
    ProfileObservation,
    ProfileObservationCreateRequest,
    ProfileSensitivityClass,
    ProfileSignalKind,
    ProfileSlice,
    ProfileSliceCompileRequest,
    ProfileSliceItem,
    ProfileSourceType,
    RejectedSliceItem,
    SliceStatus,
    UnusedSliceItem,
)
from bridges.contracts.projects import ObjectDomain, ObjectRef
from bridges.invalidation import InvalidationService
from bridges.observability.service import ObservabilityService
from bridges.profiles.adapters import ProfileError
from bridges.profiles.ports import ProfileRepository
from bridges.scope import ScopeEnforcer

_AUTO_DISCARD_SIGNAL_KINDS: set[ProfileSignalKind] = {
    ProfileSignalKind.TRANSIENT_EMOTION,
    ProfileSignalKind.ROLE_PLAY,
    ProfileSignalKind.THIRD_PARTY_STORY,
    ProfileSignalKind.SENSITIVE_IDENTITY,
}

_PROHIBITED_SENSITIVITY: set[ProfileSensitivityClass] = {
    ProfileSensitivityClass.PROHIBITED,
}

#: Signal kind used for each dimension when the user manually declares a record.
#: All mapped kinds are outside the auto-discard set so user-declared records are
#: never silently discarded as transient signals.
_DIMENSION_TO_SIGNAL_KIND: dict[ProfileDimension, ProfileSignalKind] = {
    ProfileDimension.BASIC_INFORMATION: ProfileSignalKind.OTHER,
    ProfileDimension.STAGE_GOAL: ProfileSignalKind.GOAL,
    ProfileDimension.INTEREST_PREFERENCE: ProfileSignalKind.PREFERENCE,
    ProfileDimension.EXPRESSION_HABIT: ProfileSignalKind.STYLE,
    ProfileDimension.KNOWLEDGE_STATE: ProfileSignalKind.PRIOR_KNOWLEDGE,
    ProfileDimension.EMOTION_TREND: ProfileSignalKind.OTHER,
    ProfileDimension.IMPORTANT_EXPERIENCE: ProfileSignalKind.OTHER,
    ProfileDimension.CURRENT_PROBLEM: ProfileSignalKind.OTHER,
    ProfileDimension.AUTHORIZATION_SCOPE: ProfileSignalKind.OTHER,
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
        invalidation_service: InvalidationService | None = None,
        observability_service: ObservabilityService | None = None,
    ) -> None:
        self._repository = repository
        self._scope_enforcer = scope_enforcer or ScopeEnforcer()
        self._invalidation_service = invalidation_service
        self._observability_service = observability_service

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
        sensitivity_class: ProfileSensitivityClass = ProfileSensitivityClass.PREFERENCE,
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
            sensitivity_class=sensitivity_class,
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

    def get_assertion(self, account_id: str, assertion_id: str) -> ProfileAssertion:
        """Return a promoted assertion if the account owns it."""
        return self._repository.get_assertion(account_id, assertion_id)

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
            sensitivity_class=candidate.sensitivity_class,
            expires_at=candidate.expires_at,
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

        self._audit(
            account_id=account_id,
            action=AuditAction.PROFILE_CANDIDATE_DECISION,
            result=AuditResult.SUCCESS,
            object_refs=[candidate_id],
            reason=decision.reason,
            details={
                "decision": decision.decision.value,
                "dimension": candidate.canonical_dimension,
            },
        )
        return self._repository.save_candidate(candidate)

    def list_assertions(self, account_id: str) -> list[ProfileAssertion]:
        """List promoted profile assertions for the account."""
        return self._repository.list_assertions(account_id)

    def _hash_assertion_value(self, value_or_rule: str, applicable_scenes: list[str]) -> str:
        payload = "|".join([value_or_rule, *sorted(applicable_scenes)])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _assertion_object_ref(self, assertion: ProfileAssertion) -> ObjectRef:
        return ObjectRef(
            domain=ObjectDomain.PERSONAL_VAULT,
            owner_id=assertion.owner_account_id,
            object_id=assertion.assertion_id,
            version=assertion.version,
        )

    def _snapshot_assertion(
        self,
        assertion: ProfileAssertion,
        reason: str,
        changed_by: str,
    ) -> ProfileAssertionVersion:
        """Snapshot an assertion before any state transition.

        ``changed_by`` distinguishes user operations ("user") from future
        candidate-triggered changes ("candidate") so the history view can say
        who caused each version. All current transitions are user-driven.
        """
        return ProfileAssertionVersion(
            version_id=_new_id(),
            assertion_id=assertion.assertion_id,
            owner_account_id=assertion.owner_account_id,
            version=assertion.version,
            canonical_dimension=assertion.canonical_dimension,
            value_or_rule=assertion.value_or_rule,
            applicable_scenes=list(assertion.applicable_scenes),
            status=assertion.status,
            sensitivity_class=assertion.sensitivity_class,
            promoted_from_candidate_id=assertion.promoted_from_candidate_id,
            content_hash=self._hash_assertion_value(
                assertion.value_or_rule, assertion.applicable_scenes
            ),
            changed_at=_now(),
            changed_by=changed_by,
            change_reason=reason,
        )

    def _audit(
        self,
        account_id: str,
        action: AuditAction,
        result: AuditResult,
        object_refs: list[str],
        reason: str,
        details: dict[str, object] | None = None,
    ) -> str | None:
        if self._observability_service is None:
            return None
        event = self._observability_service.log_audit(
            actor_account_id=account_id,
            action=action,
            result=result,
            object_refs=object_refs,
            reason=reason,
            details=details,
        )
        return event.event_id

    def find_slices_for_assertion(
        self, account_id: str, assertion_id: str
    ) -> list[ProfileSlice]:
        """Return active slices that include the assertion.

        Public so the invalidation service's profile impact resolver can build
        scope-correct downstream entries without accessing the repository directly.
        """
        return self._repository.list_slices_containing_assertion(account_id, assertion_id)

    def _invalidate_slices_for_assertion(
        self, account_id: str, assertion_id: str, reason: str
    ) -> list[str]:
        invalidated: list[str] = []
        for slice_ in self.find_slices_for_assertion(account_id, assertion_id):
            if slice_.status != SliceStatus.ACTIVE:
                continue
            slice_.status = SliceStatus.REVOKED
            slice_.invalidated_at = _now()
            slice_.invalidation_reason = reason
            self._repository.save_slice(slice_)
            invalidated.append(slice_.slice_id)
        return invalidated

    def _is_assertion_expired(self, assertion: ProfileAssertion, now: datetime) -> bool:
        return assertion.expires_at is not None and assertion.expires_at <= now

    def _is_sensitivity_allowed(
        self,
        sensitivity: ProfileSensitivityClass,
        allowed: list[ProfileSensitivityClass] | None,
    ) -> bool:
        if sensitivity in _PROHIBITED_SENSITIVITY:
            return False
        if allowed is None or not allowed:
            return sensitivity != ProfileSensitivityClass.PROHIBITED
        return sensitivity in allowed

    def manual_create_assertion(
        self, account_id: str, request: ManualAssertionCreateRequest
    ) -> ProfileAssertion:
        """Create a governed profile record directly from a user declaration.

        The user is the declarer, so no separate confirmation step is needed:
        the record is promoted immediately through the regular observation →
        candidate → accept path so its provenance stays traceable. The account
        audit records the creation with the declared source note.
        """
        observation = self.record_observation(
            ProfileObservationCreateRequest(
                owner_account_id=account_id,
                source_type=ProfileSourceType.EXPLICIT_STATEMENT,
                source_ref=request.source_note,
                source_span_or_event="manual",
                scene="用户手动新增画像记录",
                purpose="用户主动声明画像事实",
                observed_content=request.value_or_rule,
                signal_kind=_DIMENSION_TO_SIGNAL_KIND[request.dimension],
                extractor_and_version="manual-1.0",
                sensitivity_class=request.sensitivity_class,
                retention_policy="user-managed",
            )
        )
        candidate = self.propose_candidate(
            account_id,
            canonical_dimension=request.dimension.value,
            value_or_rule=request.value_or_rule,
            applicable_scenes=request.applicable_scenes,
            supporting_observation_ids=[observation.observation_id],
            evidence_summary="用户手动声明",
            authorization_scope=request.authorization_scope,
            sensitivity_class=request.sensitivity_class,
        )
        self.decide_candidate(
            account_id,
            candidate.candidate_id,
            CandidateDecision(
                decision=DecisionType.ACCEPT,
                reason="用户手动确认新增画像记录",
            ),
        )
        assertion = next(
            (
                a
                for a in self._repository.list_assertions(account_id)
                if a.promoted_from_candidate_id == candidate.candidate_id
            ),
            None,
        )
        if assertion is None:
            raise ProfileError("画像记录创建失败，请稍后重试。")
        self._audit(
            account_id=account_id,
            action=AuditAction.PROFILE_CREATE,
            result=AuditResult.SUCCESS,
            object_refs=[assertion.assertion_id],
            reason=request.source_note,
            details={
                "dimension": request.dimension.value,
                "observation_id": observation.observation_id,
                "candidate_id": candidate.candidate_id,
                "content_hash": self._hash_assertion_value(
                    assertion.value_or_rule, assertion.applicable_scenes
                ),
            },
        )
        return assertion

    def _transition_status(
        self,
        account_id: str,
        assertion_id: str,
        *,
        target_status: AssertionStatus,
        allowed_statuses: set[AssertionStatus],
        failure_message: str,
        audit_action: AuditAction,
        revoke_slices: bool,
        reason: str,
    ) -> ProfileAssertion:
        """Snapshot and transition an assertion with an unambiguous audit event.

        Shared by freeze/withdraw/unfreeze: each snapshots the current value
        (changed_by="user"), bumps the version, emits its own audit action and
        optionally revokes slices that used the assertion.
        """
        assertion = self._repository.get_assertion(account_id, assertion_id)
        if assertion.status not in allowed_statuses:
            raise ProfileError(failure_message)

        previous_version = assertion.version
        self._repository.save_assertion_version(
            self._snapshot_assertion(assertion, reason, "user")
        )
        assertion.status = target_status
        assertion.version = previous_version + 1
        assertion.updated_at = _now()
        self._repository.save_assertion(assertion)

        invalidated_slices: list[str] = []
        if revoke_slices:
            invalidated_slices = self._invalidate_slices_for_assertion(
                account_id, assertion_id, f"assertion {target_status.value}: {reason}"
            )
        self._audit(
            account_id=account_id,
            action=audit_action,
            result=AuditResult.SUCCESS,
            object_refs=[assertion_id],
            reason=reason,
            details={
                "previous_version": previous_version,
                "content_hash": self._hash_assertion_value(
                    assertion.value_or_rule, assertion.applicable_scenes
                ),
                "invalidated_slice_ids": invalidated_slices,
            },
        )
        return assertion

    def freeze_assertion(
        self, account_id: str, assertion_id: str, reason: str
    ) -> ProfileAssertion:
        """Freeze an active assertion so it stops being recalled for new runs.

        Freezing creates a version snapshot and emits an audit event; existing
        slices that contain the assertion are revoked. From Issue 26 frozen
        assertions additionally refuse automatic updates.
        """
        return self._transition_status(
            account_id,
            assertion_id,
            target_status=AssertionStatus.FROZEN,
            allowed_statuses={AssertionStatus.ACTIVE},
            failure_message="只能冻结处于活跃状态的画像断言。",
            audit_action=AuditAction.PROFILE_FREEZE,
            revoke_slices=True,
            reason=reason,
        )

    def withdraw_assertion(
        self, account_id: str, assertion_id: str, reason: str
    ) -> ProfileAssertion:
        """Withdraw an active assertion: it stops being used in answers.

        Withdrawal keeps the auditable history and version snapshots; existing
        slices that included the assertion are revoked so models cannot keep
        using it. The record can later be restored with ``unfreeze_assertion``.
        """
        return self._transition_status(
            account_id,
            assertion_id,
            target_status=AssertionStatus.WITHDRAWN,
            allowed_statuses={AssertionStatus.ACTIVE},
            failure_message="只能撤回处于活跃状态的画像断言。",
            audit_action=AuditAction.PROFILE_WITHDRAW,
            revoke_slices=True,
            reason=reason,
        )

    def unfreeze_assertion(
        self, account_id: str, assertion_id: str, reason: str
    ) -> ProfileAssertion:
        """Restore a frozen or withdrawn assertion back to active use.

        Unfreezing creates a version snapshot recording the restoration; the
        record becomes usable in new slices again. Automatic updates stay gated
        by the category-level permissions (Issue 26), not by this operation.
        """
        return self._transition_status(
            account_id,
            assertion_id,
            target_status=AssertionStatus.ACTIVE,
            allowed_statuses={AssertionStatus.FROZEN, AssertionStatus.WITHDRAWN},
            failure_message="只能解冻已冻结或已撤回的画像断言。",
            audit_action=AuditAction.PROFILE_UNFREEZE,
            revoke_slices=False,
            reason=reason,
        )

    def modify_assertion(
        self,
        account_id: str,
        assertion_id: str,
        value_or_rule: str,
        applicable_scenes: list[str],
        reason: str,
    ) -> ProfileAssertion:
        """Modify an active assertion, creating a new version.

        The previous value is preserved in the version history so the user can
        later roll back. Existing slices using the old value are revoked.
        """
        assertion = self._repository.get_assertion(account_id, assertion_id)
        if assertion.status != AssertionStatus.ACTIVE:
            raise ProfileError("只能修改处于活跃状态的画像断言。")

        previous_version = assertion.version
        self._repository.save_assertion_version(
            self._snapshot_assertion(assertion, reason, "user")
        )
        assertion.value_or_rule = value_or_rule
        assertion.applicable_scenes = list(applicable_scenes)
        assertion.version = previous_version + 1
        assertion.updated_at = _now()
        self._repository.save_assertion(assertion)

        invalidated_slices = self._invalidate_slices_for_assertion(
            account_id, assertion_id, f"assertion modified: {reason}"
        )
        self._audit(
            account_id=account_id,
            action=AuditAction.PROFILE_MODIFY,
            result=AuditResult.SUCCESS,
            object_refs=[assertion_id],
            reason=reason,
            details={
                "previous_version": previous_version,
                "new_content_hash": self._hash_assertion_value(
                    value_or_rule, applicable_scenes
                ),
                "invalidated_slice_ids": invalidated_slices,
            },
        )
        return assertion

    def delete_assertion(
        self, account_id: str, assertion_id: str, reason: str
    ) -> ProfileAssertion:
        """Delete a profile assertion by writing a tombstone first.

        Deletion records an immutable tombstone through the invalidation service,
        marks the assertion deleted, revokes any slices that included it, and
        produces an invalidation plan covering cache, index and derived impacts.
        """
        assertion = self._repository.get_assertion(account_id, assertion_id)
        if assertion.status == AssertionStatus.DELETED:
            raise ProfileError("画像断言已被删除。")

        previous_version = assertion.version
        self._repository.save_assertion_version(
            self._snapshot_assertion(assertion, reason, "user")
        )
        assertion.status = AssertionStatus.DELETED
        assertion.version = previous_version + 1
        assertion.updated_at = _now()
        self._repository.save_assertion(assertion)

        if self._invalidation_service is None:
            raise ProfileError("删除服务未配置。")

        object_ref = self._assertion_object_ref(assertion)
        event, tombstone = self._invalidation_service.record_tombstone(
            self._subject(account_id), object_ref, reason
        )
        plan = self._invalidation_service.plan_invalidation(event.event_id)

        invalidated_slices = self._invalidate_slices_for_assertion(
            account_id, assertion_id, f"assertion deleted: {reason}"
        )
        self._audit(
            account_id=account_id,
            action=AuditAction.PROFILE_DELETE,
            result=AuditResult.SUCCESS,
            object_refs=[assertion_id],
            reason=reason,
            details={
                "previous_version": previous_version,
                "tombstone_id": tombstone.tombstone_id,
                "event_id": event.event_id,
                "plan_id": plan.plan_id,
                "content_hash": self._hash_assertion_value(
                    assertion.value_or_rule, assertion.applicable_scenes
                ),
                "invalidated_slice_ids": invalidated_slices,
            },
        )
        return assertion

    def rollback_assertion(
        self,
        account_id: str,
        assertion_id: str,
        to_version: int,
        reason: str,
    ) -> ProfileAssertion:
        """Roll an assertion back to a previous version snapshot.

        Rollback creates a new active version from the chosen historical snapshot
        without erasing the audit chain. Slices that used the superseded value are
        revoked.
        """
        assertion = self._repository.get_assertion(account_id, assertion_id)
        if assertion.status == AssertionStatus.DELETED:
            raise ProfileError("已删除的画像断言不能回滚。")
        if assertion.status in {AssertionStatus.FROZEN, AssertionStatus.WITHDRAWN}:
            raise ProfileError("已冻结或已撤回的画像断言不能回滚，请先解冻。")

        history = self._repository.list_assertion_versions(account_id, assertion_id)
        target = next((v for v in history if v.version == to_version), None)
        if target is None:
            raise ProfileError("目标版本不存在。")

        previous_version = assertion.version
        self._repository.save_assertion_version(
            self._snapshot_assertion(assertion, reason, "user")
        )
        assertion.value_or_rule = target.value_or_rule
        assertion.applicable_scenes = list(target.applicable_scenes)
        assertion.status = AssertionStatus.ACTIVE
        assertion.version = previous_version + 1
        assertion.updated_at = _now()
        self._repository.save_assertion(assertion)

        invalidated_slices = self._invalidate_slices_for_assertion(
            account_id, assertion_id, f"assertion rolled back to version {to_version}: {reason}"
        )
        self._audit(
            account_id=account_id,
            action=AuditAction.PROFILE_ROLLBACK,
            result=AuditResult.SUCCESS,
            object_refs=[assertion_id],
            reason=reason,
            details={
                "previous_version": previous_version,
                "rolled_back_to_version": to_version,
                "new_content_hash": self._hash_assertion_value(
                    assertion.value_or_rule, assertion.applicable_scenes
                ),
                "invalidated_slice_ids": invalidated_slices,
            },
        )
        return assertion

    def get_assertion_history(
        self, account_id: str, assertion_id: str
    ) -> ProfileAssertionHistory:
        """Return the version history of one account-owned assertion.

        The history exposes who changed each version (user operation vs.
        candidate promotion) so the profile center can explain provenance
        without overwriting old values.
        """
        assertion = self._repository.get_assertion(account_id, assertion_id)
        return ProfileAssertionHistory(
            assertion_id=assertion.assertion_id,
            owner_account_id=assertion.owner_account_id,
            current_version=assertion.version,
            versions=self._repository.list_assertion_versions(
                account_id, assertion_id
            ),
        )

    def export_profile_data(self, account_id: str) -> ProfileExport:
        """Export a structured, account-scoped view of the user's profile.

        The export includes active and frozen assertions, version history and
        governance audit events. Deleted assertions are listed with metadata but
        their value is redacted so the export does not retain deleted body.
        """
        assertions = self._repository.list_assertions(account_id)
        export_assertions: list[ProfileExportAssertion] = []
        history: dict[str, ProfileAssertionHistory] = {}

        for assertion in assertions:
            versions = self._repository.list_assertion_versions(
                account_id, assertion.assertion_id
            )
            history[assertion.assertion_id] = ProfileAssertionHistory(
                assertion_id=assertion.assertion_id,
                owner_account_id=assertion.owner_account_id,
                current_version=assertion.version,
                versions=versions,
            )
            export_assertions.append(
                ProfileExportAssertion(
                    assertion_id=assertion.assertion_id,
                    canonical_dimension=assertion.canonical_dimension,
                    status=assertion.status,
                    value_or_rule=(
                        None
                        if assertion.status == AssertionStatus.DELETED
                        else assertion.value_or_rule
                    ),
                    applicable_scenes=list(assertion.applicable_scenes),
                    version=assertion.version,
                    content_hash=self._hash_assertion_value(
                        assertion.value_or_rule, assertion.applicable_scenes
                    ),
                    promoted_from_candidate_id=assertion.promoted_from_candidate_id,
                    last_used_at=assertion.last_used_at,
                    created_at=assertion.created_at,
                    updated_at=assertion.updated_at,
                    deleted_at=(
                        assertion.updated_at
                        if assertion.status == AssertionStatus.DELETED
                        else None
                    ),
                )
            )

        audit_event_refs: list[str] = []
        if self._observability_service is not None:
            for action in {
                AuditAction.PROFILE_CREATE,
                AuditAction.PROFILE_CANDIDATE_DECISION,
                AuditAction.PROFILE_FREEZE,
                AuditAction.PROFILE_WITHDRAW,
                AuditAction.PROFILE_UNFREEZE,
                AuditAction.PROFILE_MODIFY,
                AuditAction.PROFILE_DELETE,
                AuditAction.PROFILE_ROLLBACK,
                AuditAction.PROFILE_EXPORT,
            }:
                events = self._observability_service.list_audit_events(
                    account_id=account_id, action=action
                )
                audit_event_refs.extend(e.event_id for e in events)

        self._audit(
            account_id=account_id,
            action=AuditAction.PROFILE_EXPORT,
            result=AuditResult.SUCCESS,
            object_refs=[],
            reason="User requested profile export.",
            details={
                "assertion_count": len(export_assertions),
                "history_count": len(history),
            },
        )

        return ProfileExport(
            export_id=_new_id(),
            owner_account_id=account_id,
            exported_at=_now(),
            assertions=export_assertions,
            history=history,
            audit_event_refs=audit_event_refs,
        )

    def compile_memory_slice(
        self,
        account_id: str,
        *,
        purpose: str,
        run_id: str,
        project_id: str | None = None,
        sensitivity_classes: list[ProfileSensitivityClass] | None = None,
        ttl_seconds: int = 3600,
        authorization_version: str = "authz-1.0",
        key_epoch: str = "epoch-0",
    ) -> ProfileSlice:
        """Compile the minimal, authorized profile slice for a run.

        The slice is compiled according to task purpose, object scope,
        authorization snapshot, key epoch, expiration and sensitivity class.
        Active assertions are included only when all filters pass. The resulting
        slice is persisted and bound to the run so models and worker nodes can
        access only the slice, never the full profile vault.
        """
        now = _now()
        included: list[ProfileSliceItem] = []
        unused: list[UnusedSliceItem] = []
        excluded_candidate_ids: list[str] = []
        exclusion_reasons: dict[str, str] = {}
        rejected: list[RejectedSliceItem] = []

        for assertion in self._repository.list_assertions(account_id):
            if assertion.status != AssertionStatus.ACTIVE:
                continue

            if self._is_assertion_expired(assertion, now):
                unused.append(
                    UnusedSliceItem(
                        assertion_id=assertion.assertion_id,
                        dimension=assertion.canonical_dimension,
                        value_or_rule=assertion.value_or_rule,
                        exclusion_reason="Assertion has expired.",
                    )
                )
                continue

            if not self._is_sensitivity_allowed(
                assertion.sensitivity_class, sensitivity_classes
            ):
                unused.append(
                    UnusedSliceItem(
                        assertion_id=assertion.assertion_id,
                        dimension=assertion.canonical_dimension,
                        value_or_rule=assertion.value_or_rule,
                        exclusion_reason=(
                            f"Sensitivity '{assertion.sensitivity_class.value}' "
                            "is not permitted for this task."
                        ),
                    )
                )
                continue

            if purpose in assertion.applicable_scenes or not assertion.applicable_scenes:
                included.append(
                    ProfileSliceItem(
                        assertion_id=assertion.assertion_id,
                        dimension=assertion.canonical_dimension,
                        value_or_rule=assertion.value_or_rule,
                        inclusion_reason=f"Active assertion applicable to purpose '{purpose}'.",
                        sensitivity_class=assertion.sensitivity_class,
                        expires_at=assertion.expires_at,
                    )
                )
                # Record when the assertion was last used for an answer so the
                # profile center can show "最近使用" per record.
                assertion.last_used_at = now
                self._repository.save_assertion(assertion)
            else:
                unused.append(
                    UnusedSliceItem(
                        assertion_id=assertion.assertion_id,
                        dimension=assertion.canonical_dimension,
                        value_or_rule=assertion.value_or_rule,
                        exclusion_reason=(
                            f"Purpose '{purpose}' is not in applicable scenes "
                            f"{assertion.applicable_scenes}."
                        ),
                    )
                )

        for candidate in self._repository.list_candidates(account_id):
            if candidate.review_status not in {
                CandidateReviewStatus.ACCEPTED,
                CandidateReviewStatus.MODIFIED,
            }:
                excluded_candidate_ids.append(candidate.candidate_id)
                status_value = candidate.review_status.value
                reason = f"Candidate status '{status_value}' cannot be used as a stable fact."
                exclusion_reasons[candidate.candidate_id] = reason
                rejected.append(
                    RejectedSliceItem(
                        candidate_id=candidate.candidate_id,
                        dimension=candidate.canonical_dimension,
                        value_or_rule=candidate.value_or_rule,
                        rejection_reason=reason,
                    )
                )

        expires_at = now + timedelta(seconds=ttl_seconds)
        slice_ = ProfileSlice(
            slice_id=_new_id(),
            owner_account_id=account_id,
            run_id=run_id,
            purpose=purpose,
            project_id=project_id,
            included_items=included,
            unused_items=unused,
            excluded_candidate_ids=excluded_candidate_ids,
            exclusion_reasons=exclusion_reasons,
            rejected_items=rejected,
            authorization_snapshot=authorization_version,
            key_epoch=key_epoch,
            expires_at=expires_at,
            sensitivity_classes_allowed=list(sensitivity_classes or []),
            compiled_at=now,
        )
        return self._repository.save_slice(slice_)

    def compile_memory_slice_from_request(
        self, account_id: str, request: ProfileSliceCompileRequest
    ) -> ProfileSlice:
        """Compile a slice from a typed request."""
        return self.compile_memory_slice(
            account_id,
            purpose=request.purpose,
            run_id=request.run_id,
            project_id=request.project_id,
            sensitivity_classes=request.sensitivity_classes,
            ttl_seconds=request.ttl_seconds,
            authorization_version=request.authorization_version,
            key_epoch=request.key_epoch,
        )

    def get_slice(self, account_id: str, slice_id: str) -> ProfileSlice:
        """Return a compiled slice if the account owns it."""
        return self._repository.get_slice(account_id, slice_id)

    def list_slices_for_run(self, account_id: str, run_id: str) -> list[ProfileSlice]:
        """List slices bound to a run for the account."""
        return self._repository.list_slices_for_run(account_id, run_id)

    def check_slice_usable(self, slice_: ProfileSlice) -> None:
        """Fail closed if the slice is expired, revoked or cancelled."""
        now = _now()
        if slice_.status == SliceStatus.CANCELLED:
            raise ProfileError("切片已被取消，无法使用。")
        if slice_.status == SliceStatus.REVOKED:
            raise ProfileError("切片已被撤权，无法使用。")
        if slice_.status == SliceStatus.EXPIRED or (
            slice_.expires_at is not None and slice_.expires_at <= now
        ):
            raise ProfileError("切片已过期，无法使用。")

    def require_slice_for_run(self, slice_id: str, run_id: str) -> ProfileSlice:
        """Return a usable slice only when it is bound to the expected run.

        Models and worker nodes must call this method rather than browsing the
        profile vault directly. It fails closed on scope or state mismatch.
        """
        slice_ = self._repository.get_slice_by_id(slice_id)
        if slice_.run_id != run_id:
            raise ProfileError("切片与运行绑定不一致。")
        self.check_slice_usable(slice_)
        return slice_

    def invalidate_slice(
        self,
        account_id: str,
        slice_id: str,
        reason: str,
        status: SliceStatus = SliceStatus.REVOKED,
    ) -> ProfileSlice:
        """Invalidate a compiled slice.

        Called when authorization is withdrawn, the run is cancelled, or the
        underlying assertion is deleted. Invalidated slices cannot be read by
        models or worker nodes.
        """
        slice_ = self._repository.get_slice(account_id, slice_id)
        if slice_.status != SliceStatus.ACTIVE:
            raise ProfileError("切片已经失效。")
        slice_.status = status
        slice_.invalidated_at = _now()
        slice_.invalidation_reason = reason
        return self._repository.save_slice(slice_)
