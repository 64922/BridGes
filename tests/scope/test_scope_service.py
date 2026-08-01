"""Unit tests for the ScopeEnforcer.

These tests exercise the pure domain interpreter without any web framework,
database or orchestrator.
"""

from __future__ import annotations

import pytest

from bridges.contracts.identity import AuthMethod, SubjectContext
from bridges.contracts.projects import ObjectDomain, ObjectRef
from bridges.contracts.scope import (
    BackgroundTaskEnvelope,
    ScopeAction,
    ScopeCacheKey,
    ScopeEnvelope,
    ScopeIsolationError,
)
from bridges.contracts.vault import VaultObjectDomain, VaultObjectRef
from bridges.scope import ScopeEnforcer, ScopeFixtureFactory


@pytest.fixture
def enforcer() -> ScopeEnforcer:
    return ScopeEnforcer(deployment_cell="unit-test")


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


class TestScopeCompilation:
    def test_compile_scope_returns_envelope(self, enforcer: ScopeEnforcer, alice: SubjectContext) -> None:
        scope = enforcer.compile_scope(
            alice,
            tenant_id="tenant-1",
            project_id="project-1",
            object_domain=ObjectDomain.SHARED_PROJECT,
            purpose="ingest",
        )
        assert isinstance(scope, ScopeEnvelope)
        assert scope.account_id == alice.account_id
        assert scope.tenant_id == "tenant-1"
        assert scope.project_id == "project-1"
        assert scope.object_domain == ObjectDomain.SHARED_PROJECT
        assert scope.purpose == "ingest"


class TestObjectAuthorization:
    def test_owner_can_read_personal_object(
        self, enforcer: ScopeEnforcer, alice: SubjectContext
    ) -> None:
        ref = ObjectRef(
            domain=ObjectDomain.PERSONAL_VAULT,
            owner_id=alice.account_id,
            object_id="obj-1",
            version=1,
        )
        scope = enforcer.authorize(alice, ScopeAction.READ, ref)
        assert scope.account_id == alice.account_id
        assert scope.object_domain == ObjectDomain.PERSONAL_VAULT

    def test_other_user_cannot_read_personal_object(
        self, enforcer: ScopeEnforcer, alice: SubjectContext, bob: SubjectContext
    ) -> None:
        ref = ObjectRef(
            domain=ObjectDomain.PERSONAL_VAULT,
            owner_id=alice.account_id,
            object_id="obj-1",
            version=1,
        )
        with pytest.raises(ScopeIsolationError):
            enforcer.authorize(bob, ScopeAction.READ, ref)

    def test_other_user_cannot_read_institution_object(
        self, enforcer: ScopeEnforcer, alice: SubjectContext, bob: SubjectContext
    ) -> None:
        ref = ObjectRef(
            domain=ObjectDomain.INSTITUTION_OWNED,
            owner_id="institution-alpha",
            object_id="obj-2",
            version=1,
        )
        with pytest.raises(ScopeIsolationError):
            enforcer.authorize(bob, ScopeAction.READ, ref)


class TestVaultAuthorization:
    def test_owner_can_read_vault_object(
        self, enforcer: ScopeEnforcer, alice: SubjectContext
    ) -> None:
        ref = VaultObjectRef(
            domain=VaultObjectDomain.PERSONAL_VAULT,
            owner_id=alice.account_id,
            object_id="vobj-1",
            version=1,
        )
        scope = enforcer.authorize_vault(alice, ScopeAction.READ, ref)
        assert scope.account_id == alice.account_id

    def test_other_user_cannot_read_vault_object(
        self, enforcer: ScopeEnforcer, alice: SubjectContext, bob: SubjectContext
    ) -> None:
        ref = VaultObjectRef(
            domain=VaultObjectDomain.PERSONAL_VAULT,
            owner_id=alice.account_id,
            object_id="vobj-1",
            version=1,
        )
        with pytest.raises(ScopeIsolationError):
            enforcer.authorize_vault(bob, ScopeAction.READ, ref)


class TestCacheKeyIsolation:
    def test_cache_key_includes_account(self, enforcer: ScopeEnforcer, alice: SubjectContext) -> None:
        scope = enforcer.compile_scope(alice)
        key = enforcer.build_cache_key(scope)
        assert alice.account_id in key

    def test_different_users_get_different_keys(
        self, enforcer: ScopeEnforcer, alice: SubjectContext, bob: SubjectContext
    ) -> None:
        alice_key = enforcer.build_cache_key(enforcer.compile_scope(alice))
        bob_key = enforcer.build_cache_key(enforcer.compile_scope(bob))
        assert alice_key != bob_key

    def test_cache_key_model_includes_all_scope_fields(
        self, enforcer: ScopeEnforcer, alice: SubjectContext
    ) -> None:
        scope = enforcer.compile_scope(
            alice,
            tenant_id="tenant-x",
            project_id="project-x",
            object_domain=ObjectDomain.SHARED_PROJECT,
            authorization_version="authz-2.0",
            key_epoch="epoch-3",
        )
        key = ScopeCacheKey(
            deployment_cell="prod",
            tenant_id=scope.tenant_id,
            account_id=scope.account_id,
            object_domain=scope.object_domain,
            project_id=scope.project_id,
            authorization_version=scope.authorization_version,
            key_epoch=scope.key_epoch,
            object_version="v1",
            content_version="v2",
            model_schema_version="v3",
        ).build()
        assert "tenant-x" in key
        assert alice.account_id in key
        assert "shared_project" in key
        assert "project-x" in key
        assert "authz-2.0" in key
        assert "epoch-3" in key


class TestBackgroundTaskValidation:
    def test_valid_task_passes(self, enforcer: ScopeEnforcer, alice: SubjectContext) -> None:
        envelope = BackgroundTaskEnvelope(
            task_id="task-1",
            task_type="ingestion",
            subject=alice,
            object_domain=ObjectDomain.PERSONAL_VAULT,
            authorization_version="authz-1.0",
            key_epoch="epoch-0",
            purpose="test",
        )
        assert enforcer.validate_background_task(envelope) is envelope

    def test_missing_account_fails(self, enforcer: ScopeEnforcer) -> None:
        subject = SubjectContext(
            account_id="",
            session_id="session-1",
            auth_method=AuthMethod.PASSWORD,
        )
        envelope = BackgroundTaskEnvelope(
            task_id="task-2",
            task_type="ingestion",
            subject=subject,
            object_domain=ObjectDomain.PERSONAL_VAULT,
            authorization_version="authz-1.0",
            key_epoch="epoch-0",
            purpose="test",
        )
        with pytest.raises(ScopeIsolationError):
            enforcer.validate_background_task(envelope)

    def test_missing_key_epoch_fails(self, enforcer: ScopeEnforcer, alice: SubjectContext) -> None:
        envelope = BackgroundTaskEnvelope(
            task_id="task-3",
            task_type="ingestion",
            subject=alice,
            object_domain=ObjectDomain.PERSONAL_VAULT,
            authorization_version="authz-1.0",
            key_epoch="",
            purpose="test",
        )
        with pytest.raises(ScopeIsolationError):
            enforcer.validate_background_task(envelope)


class TestFixtureFactory:
    def test_two_user_scenario_has_distinct_accounts(self) -> None:
        factory = ScopeFixtureFactory(ScopeEnforcer())
        fixture = factory.build_two_user_scenario()
        assert fixture["alice"]["subject"].account_id != fixture["bob"]["subject"].account_id
        assert fixture["alice"]["tenant_id"] != fixture["bob"]["tenant_id"]
        assert fixture["alice"]["project_id"] != fixture["bob"]["project_id"]
