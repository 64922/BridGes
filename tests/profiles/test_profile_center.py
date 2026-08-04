"""Issue 25: digital-twin profile center governance module tests.

The seam under test: the nine-dimension registry, manual record creation,
withdraw/unfreeze lifecycle, last-used tracking and export hygiene that the
profile center page consumes. Every operation must be account-scoped and
produce an unambiguous audit action.
"""

from __future__ import annotations

import pytest

from bridges.contracts.observability import AuditAction
from bridges.contracts.profiles import (
    PROFILE_DIMENSION_LABELS,
    USER_CONFIRMED_DIMENSIONS,
    AssertionStatus,
    ManualAssertionCreateRequest,
    ProfileDimension,
    ProfileSensitivityClass,
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
def observability_service() -> ObservabilityService:
    return ObservabilityService()


@pytest.fixture
def invalidation_service() -> InvalidationService:
    return InvalidationService()


@pytest.fixture
def service(
    repository: InMemoryProfileRepository,
    invalidation_service: InvalidationService,
    observability_service: ObservabilityService,
) -> ProfileService:
    return ProfileService(
        repository=repository,
        invalidation_service=invalidation_service,
        observability_service=observability_service,
    )


@pytest.fixture
def alice_id() -> str:
    return "account-alice"


@pytest.fixture
def bob_id() -> str:
    return "account-bob"


def _manual_request(
    dimension: ProfileDimension = ProfileDimension.STAGE_GOAL,
    value: str = "三个月内完成科学项目框架",
    scenes: list[str] | None = None,
) -> ManualAssertionCreateRequest:
    return ManualAssertionCreateRequest(
        dimension=dimension,
        value_or_rule=value,
        applicable_scenes=scenes or ["quick_check"],
        sensitivity_class=ProfileSensitivityClass.PREFERENCE,
        authorization_scope="general",
        source_note="用户手动记录",
    )


def _audit_actions(
    observability: ObservabilityService, account_id: str, action: AuditAction
) -> list[str]:
    return [
        event.event_id
        for event in observability.list_audit_events(
            account_id=account_id, action=action
        )
    ]


class TestProfileDimensionRegistry:
    def test_registry_has_nine_governable_dimensions(self) -> None:
        assert len(ProfileDimension) == 9
        assert set(ProfileDimension) == {
            ProfileDimension.BASIC_INFORMATION,
            ProfileDimension.STAGE_GOAL,
            ProfileDimension.INTEREST_PREFERENCE,
            ProfileDimension.EXPRESSION_HABIT,
            ProfileDimension.KNOWLEDGE_STATE,
            ProfileDimension.EMOTION_TREND,
            ProfileDimension.IMPORTANT_EXPERIENCE,
            ProfileDimension.CURRENT_PROBLEM,
            ProfileDimension.AUTHORIZATION_SCOPE,
        }

    def test_every_dimension_has_a_chinese_label(self) -> None:
        assert set(PROFILE_DIMENSION_LABELS) == set(ProfileDimension)
        assert all(label for label in PROFILE_DIMENSION_LABELS.values())

    def test_sensitive_dimensions_require_user_confirmation(self) -> None:
        assert {
            ProfileDimension.EMOTION_TREND,
            ProfileDimension.IMPORTANT_EXPERIENCE,
            ProfileDimension.CURRENT_PROBLEM,
        } == USER_CONFIRMED_DIMENSIONS


class TestManualAssertionCreate:
    def test_manual_create_promotes_active_assertion_with_traceable_chain(
        self,
        service: ProfileService,
        alice_id: str,
    ) -> None:
        assertion = service.manual_create_assertion(alice_id, _manual_request())

        assert assertion.status == AssertionStatus.ACTIVE
        assert assertion.canonical_dimension == ProfileDimension.STAGE_GOAL.value
        assert assertion.value_or_rule == "三个月内完成科学项目框架"
        assert assertion.version == 1
        assert assertion.promoted_from_candidate_id is not None

        # The full observation → candidate → accept chain is retained so the
        # record's provenance stays traceable.
        assert len(service.list_observations(alice_id)) == 1
        assert len(service.list_candidates(alice_id)) == 1
        candidate = service.list_candidates(alice_id)[0]
        assert candidate.human_decision is not None
        assert candidate.human_decision.decision.value == "accept"

    def test_manual_create_writes_unambiguous_create_audit(
        self,
        service: ProfileService,
        observability_service: ObservabilityService,
        alice_id: str,
    ) -> None:
        service.manual_create_assertion(alice_id, _manual_request())

        assert _audit_actions(
            observability_service, alice_id, AuditAction.PROFILE_CREATE
        )
        # No other profile governance action is recorded for a plain create.
        assert not _audit_actions(
            observability_service, alice_id, AuditAction.PROFILE_WITHDRAW
        )

    def test_manual_create_is_account_scoped(
        self,
        service: ProfileService,
        alice_id: str,
        bob_id: str,
    ) -> None:
        service.manual_create_assertion(alice_id, _manual_request())

        # Bob cannot see, withdraw or delete Alice's record.
        assertions = service.list_assertions(bob_id)
        assert assertions == []
        alice_assertion = service.list_assertions(alice_id)[0]
        with pytest.raises(ProfileError):
            service.withdraw_assertion(bob_id, alice_assertion.assertion_id, "Hack.")
        with pytest.raises(ProfileError):
            service.delete_assertion(bob_id, alice_assertion.assertion_id, "Hack.")

    def test_manual_create_allows_sensitive_user_declared_dimensions(
        self,
        service: ProfileService,
        alice_id: str,
    ) -> None:
        for dimension in USER_CONFIRMED_DIMENSIONS:
            assertion = service.manual_create_assertion(
                alice_id, _manual_request(dimension=dimension, value=f"关于{dimension.value}")
            )
            assert assertion.status == AssertionStatus.ACTIVE

    def test_manual_create_authorization_scope_is_user_set(
        self,
        service: ProfileService,
        alice_id: str,
    ) -> None:
        assertion = service.manual_create_assertion(
            alice_id,
            _manual_request(
                dimension=ProfileDimension.AUTHORIZATION_SCOPE,
                value="仅限学习模式使用",
                scenes=["learning_mode"],
            ),
        )
        assert assertion.authorization_scope == "general"
        assert assertion.applicable_scenes == ["learning_mode"]


class TestWithdrawAndUnfreeze:
    def test_withdraw_stops_answer_use_but_keeps_history(
        self,
        service: ProfileService,
        observability_service: ObservabilityService,
        alice_id: str,
    ) -> None:
        assertion = service.manual_create_assertion(alice_id, _manual_request())
        created_version = assertion.version
        slice_before = service.compile_memory_slice(
            alice_id, purpose="quick_check", run_id="run-before-withdraw"
        )
        assert any(
            item.assertion_id == assertion.assertion_id
            for item in slice_before.included_items
        )

        withdrawn = service.withdraw_assertion(
            alice_id, assertion.assertion_id, "不再适用"
        )
        assert withdrawn.status == AssertionStatus.WITHDRAWN
        assert withdrawn.version == created_version + 1

        slice_after = service.compile_memory_slice(
            alice_id, purpose="quick_check", run_id="run-after-withdraw"
        )
        assert not any(
            item.assertion_id == assertion.assertion_id
            for item in slice_after.included_items
        )
        # History is retained.
        history = service.list_assertions(alice_id)
        assert len(history) == 1
        assert _audit_actions(
            observability_service, alice_id, AuditAction.PROFILE_WITHDRAW
        )

    def test_withdraw_revokes_existing_slices(
        self,
        service: ProfileService,
        alice_id: str,
    ) -> None:
        assertion = service.manual_create_assertion(alice_id, _manual_request())
        slice_ = service.compile_memory_slice(
            alice_id, purpose="quick_check", run_id="run-revoke"
        )
        service.withdraw_assertion(alice_id, assertion.assertion_id, "不再适用")

        revoked = service.get_slice(alice_id, slice_.slice_id)
        assert revoked.status == SliceStatus.REVOKED

    def test_withdraw_requires_active_status(
        self,
        service: ProfileService,
        alice_id: str,
    ) -> None:
        assertion = service.manual_create_assertion(alice_id, _manual_request())
        service.withdraw_assertion(alice_id, assertion.assertion_id, "第一次撤回")
        with pytest.raises(ProfileError):
            service.withdraw_assertion(alice_id, assertion.assertion_id, "再次撤回")

    def test_unfreeze_restores_frozen_and_withdrawn(
        self,
        service: ProfileService,
        observability_service: ObservabilityService,
        alice_id: str,
    ) -> None:
        assertion = service.manual_create_assertion(alice_id, _manual_request())

        frozen = service.freeze_assertion(alice_id, assertion.assertion_id, "暂时冻结")
        frozen_version = frozen.version
        restored = service.unfreeze_assertion(alice_id, frozen.assertion_id, "恢复使用")
        assert restored.status == AssertionStatus.ACTIVE
        assert restored.version == frozen_version + 1

        withdrawn = service.withdraw_assertion(alice_id, restored.assertion_id, "撤回")
        withdrawn_version = withdrawn.version
        restored_again = service.unfreeze_assertion(
            alice_id, withdrawn.assertion_id, "恢复使用"
        )
        assert restored_again.status == AssertionStatus.ACTIVE
        assert restored_again.version == withdrawn_version + 1

        slice_ = service.compile_memory_slice(
            alice_id, purpose="quick_check", run_id="run-after-unfreeze"
        )
        assert any(
            item.assertion_id == restored_again.assertion_id
            for item in slice_.included_items
        )
        assert _audit_actions(
            observability_service, alice_id, AuditAction.PROFILE_UNFREEZE
        )

    def test_unfreeze_requires_frozen_or_withdrawn(
        self,
        service: ProfileService,
        alice_id: str,
    ) -> None:
        assertion = service.manual_create_assertion(alice_id, _manual_request())
        with pytest.raises(ProfileError):
            service.unfreeze_assertion(alice_id, assertion.assertion_id, "解冻")

    def test_deleted_assertion_cannot_withdraw_or_unfreeze(
        self,
        service: ProfileService,
        alice_id: str,
    ) -> None:
        assertion = service.manual_create_assertion(alice_id, _manual_request())
        service.delete_assertion(alice_id, assertion.assertion_id, "删除")
        with pytest.raises(ProfileError):
            service.withdraw_assertion(alice_id, assertion.assertion_id, "撤回")
        with pytest.raises(ProfileError):
            service.unfreeze_assertion(alice_id, assertion.assertion_id, "解冻")


class TestLastUsedTracking:
    def test_compile_slice_records_last_used_at(
        self,
        service: ProfileService,
        alice_id: str,
    ) -> None:
        assertion = service.manual_create_assertion(alice_id, _manual_request())
        assert assertion.last_used_at is None

        service.compile_memory_slice(
            alice_id, purpose="quick_check", run_id="run-used"
        )
        updated = service.get_assertion(alice_id, assertion.assertion_id)
        assert updated.last_used_at is not None

    def test_unused_assertion_keeps_last_used_at_none(
        self,
        service: ProfileService,
        alice_id: str,
    ) -> None:
        assertion = service.manual_create_assertion(
            alice_id,
            _manual_request(
                dimension=ProfileDimension.EXPRESSION_HABIT,
                value="回答保持简洁",
                scenes=["learning_mode"],
            ),
        )
        # Compile for a purpose outside the assertion's scenes.
        service.compile_memory_slice(
            alice_id, purpose="quick_check", run_id="run-not-applicable"
        )
        updated = service.get_assertion(alice_id, assertion.assertion_id)
        assert updated.last_used_at is None


class TestExportHygiene:
    def test_export_includes_last_used_at_and_redacts_deleted_body(
        self,
        service: ProfileService,
        alice_id: str,
    ) -> None:
        assertion = service.manual_create_assertion(
            alice_id,
            _manual_request(
                value="这条记录会被删除",
                scenes=["quick_check"],
            ),
        )
        service.compile_memory_slice(
            alice_id, purpose="quick_check", run_id="run-export-used"
        )
        service.delete_assertion(alice_id, assertion.assertion_id, "清理")

        exported = service.export_profile_data(alice_id)
        assert len(exported.assertions) == 1
        record = exported.assertions[0]
        assert record.status == AssertionStatus.DELETED
        assert record.value_or_rule is None
        assert record.last_used_at is not None
        assert record.content_hash  # integrity hash retained
