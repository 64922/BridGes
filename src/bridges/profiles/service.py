"""Profile observation-candidate domain service."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from bridges.contracts.identity import SubjectContext
from bridges.contracts.observability import AuditAction, AuditResult
from bridges.contracts.profiles import (
    AUTO_WRITABLE_DIMENSIONS,
    PROFILE_DIMENSION_LABELS,
    USER_CONFIRMED_DIMENSIONS,
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
    ProfileBatchCandidateDecisionRequest,
    ProfileBatchCandidateResult,
    ProfileCandidate,
    ProfileDimension,
    ProfileExport,
    ProfileExportAssertion,
    ProfileNotification,
    ProfileNotificationKind,
    ProfileObservation,
    ProfileObservationCreateRequest,
    ProfilePermission,
    ProfilePermissionUpdateRequest,
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
from bridges.profiles.extraction import (
    _CATEGORY_KEYWORDS,
    MemoryIntent,
    MemoryIntentExtractor,
    MemoryIntentKind,
    _clean_value,
)
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

#: 对话模式 → 可参与任务切片编译的画像维度（Issue 27，AC-08）。
#: 学习模式使用阶段目标、知识状态与学习相关兴趣调整教学；日常模式只
#: 使用必要偏好、表达习惯与基本情况，不强制教学结构。
_CHAT_MODE_DIMENSIONS: dict[str, frozenset[ProfileDimension]] = {
    "companion": frozenset(
        {
            ProfileDimension.INTEREST_PREFERENCE,
            ProfileDimension.EXPRESSION_HABIT,
            ProfileDimension.BASIC_INFORMATION,
        }
    ),
    "study": frozenset(
        {
            ProfileDimension.STAGE_GOAL,
            ProfileDimension.KNOWLEDGE_STATE,
            ProfileDimension.INTEREST_PREFERENCE,
        }
    ),
    # Issue 33: 提醒措辞适配只使用表达习惯、基本偏好与偏好（最小切片，
    # 与日常模式同白名单——提醒正文只需要称呼/语气等表达类信息）。
    "reminder": frozenset(
        {
            ProfileDimension.INTEREST_PREFERENCE,
            ProfileDimension.EXPRESSION_HABIT,
            ProfileDimension.BASIC_INFORMATION,
        }
    ),
}

#: 单维度切片条数上限与整卷切片总量上限（最小必要，防整卷画像注入）。
_MAX_SLICE_ITEMS_PER_DIMENSION = 2
_MAX_SLICE_ITEMS_TOTAL = 6
#: 切片中单条值的摘要长度上限（披露与注入共用的截断长度）。
_SLICE_VALUE_SUMMARY_MAX = 80

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


def _slice_value_summary(value: str, max_chars: int = _SLICE_VALUE_SUMMARY_MAX) -> str:
    """切片值的摘要：超长截断，披露与注入共用同一截断长度。"""
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 1] + "…"


def _dimension_label(dimension: str) -> str:
    """画像维度中文标签；未知维度直接回退原始值，不抛错。"""
    try:
        return PROFILE_DIMENSION_LABELS[ProfileDimension(dimension)]
    except ValueError:
        return dimension


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
        extractor: MemoryIntentExtractor | None = None,
    ) -> None:
        self._repository = repository
        self._scope_enforcer = scope_enforcer or ScopeEnforcer()
        self._invalidation_service = invalidation_service
        self._observability_service = observability_service
        #: 确定性记忆意图提取器（Issue 26）；可注入假实现用于测试。
        self._extractor = extractor or MemoryIntentExtractor()

    def _subject(self, account_id: str) -> SubjectContext:
        return ScopeEnforcer.service_subject(account_id, "profiles")

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
        candidate = self._repository.save_candidate(candidate)
        # Issue 26：所有候选提出（含 API 播种与聊天管线）统一写入审计，
        # 账户隔离由调用方账户 ID 保证。
        self._audit(
            account_id=account_id,
            action=AuditAction.PROFILE_CANDIDATE_PROPOSE,
            result=AuditResult.SUCCESS,
            object_refs=[candidate.candidate_id],
            reason="画像候选提出",
            details={
                "dimension": canonical_dimension,
                "sensitivity_class": sensitivity_class.value,
                "supporting_observation_count": len(supporting_observation_ids),
            },
        )
        return candidate

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

    def compile_chat_slice(
        self,
        account_id: str,
        *,
        mode: str,
        run_id: str,
        project_id: str | None = None,
        sensitivity_classes: list[ProfileSensitivityClass] | None = None,
    ) -> ProfileSlice:
        """为一次对话轮次编译最小必要画像切片（Issue 27）。

        在通用编译（场景/敏感度/有效期/状态过滤、未确认候选排除）之上
        叠加两层任务级筛选：
        1. 按对话模式限定可参与维度——学习模式只取目标/知识状态/学习
           兴趣，日常模式只取偏好/表达习惯/基本情况，绝不把教学结构
           强加给日常陪伴（模式即本轮任务类型，维度白名单是任务相关性
           的可解释筛选代理，不依赖脆弱的文本关键词匹配）；
        2. 最小化上限——单维度最多 2 条、整卷最多 6 条，超出按最近使用
           优先保留，保证只注入当前任务所需的最小记录，而不是整份画像。
        敏感度默认白名单为 public/preference/learning——SENSITIVE 与
        PROHIBITED 记录默认不进聊天切片（可显式放宽）；授权范围只接受
        ``general`` 或当前模式值；其余范围一律排除。``excluded_count``
        统计被任务级过滤排除的记录数，供上下文说明披露。
        """
        # 聊天切片默认排除 SENSITIVE：敏感记录不随日常/学习对话进入模型
        # 上下文，除非调用方显式指定白名单。
        if sensitivity_classes is None:
            sensitivity_classes = [
                ProfileSensitivityClass.PUBLIC,
                ProfileSensitivityClass.PREFERENCE,
                ProfileSensitivityClass.LEARNING,
            ]
        allowed_dimensions = _CHAT_MODE_DIMENSIONS.get(mode, frozenset())
        now = _now()
        included: list[ProfileSliceItem] = []
        unused: list[UnusedSliceItem] = []
        excluded_count = 0
        per_dimension: dict[str, int] = {}

        def _record_excluded(
            assertion: ProfileAssertion, reason: str
        ) -> None:
            nonlocal excluded_count
            excluded_count += 1
            unused.append(
                UnusedSliceItem(
                    assertion_id=assertion.assertion_id,
                    dimension=assertion.canonical_dimension,
                    value_or_rule=assertion.value_or_rule,
                    exclusion_reason=reason,
                )
            )

        assertions = sorted(
            self._repository.list_assertions(account_id),
            key=lambda a: (
                a.last_used_at is not None,
                a.last_used_at or datetime.min.replace(tzinfo=UTC),
            ),
            reverse=True,
        )
        for assertion in assertions:
            dimension = assertion.canonical_dimension
            if assertion.status != AssertionStatus.ACTIVE:
                _record_excluded(
                    assertion,
                    f"记录状态为 {assertion.status.value}，禁止用于本轮。",
                )
                continue
            if dimension not in allowed_dimensions:
                _record_excluded(
                    assertion,
                    f"类别「{_dimension_label(dimension)}」"
                    f"不属于{mode}模式可用范围。",
                )
                continue
            if self._is_assertion_expired(assertion, now):
                _record_excluded(assertion, "记录已过期。")
                continue
            if not self._is_sensitivity_allowed(
                assertion.sensitivity_class, sensitivity_classes
            ):
                _record_excluded(
                    assertion,
                    f"敏感度「{assertion.sensitivity_class.value}」不允许进入本轮。",
                )
                continue
            if assertion.authorization_scope not in {"", "general", mode}:
                _record_excluded(
                    assertion,
                    f"授权范围「{assertion.authorization_scope}」与{mode}模式不匹配。",
                )
                continue
            if (
                assertion.applicable_scenes
                and mode not in assertion.applicable_scenes
            ):
                _record_excluded(
                    assertion,
                    f"适用场景 {assertion.applicable_scenes} 不包含当前{mode}模式。",
                )
                continue
            if per_dimension.get(dimension, 0) >= _MAX_SLICE_ITEMS_PER_DIMENSION:
                _record_excluded(assertion, "该类别本轮切片已达单类别上限。")
                continue
            if len(included) >= _MAX_SLICE_ITEMS_TOTAL:
                _record_excluded(assertion, "本轮切片已达总量上限。")
                continue
            per_dimension[dimension] = per_dimension.get(dimension, 0) + 1
            included.append(
                ProfileSliceItem(
                    assertion_id=assertion.assertion_id,
                    dimension=dimension,
                    value_or_rule=_slice_value_summary(assertion.value_or_rule),
                    inclusion_reason=(
                        f"与{mode}模式相关且已授权的最小切片（类别："
                        f"{_dimension_label(dimension)}）。"
                    ),
                    sensitivity_class=assertion.sensitivity_class,
                    expires_at=assertion.expires_at,
                )
            )
            assertion.last_used_at = now
            self._repository.save_assertion(assertion)

        rejected: list[RejectedSliceItem] = []
        excluded_candidate_ids: list[str] = []
        exclusion_reasons: dict[str, str] = {}
        for candidate in self._repository.list_candidates(account_id):
            if candidate.review_status not in {
                CandidateReviewStatus.ACCEPTED,
                CandidateReviewStatus.MODIFIED,
            }:
                excluded_candidate_ids.append(candidate.candidate_id)
                reason = (
                    f"候选状态 '{candidate.review_status.value}' 未经确认，"
                    "不得作为稳定事实使用。"
                )
                exclusion_reasons[candidate.candidate_id] = reason
                rejected.append(
                    RejectedSliceItem(
                        candidate_id=candidate.candidate_id,
                        dimension=candidate.canonical_dimension,
                        value_or_rule=candidate.value_or_rule,
                        rejection_reason=reason,
                    )
                )

        slice_ = ProfileSlice(
            slice_id=_new_id(),
            owner_account_id=account_id,
            run_id=run_id,
            purpose=mode,
            project_id=project_id,
            included_items=included,
            unused_items=unused,
            excluded_candidate_ids=excluded_candidate_ids,
            exclusion_reasons=exclusion_reasons,
            rejected_items=rejected,
            authorization_snapshot="authz-1.0",
            key_epoch="epoch-0",
            expires_at=now + timedelta(seconds=3600),
            sensitivity_classes_allowed=list(sensitivity_classes or []),
            compiled_at=now,
        )
        return self._repository.save_slice(slice_)

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

    # ------------------------------------------------------------------
    # Issue 26：分级许可、自动写入、候选箱与通知
    # ------------------------------------------------------------------

    #: 许可适用的场景（与对话模式对应；ADR-0022）。
    _PERMISSION_SCENES = frozenset({"companion", "study"})

    def list_permissions(self, account_id: str) -> list[ProfilePermission]:
        """返回该账户的低风险自动更新许可（默认全关）。"""
        return self._repository.list_permissions(account_id)

    def _permission_enabled(
        self, account_id: str, dimension: ProfileDimension, scene: str
    ) -> bool:
        """低风险自动写入的许可门：默认关闭，仅用户显式开启才放行。

        授权范围只能由用户设置——本方法只读取用户写入的许可行，不
        从沉默、语气或历史行为推断任何授权。
        """
        for permission in self._repository.list_permissions(account_id):
            if (
                permission.dimension == dimension
                and permission.scene == scene
                and permission.enabled
            ):
                return True
        return False

    def set_permission(
        self, account_id: str, request: ProfilePermissionUpdateRequest
    ) -> ProfilePermission:
        """由用户开启/关闭一条低风险自动更新许可。

        仅允许 ``AUTO_WRITABLE_DIMENSIONS`` 内类别，场景仅限日常陪伴与
        学习模式；模型或其他后台代码没有此入口，授权不可能被推断代开。
        """
        if request.dimension not in AUTO_WRITABLE_DIMENSIONS:
            raise ProfileError("该类别不支持自动更新许可，只能手动或候选确认。")
        if request.scene not in self._PERMISSION_SCENES:
            raise ProfileError("许可场景仅支持日常陪伴与学习模式。")
        now = _now()
        permission = self._repository.set_permission(
            account_id,
            request.dimension.value,
            request.scene,
            request.enabled,
            now,
        )
        self._audit(
            account_id=account_id,
            action=(
                AuditAction.PROFILE_PERMISSION_ENABLE
                if request.enabled
                else AuditAction.PROFILE_PERMISSION_DISABLE
            ),
            result=AuditResult.SUCCESS,
            object_refs=[],
            reason=f"用户{('开启' if request.enabled else '关闭')}自动更新许可",
            details={"dimension": request.dimension.value, "scene": request.scene},
        )
        return permission

    def list_notifications(self, account_id: str) -> list[ProfileNotification]:
        """返回该账户的通知，最新在前。"""
        return self._repository.list_notifications(account_id)

    def mark_notification_read(
        self, account_id: str, notification_id: str
    ) -> ProfileNotification:
        """标记一条通知为已读；跨账户访问返回不存在。"""
        return self._repository.mark_notification_read(
            account_id, notification_id, _now()
        )

    def recall_auto_write(
        self, account_id: str, notification_id: str
    ) -> ProfileNotification:
        """一键撤回一条自动写入记录（幂等）。

        撤回把对应断言转为 WITHDRAWN（停止后续回答使用，保留审计历史），
        并以丢弃观察阻止未来自动写入同一事实；已撤回的通知再次撤回
        直接返回，不重复写入。
        """
        notification = self._repository.get_notification(account_id, notification_id)
        if not notification.recallable or notification.assertion_id is None:
            raise ProfileError("该通知不支持一键撤回。")
        if notification.recalled_at is not None:
            return notification

        assertion = self._repository.get_assertion(
            account_id, notification.assertion_id
        )
        if assertion.status == AssertionStatus.ACTIVE:
            self.withdraw_assertion(
                account_id,
                assertion.assertion_id,
                "用户一键撤回自动写入记录",
            )
        # 阻止同一事实被再次自动写入：丢弃观察以稳定内容键阻断后续去重。
        if (
            notification.dimension is not None
            and notification.scene is not None
        ):
            blocker = self._blocker_observation(
                account_id=account_id,
                dimension=notification.dimension,
                value=assertion.value_or_rule,
                scene=notification.scene,
                purpose="用户一键撤回自动写入记录",
            )
            self._repository.save_observation(blocker)

        notification.recalled_at = _now()
        self._repository.save_notification(notification)
        self._audit(
            account_id=account_id,
            action=AuditAction.PROFILE_AUTO_WRITE_RECALL,
            result=AuditResult.SUCCESS,
            object_refs=[assertion.assertion_id, notification_id],
            reason="用户一键撤回自动写入记录",
            details={
                "dimension": notification.dimension.value
                if notification.dimension is not None
                else None,
                "scene": notification.scene,
                "assertion_id": assertion.assertion_id,
            },
        )
        return notification

    # ------------------------------------------------------------------
    # 对话消息 → 记忆意图处理（确定性规则，Issue 26）
    # ------------------------------------------------------------------

    def process_conversation_message(
        self,
        account_id: str,
        *,
        message_id: str,
        conversation_id: str,
        content: str,
        mode: str,
    ) -> list[ProfileNotification]:
        """处理一条用户消息：把明确记忆意图映射为可见类别/范围/证据。

        明确"记住/不要记住/只在本对话使用"按规则路由；低风险观察仅在
        对应类别与场景许可开启时自动写入；敏感内容只进候选箱；单次情绪
        只作为会话情境提示。任何失败都不影响调用方（聊天）主流程。
        """
        scene = mode
        intents = self._extractor.extract(content, scene=scene)
        notifications: list[ProfileNotification] = []
        explicit_present = any(
            intent.kind
            in {MemoryIntentKind.REMEMBER, MemoryIntentKind.FORGET, MemoryIntentKind.SESSION_ONLY}
            for intent in intents
        )
        # "只在本对话使用"优先于"记住"：内容留在会话内，不写入长期画像。
        session_only_present = any(
            intent.kind == MemoryIntentKind.SESSION_ONLY for intent in intents
        )
        for intent in intents:
            if intent.kind == MemoryIntentKind.REMEMBER:
                if session_only_present:
                    continue
                notification = self._handle_remember(
                    account_id, message_id, conversation_id, intent
                )
            elif intent.kind == MemoryIntentKind.FORGET:
                notification = self._handle_forget(
                    account_id, message_id, conversation_id, intent
                )
            elif intent.kind == MemoryIntentKind.SESSION_ONLY:
                notification = self._handle_session_only(
                    account_id, message_id, conversation_id, intent
                )
            elif intent.kind == MemoryIntentKind.AUTO_WRITE:
                if explicit_present:
                    continue
                notification = self._handle_auto_write(
                    account_id, message_id, conversation_id, intent
                )
            elif intent.kind == MemoryIntentKind.TRANSIENT:
                if explicit_present or notifications:
                    continue
                notification = self._transient_notification(
                    account_id, message_id, conversation_id, intent
                )
            else:
                notification = None
            if notification is not None:
                notifications.append(notification)

        if intents:
            self._audit(
                account_id=account_id,
                action=AuditAction.PROFILE_INTENT,
                result=AuditResult.SUCCESS,
                object_refs=[message_id, conversation_id],
                reason="对话记忆意图处理",
                details={
                    "intents": [
                        {
                            "kind": intent.kind.value,
                            "dimension": (
                                intent.dimension.value if intent.dimension else None
                            ),
                            "value": intent.value,
                            "scene": intent.scene,
                        }
                        for intent in intents
                    ],
                    "notification_count": len(notifications),
                },
            )
        return notifications

    def _dimension_label(self, dimension: ProfileDimension) -> str:
        return PROFILE_DIMENSION_LABELS.get(dimension, dimension.value)

    def _dedup_key(
        self, dimension: ProfileDimension, value: str, scene: str
    ) -> str:
        """稳定内容键：同一类别、同一值、同一场景的事实去重与阻断一致。"""
        payload = "|".join([dimension.value, value, scene])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _blocker_observation(
        self,
        *,
        account_id: str,
        dimension: ProfileDimension,
        value: str,
        scene: str,
        purpose: str,
    ) -> ProfileObservation:
        """构造一条 DISCARDED 观察：以稳定内容键阻断未来的自动写入。"""
        return ProfileObservation(
            observation_id=_new_id(),
            owner_account_id=account_id,
            project_id=None,
            source_type=ProfileSourceType.SYSTEM_INFERENCE,
            source_ref="memory-intent",
            source_span_or_event="blocker",
            scene=scene,
            purpose=purpose,
            observed_content=value,
            signal_kind=ProfileSignalKind.OTHER,
            extractor_and_version="memory-intent-1.0",
            sensitivity_class=ProfileSensitivityClass.PREFERENCE,
            retention_policy="user-managed",
            authorization_version="authz-1.0",
            content_hash=self._dedup_key(dimension, value, scene),
            status=ObservationStatus.DISCARDED,
            created_at=_now(),
            updated_at=_now(),
        )

    def _observation_for(
        self,
        account_id: str,
        *,
        conversation_id: str,
        message_id: str,
        scene: str,
        value: str,
        evidence: str,
        dimension: ProfileDimension,
        signal_kind: ProfileSignalKind,
        sensitivity_class: ProfileSensitivityClass,
        source_type: ProfileSourceType,
        purpose: str,
    ) -> ProfileObservation:
        """构造一条来自对话的观察，内容键稳定用于去重与阻断。

        ``observed_content`` 保存用户原句（证据原文），抽取值存于候选/
        断言的 value_or_rule；候选卡"来源消息"因此显示真实原句而非抽取值。
        """
        return ProfileObservation(
            observation_id=_new_id(),
            owner_account_id=account_id,
            project_id=None,
            source_type=source_type,
            source_ref=f"{conversation_id}:{message_id}",
            source_span_or_event=message_id,
            scene=scene,
            purpose=purpose,
            observed_content=evidence,
            signal_kind=signal_kind,
            extractor_and_version="memory-intent-1.0",
            sensitivity_class=sensitivity_class,
            retention_policy="user-managed",
            authorization_version="authz-1.0",
            content_hash=self._dedup_key(dimension, value, scene),
            status=ObservationStatus.ACTIVE,
            created_at=_now(),
            updated_at=_now(),
        )

    def _reuse_or_save_observation(
        self, account_id: str, observation: ProfileObservation
    ) -> ProfileObservation:
        """保存观察前按（内容键, 来源）复用既有观察，重试轮不累积重复行。

        聊天重试会重跑同一条用户消息的意图管线；合并分支以稳定内容键与
        来源定位已持久化的观察并直接复用，观察与证据链不因重试膨胀。
        """
        existing = self._repository.find_observation_by_source(
            account_id, observation.content_hash, observation.source_ref
        )
        if existing is not None:
            return existing
        return self._repository.save_observation(observation)

    def _notification(
        self,
        account_id: str,
        *,
        kind: ProfileNotificationKind,
        title: str,
        message: str,
        conversation_id: str,
        message_id: str,
        source_text: str,
        dimension: ProfileDimension | None,
        scene: str,
        assertion_id: str | None = None,
        candidate_id: str | None = None,
        recallable: bool = False,
    ) -> ProfileNotification:
        return self._repository.save_notification(
            ProfileNotification(
                notification_id=_new_id(),
                owner_account_id=account_id,
                kind=kind,
                title=title,
                message=message,
                source_ref=f"{conversation_id}:{message_id}",
                source_text=source_text,
                dimension=dimension,
                scene=scene,
                assertion_id=assertion_id,
                candidate_id=candidate_id,
                recallable=recallable,
                created_at=_now(),
            )
        )

    def _handle_remember(
        self,
        account_id: str,
        message_id: str,
        conversation_id: str,
        intent: MemoryIntent,
    ) -> ProfileNotification | None:
        """明确"记住"：敏感类别只进候选箱，其余写入证据化记录。"""
        if intent.dimension is None:
            return self._notification(
                account_id,
                kind=ProfileNotificationKind.INTENT_RECORDED,
                title="已收到你的记忆请求",
                message="请说明要记住的类别（如目标、偏好、习惯、名字），我会按你指定的类别记录。",
                conversation_id=conversation_id,
                message_id=message_id,
                source_text=intent.evidence,
                dimension=None,
                scene=intent.scene,
            )

        dimension = intent.dimension
        value = intent.value or ""
        if not value:
            return None

        if dimension in USER_CONFIRMED_DIMENSIONS:
            return self._propose_sensitive_candidate(
                account_id, message_id, conversation_id, intent
            )

        existing = self._repository.find_assertion_by_value(
            account_id, dimension.value, value
        )
        if existing is not None:
            if existing.status in {AssertionStatus.ACTIVE, AssertionStatus.FROZEN}:
                # 同一事实已存在：合并证据观察，不重复写入、不重复通知。
                observation = self._observation_for(
                    account_id,
                    conversation_id=conversation_id,
                    message_id=message_id,
                    scene=intent.scene,
                    value=value,
                    dimension=dimension,
                    signal_kind=_DIMENSION_TO_SIGNAL_KIND[dimension],
                    sensitivity_class=ProfileSensitivityClass.PREFERENCE,
                    source_type=ProfileSourceType.EXPLICIT_STATEMENT,
                    purpose="用户明确要求记住（合并既有记录证据）",
                    evidence=intent.evidence,
                )
                observation = self._reuse_or_save_observation(account_id, observation)
                if (
                    observation.observation_id
                    not in existing.supporting_observation_ids
                ):
                    existing.supporting_observation_ids.append(
                        observation.observation_id
                    )
                    self._repository.save_assertion(existing)
                self._audit(
                    account_id=account_id,
                    action=AuditAction.PROFILE_CREATE,
                    result=AuditResult.SUCCESS,
                    object_refs=[existing.assertion_id],
                    reason="用户明确要求记住（证据合并到既有记录）",
                    details={
                        "dimension": dimension.value,
                        "merged": True,
                    },
                )
                return None
            # WITHDRAWN/DELETED 的既有同值记录：用户曾撤回，不自动复活。
            return None

        observation = self._observation_for(
            account_id,
            conversation_id=conversation_id,
            message_id=message_id,
            scene=intent.scene,
            value=value,
            dimension=dimension,
            signal_kind=_DIMENSION_TO_SIGNAL_KIND[dimension],
            sensitivity_class=ProfileSensitivityClass.PREFERENCE,
            source_type=ProfileSourceType.EXPLICIT_STATEMENT,
            purpose="用户明确要求记住",
        evidence=intent.evidence,
        )
        self._repository.save_observation(observation)
        candidate = self._repository.save_candidate(
            ProfileCandidate(
                candidate_id=_new_id(),
                owner_account_id=account_id,
                canonical_dimension=dimension.value,
                value_or_rule=value,
                applicable_scenes=[intent.scene] if intent.scene else [],
                supporting_observation_ids=[observation.observation_id],
                evidence_summary=f"用户在对话中明确要求记住（来源消息 {message_id[:8]}）",
                authorization_scope="general",
                promotion_policy_version="promotion-1.0",
                review_status=CandidateReviewStatus.PROPOSED,
                stability_state=CandidateStabilityState.CANDIDATE,
                sensitivity_class=ProfileSensitivityClass.PREFERENCE,
                proposed_at=_now(),
                updated_at=_now(),
            )
        )
        self.decide_candidate(
            account_id,
            candidate.candidate_id,
            CandidateDecision(
                decision=DecisionType.ACCEPT,
                reason="用户明确要求记住（对话中确认）",
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
            raise ProfileError("画像记录写入失败，请稍后重试。")
        return self._notification(
            account_id,
            kind=ProfileNotificationKind.INTENT_RECORDED,
            title="已记住",
            message=f"已把你的「{self._dimension_label(dimension)}」记录为：{value}（来源：本条消息）。",
            conversation_id=conversation_id,
            message_id=message_id,
            source_text=intent.evidence,
            dimension=dimension,
            scene=intent.scene,
            assertion_id=assertion.assertion_id,
        )

    def _propose_sensitive_candidate(
        self,
        account_id: str,
        message_id: str,
        conversation_id: str,
        intent: MemoryIntent,
    ) -> ProfileNotification | None:
        """敏感类别（情绪趋势/重要经历/正在面对的问题）只进候选箱。

        候选未确认前绝不进入跨会话画像切片；重复观察合并进同一候选的
        证据，不重复生成候选或通知。
        """
        dimension = intent.dimension
        assert dimension is not None
        value = intent.value or ""
        for candidate in self._repository.list_candidates(account_id):
            if (
                candidate.review_status == CandidateReviewStatus.PROPOSED
                and candidate.canonical_dimension == dimension.value
                and candidate.value_or_rule == value
            ):
                observation = self._observation_for(
                    account_id,
                    conversation_id=conversation_id,
                    message_id=message_id,
                    scene=intent.scene,
                    value=value,
                    dimension=dimension,
                    signal_kind=ProfileSignalKind.OTHER,
                    sensitivity_class=ProfileSensitivityClass.SENSITIVE,
                    source_type=ProfileSourceType.EXPLICIT_STATEMENT,
                    purpose="用户明确要求记住（敏感类别，合并候选证据）",
                    evidence=intent.evidence,
                )
                observation = self._reuse_or_save_observation(account_id, observation)
                if observation.observation_id not in candidate.supporting_observation_ids:
                    candidate.supporting_observation_ids.append(
                        observation.observation_id
                    )
                    candidate.evidence_summary = (
                        f"敏感候选，待确认：{len(candidate.supporting_observation_ids)} 条来源观察"
                    )
                    candidate.updated_at = _now()
                    self._repository.save_candidate(candidate)
                return None

        observation = self._observation_for(
            account_id,
            conversation_id=conversation_id,
            message_id=message_id,
            scene=intent.scene,
            value=value,
            dimension=dimension,
            signal_kind=ProfileSignalKind.OTHER,
            sensitivity_class=ProfileSensitivityClass.SENSITIVE,
            source_type=ProfileSourceType.EXPLICIT_STATEMENT,
            purpose="用户明确要求记住（敏感类别，仅候选）",
        evidence=intent.evidence,
        )
        self._repository.save_observation(observation)
        candidate = self.propose_candidate(
            account_id,
            canonical_dimension=dimension.value,
            value_or_rule=value,
            applicable_scenes=[intent.scene] if intent.scene else [],
            supporting_observation_ids=[observation.observation_id],
            evidence_summary="敏感候选：情绪趋势/重要经历/当前问题需你确认后才能跨会话使用",
            sensitivity_class=ProfileSensitivityClass.SENSITIVE,
        )
        # 候选提出审计统一在 propose_candidate 记录，此处不重复。
        return self._notification(
            account_id,
            kind=ProfileNotificationKind.CANDIDATE_PROPOSED,
            title="已生成候选画像",
            message=(
                f"关于「{self._dimension_label(dimension)}」的候选已生成："
                f"{value}。这是敏感信息，需你在画像中心确认后才会跨会话使用。"
            ),
            conversation_id=conversation_id,
            message_id=message_id,
            source_text=intent.evidence,
            dimension=dimension,
            scene=intent.scene,
            candidate_id=candidate.candidate_id,
        )

    def _handle_forget(
        self,
        account_id: str,
        message_id: str,
        conversation_id: str,
        intent: MemoryIntent,
    ) -> ProfileNotification | None:
        """明确"不要记住"：丢弃观察阻断未来写入，并撤回同值既有记录。

        类别词出现在被忘对象文本中时（如"忘掉我上次说的蓝色偏好"），
        按可见关键词映射拆出类别与事实值；无法映射时不猜测，只提示。
        """
        withdrawn_assertions: list[str] = []
        dimension = intent.dimension
        value = intent.value
        if value and dimension is None:
            split = self._split_dimension_value(value)
            if split is not None:
                dimension, value = split

        if dimension is not None and value:
            blocker = self._blocker_observation(
                account_id=account_id,
                dimension=dimension,
                value=value,
                scene=intent.scene,
                purpose="用户明确要求不要记住",
            )
            self._repository.save_observation(blocker)
            existing = self._repository.find_assertion_by_value(
                account_id, dimension.value, value
            )
            if existing is not None and existing.status == AssertionStatus.ACTIVE:
                self.withdraw_assertion(
                    account_id,
                    existing.assertion_id,
                    "用户明确要求不要记住",
                )
                withdrawn_assertions.append(existing.assertion_id)

        message = "这条内容不会写入长期画像。"
        if withdrawn_assertions:
            message += " 已撤回既有相同记录，保留可审计历史。"
        return self._notification(
            account_id,
            kind=ProfileNotificationKind.INTENT_RECORDED,
            title="已按你的要求不再记录",
            message=message,
            conversation_id=conversation_id,
            message_id=message_id,
            source_text=intent.evidence,
            dimension=dimension,
            scene=intent.scene,
        )

    def _split_dimension_value(
        self, value: str
    ) -> tuple[ProfileDimension, str] | None:
        """按可见类别关键词把被提及内容拆成（类别, 事实值）。

        与 :meth:`_handle_forget` 共用同一拆分，保证"只在本对话使用"与
        "不要记住"产生的阻断键和自动写入提取键一致，阻断才真正生效。
        """
        for keyword, candidate_dimension in sorted(
            _CATEGORY_KEYWORDS.items(), key=lambda kv: len(kv[0]), reverse=True
        ):
            if keyword in value:
                remainder = _clean_value(value.split(keyword, 1)[1].lstrip("是：:，, "))
                if remainder:
                    return candidate_dimension, remainder
        return None

    def _handle_session_only(
        self,
        account_id: str,
        message_id: str,
        conversation_id: str,
        intent: MemoryIntent,
    ) -> ProfileNotification | None:
        """明确"只在本对话使用"：内容留在会话内，不进入长期画像。

        阻断键与自动写入提取键对齐（类别词拆分），确保后续同内容的
        自动写入被真正阻止，而不是落在错位的键上。
        """
        if intent.value:
            dimension = intent.dimension
            value = intent.value
            if dimension is None:
                split = self._split_dimension_value(value)
                if split is not None:
                    dimension, value = split
            if dimension is not None:
                blocker = self._blocker_observation(
                    account_id=account_id,
                    dimension=dimension,
                    value=value,
                    scene=intent.scene,
                    purpose="用户明确要求只在本对话使用",
                )
                self._repository.save_observation(blocker)
        return self._notification(
            account_id,
            kind=ProfileNotificationKind.INTENT_RECORDED,
            title="仅在本对话使用",
            message="这段内容只作为本次对话的情境，不会写入长期画像。",
            conversation_id=conversation_id,
            message_id=message_id,
            source_text=intent.evidence,
            dimension=intent.dimension,
            scene=intent.scene,
        )

    def _handle_auto_write(
        self,
        account_id: str,
        message_id: str,
        conversation_id: str,
        intent: MemoryIntent,
    ) -> ProfileNotification | None:
        """许可内的低风险自动写入：许可门、冻结门与去重门全过才写入。

        未获许可、类别被冻结或内容被明确阻断时静默跳过——默认关闭即
        默认不写入，绝不代替用户决定。
        """
        dimension = intent.dimension
        assert dimension is not None
        value = intent.value or ""
        if not value:
            return None
        if not self._permission_enabled(account_id, dimension, intent.scene):
            return None
        if self._repository.find_discarded_observation(
            account_id, self._dedup_key(dimension, value, intent.scene)
        ) is not None:
            return None
        for frozen in self._repository.list_assertions(account_id):
            if (
                frozen.canonical_dimension == dimension.value
                and frozen.status == AssertionStatus.FROZEN
            ):
                # 冻结类别不接收自动写入（Issue 25 契约在 Issue 26 生效）。
                return None

        existing = self._repository.find_assertion_by_value(
            account_id, dimension.value, value
        )
        if existing is not None:
            # 同一事实已存在（活跃/冻结/已撤回）：不重复写入，合并证据。
            observation = self._observation_for(
                account_id,
                conversation_id=conversation_id,
                message_id=message_id,
                scene=intent.scene,
                value=value,
                dimension=dimension,
                signal_kind=_DIMENSION_TO_SIGNAL_KIND[dimension],
                sensitivity_class=ProfileSensitivityClass.PREFERENCE,
                source_type=ProfileSourceType.TASK_BEHAVIOR,
                purpose="许可内自动观察（证据合并）",
            evidence=intent.evidence,
            )
            observation = self._reuse_or_save_observation(account_id, observation)
            if existing.status == AssertionStatus.ACTIVE and (
                observation.observation_id
                not in existing.supporting_observation_ids
            ):
                existing.supporting_observation_ids.append(observation.observation_id)
                self._repository.save_assertion(existing)
            self._audit(
                account_id=account_id,
                action=AuditAction.PROFILE_AUTO_WRITE,
                result=AuditResult.SUCCESS,
                object_refs=[existing.assertion_id],
                reason="许可内自动写入（证据合并，去重）",
                details={
                    "dimension": dimension.value,
                    "scene": intent.scene,
                    "merged": True,
                    "content_hash": self._dedup_key(dimension, value, intent.scene),
                },
            )
            return None

        observation = self._observation_for(
            account_id,
            conversation_id=conversation_id,
            message_id=message_id,
            scene=intent.scene,
            value=value,
            dimension=dimension,
            signal_kind=_DIMENSION_TO_SIGNAL_KIND[dimension],
            sensitivity_class=ProfileSensitivityClass.PREFERENCE,
            source_type=ProfileSourceType.TASK_BEHAVIOR,
            purpose="许可内自动观察",
        evidence=intent.evidence,
        )
        self._repository.save_observation(observation)
        candidate = self._repository.save_candidate(
            ProfileCandidate(
                candidate_id=_new_id(),
                owner_account_id=account_id,
                canonical_dimension=dimension.value,
                value_or_rule=value,
                applicable_scenes=[intent.scene] if intent.scene else [],
                supporting_observation_ids=[observation.observation_id],
                evidence_summary=f"许可内自动观察（场景 {intent.scene}）",
                authorization_scope="general",
                promotion_policy_version="promotion-1.0",
                review_status=CandidateReviewStatus.PROPOSED,
                stability_state=CandidateStabilityState.CANDIDATE,
                sensitivity_class=ProfileSensitivityClass.PREFERENCE,
                proposed_at=_now(),
                updated_at=_now(),
            )
        )
        self.decide_candidate(
            account_id,
            candidate.candidate_id,
            CandidateDecision(
                decision=DecisionType.ACCEPT,
                reason="低风险自动写入许可已开启",
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
            raise ProfileError("画像自动写入失败，请稍后重试。")
        self._audit(
            account_id=account_id,
            action=AuditAction.PROFILE_AUTO_WRITE,
            result=AuditResult.SUCCESS,
            object_refs=[assertion.assertion_id],
            reason="许可内低风险自动写入",
            details={
                "dimension": dimension.value,
                "scene": intent.scene,
                "assertion_id": assertion.assertion_id,
                "content_hash": self._dedup_key(dimension, value, intent.scene),
            },
        )
        return self._notification(
            account_id,
            kind=ProfileNotificationKind.AUTO_WRITE,
            title="已自动记录",
            message=(
                f"已自动记录你的「{self._dimension_label(dimension)}」："
                f"{value}（来源：本条消息）。如不需要，可一键撤回。"
            ),
            conversation_id=conversation_id,
            message_id=message_id,
            source_text=intent.evidence,
            dimension=dimension,
            scene=intent.scene,
            assertion_id=assertion.assertion_id,
            recallable=True,
        )

    def _transient_notification(
        self,
        account_id: str,
        message_id: str,
        conversation_id: str,
        intent: MemoryIntent,
    ) -> ProfileNotification:
        """单次情绪：只作为本次会话情境信号，不进入长期画像。"""
        return self._notification(
            account_id,
            kind=ProfileNotificationKind.TRANSIENT_EMOTION,
            title="情绪仅在本对话中",
            message=(
                "我注意到了你的情绪，它只作为本次对话的情境信号，"
                "不会写入长期画像。"
            ),
            conversation_id=conversation_id,
            message_id=message_id,
            source_text=intent.evidence,
            dimension=ProfileDimension.EMOTION_TREND,
            scene=intent.scene,
        )

    def decide_candidates_batch(
        self, account_id: str, request: ProfileBatchCandidateDecisionRequest
    ) -> ProfileBatchCandidateResult:
        """批量确认/修改/拒绝候选；幂等，失败可安全重试。

        已处于目标终态的候选记为 ``already_decided`` 不计为失败，重试
        不会重复写入或重复通知。
        """
        result = ProfileBatchCandidateResult()
        for candidate_id in request.candidate_ids:
            try:
                candidate = self._repository.get_candidate(account_id, candidate_id)
                if (
                    request.decision in {DecisionType.ACCEPT, DecisionType.MODIFY}
                    and candidate.review_status
                    in {CandidateReviewStatus.ACCEPTED, CandidateReviewStatus.MODIFIED}
                ):
                    result.already_decided.append(candidate_id)
                    continue
                if (
                    request.decision == DecisionType.REJECT
                    and candidate.review_status == CandidateReviewStatus.REJECTED
                ):
                    result.already_decided.append(candidate_id)
                    continue
                self.decide_candidate(
                    account_id,
                    candidate_id,
                    CandidateDecision(
                        decision=request.decision,
                        reason=request.reason,
                        modified_value_or_rule=request.modified_value_or_rule,
                        modified_applicable_scenes=request.modified_applicable_scenes,
                    ),
                )
                result.succeeded.append(candidate_id)
            except ProfileError as exc:
                result.failed.append(
                    {"candidate_id": candidate_id, "reason": str(exc)}
                )
        return result
