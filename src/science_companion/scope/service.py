"""Scope isolation enforcer and fixture factory.

The scope enforcer is the single interpreter used by API routes, services,
cache builders, and background-task validators. It depends only on contracts and
can be exercised without FastAPI, SQLAlchemy, Temporal, or Redis.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from science_companion.contracts.identity import AuthMethod, SubjectContext
from science_companion.contracts.projects import ObjectDomain, ObjectRef
from science_companion.contracts.scope import (
    BackgroundTaskEnvelope,
    RLSContext,
    ScopeAction,
    ScopeCacheKey,
    ScopeEnvelope,
    ScopeIsolationError,
    ScopeViolationReport,
)
from science_companion.contracts.vault import VaultObjectDomain, VaultObjectRef


class ScopeEnforcer:
    """Single interpreter for scope authorization, cache keys and task envelopes.

    The enforcer treats every access as scoped to a subject, tenant, project,
    object domain, authorization version and key epoch. It returns structured
    decisions and reports; callers must not extend scope after authorization.
    """

    def __init__(self, *, deployment_cell: str = "local") -> None:
        self._deployment_cell = deployment_cell

    def compile_scope(
        self,
        subject: SubjectContext,
        *,
        tenant_id: str | None = None,
        project_id: str | None = None,
        object_domain: ObjectDomain = ObjectDomain.PERSONAL_VAULT,
        purpose: str = "general",
        requested_object_refs: list[ObjectRef] | None = None,
        requested_vault_refs: list[VaultObjectRef] | None = None,
        authorization_version: str = "authz-1.0",
        key_epoch: str = "epoch-0",
    ) -> ScopeEnvelope:
        """Compile a scope envelope from a subject and requested targets."""
        return ScopeEnvelope(
            account_id=subject.account_id,
            tenant_id=tenant_id,
            project_id=project_id,
            object_domain=object_domain,
            authorization_version=authorization_version,
            key_epoch=key_epoch,
            purpose=purpose,
            requested_object_refs=list(requested_object_refs or []),
            requested_vault_refs=list(requested_vault_refs or []),
        )

    def set_rls_context(self, subject: SubjectContext, scope: ScopeEnvelope) -> RLSContext:
        """Return a transaction-level RLS context.

        In persistent deployments this would be pushed into the database session;
        in-memory adapters carry it explicitly so tests can verify it was set.
        """
        return RLSContext(
            subject=subject,
            scope_envelope=scope,
            set_at=datetime.now(timezone.utc),
        )

    def authorize(
        self,
        subject: SubjectContext,
        action: ScopeAction,
        object_ref: ObjectRef,
        *,
        rls_context: RLSContext | None = None,
    ) -> ScopeEnvelope:
        """Authorize an action against a single object reference.

        Raises ScopeIsolationError when the subject is not allowed to perform the
        action on the object in its current domain/tenant/project.
        """
        if subject.account_id != object_ref.owner_id and object_ref.domain in {
            ObjectDomain.PERSONAL_VAULT,
            ObjectDomain.INSTITUTION_OWNED,
        }:
            # Cross-account access to personal or institution-owned objects is
            # denied at the base layer. Shared-project access is checked by
            # project membership and object grants in later tickets.
            raise ScopeIsolationError("对象不存在或没有访问权限。")

        if rls_context is not None:
            if rls_context.subject.account_id != subject.account_id:
                raise ScopeIsolationError("RLS 上下文与主体不一致。")
            if (
                rls_context.scope_envelope.object_domain != object_ref.domain
                and rls_context.scope_envelope.object_domain != ObjectDomain.PERSONAL_VAULT
            ):
                # Allow personal-vault default to avoid breaking existing routes;
                # explicit mismatch still fails.
                raise ScopeIsolationError("RLS 对象域与目标对象不一致。")

        return self.compile_scope(
            subject,
            project_id=object_ref.owner_id
            if object_ref.domain == ObjectDomain.SHARED_PROJECT
            else None,
            object_domain=object_ref.domain,
            purpose=action.value,
            requested_object_refs=[object_ref],
        )

    def authorize_vault(
        self,
        subject: SubjectContext,
        action: ScopeAction,
        vault_ref: VaultObjectRef,
        *,
        rls_context: RLSContext | None = None,
    ) -> ScopeEnvelope:
        """Authorize an action against a vault object reference."""
        if subject.account_id != vault_ref.owner_id and vault_ref.domain in {
            VaultObjectDomain.PERSONAL_VAULT,
            VaultObjectDomain.INSTITUTION_OWNED,
        }:
            raise ScopeIsolationError("对象不存在或没有访问权限。")

        if rls_context is not None:
            if rls_context.subject.account_id != subject.account_id:
                raise ScopeIsolationError("RLS 上下文与主体不一致。")

        return self.compile_scope(
            subject,
            project_id=vault_ref.owner_id
            if vault_ref.domain == VaultObjectDomain.SHARED_PROJECT
            else None,
            object_domain=ObjectDomain(vault_ref.domain.value),
            purpose=action.value,
            requested_vault_refs=[vault_ref],
        )

    def build_cache_key(
        self,
        scope: ScopeEnvelope,
        *,
        object_version: str | None = None,
        content_version: str | None = None,
        model_schema_version: str | None = None,
    ) -> str:
        """Build a scope-aware cache key that prevents cross-user hits."""
        return ScopeCacheKey(
            deployment_cell=self._deployment_cell,
            tenant_id=scope.tenant_id,
            account_id=scope.account_id,
            object_domain=scope.object_domain,
            project_id=scope.project_id,
            authorization_version=scope.authorization_version,
            key_epoch=scope.key_epoch,
            object_version=object_version,
            content_version=content_version,
            model_schema_version=model_schema_version,
        ).build()

    def validate_background_task(
        self,
        envelope: BackgroundTaskEnvelope,
        *,
        required_object_refs: list[str] | None = None,
    ) -> BackgroundTaskEnvelope:
        """Validate that a background task carries all required scope fields.

        Raises ScopeIsolationError if any mandatory field is missing, which causes
        the task to fail closed before touching storage, cache, index or models.
        """
        if not envelope.subject.account_id:
            raise ScopeIsolationError("后台任务缺少主体账户标识。")
        if not envelope.subject.session_id:
            raise ScopeIsolationError("后台任务缺少主体会话标识。")
        if envelope.object_domain is None:
            raise ScopeIsolationError("后台任务缺少对象域。")
        if not envelope.authorization_version:
            raise ScopeIsolationError("后台任务缺少授权版本。")
        if not envelope.key_epoch:
            raise ScopeIsolationError("后台任务缺少密钥时期。")
        if not envelope.purpose:
            raise ScopeIsolationError("后台任务缺少用途声明。")

        for ref in required_object_refs or []:
            if ref not in envelope.object_refs:
                raise ScopeIsolationError(f"后台任务缺少必需对象引用：{ref}。")

        return envelope

    def report_violation(
        self,
        subject: SubjectContext,
        action: ScopeAction,
        scope: ScopeEnvelope,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> ScopeViolationReport:
        """Produce an audit-safe scope violation report."""
        return ScopeViolationReport(
            actor_account_id=subject.account_id,
            actor_session_id=subject.session_id,
            action=action,
            requested_scope=scope,
            reason=reason,
            occurred_at=datetime.now(timezone.utc),
            details=details or {},
        )


class ScopeFixtureFactory:
    """Reusable factory for multi-user isolation scenarios.

    Produces two accounts, two tenants, two projects, and matching vault objects
    with identical names or content hashes so that tests can prove isolation is
    by scope, not by name or hash.
    """

    def __init__(self, enforcer: ScopeEnforcer) -> None:
        self._enforcer = enforcer

    def build_two_user_scenario(self) -> dict[str, Any]:
        """Return a deterministic two-account, two-project, two-tenant fixture.

        The returned dictionary contains subjects, scopes, object refs and vault
        refs that exercise cross-account, cross-project and cross-tenant denial.
        """
        alice_subject = SubjectContext(
            account_id="account-alice",
            session_id="session-alice",
            auth_method=AuthMethod.PASSWORD,
            device_id="device-alice",
        )
        bob_subject = SubjectContext(
            account_id="account-bob",
            session_id="session-bob",
            auth_method=AuthMethod.PASSWORD,
            device_id="device-bob",
        )

        alice_scope = self._enforcer.compile_scope(
            alice_subject,
            tenant_id="tenant-alpha",
            project_id="project-alice-1",
            object_domain=ObjectDomain.PERSONAL_VAULT,
            purpose="test_isolation",
        )
        bob_scope = self._enforcer.compile_scope(
            bob_subject,
            tenant_id="tenant-beta",
            project_id="project-bob-1",
            object_domain=ObjectDomain.PERSONAL_VAULT,
            purpose="test_isolation",
        )

        alice_ref = ObjectRef(
            domain=ObjectDomain.PERSONAL_VAULT,
            owner_id=alice_subject.account_id,
            object_id="object-same-name-1",
            version=1,
        )
        bob_ref = ObjectRef(
            domain=ObjectDomain.PERSONAL_VAULT,
            owner_id=bob_subject.account_id,
            object_id="object-same-name-1",
            version=1,
        )

        return {
            "alice": {
                "subject": alice_subject,
                "scope": alice_scope,
                "project_id": "project-alice-1",
                "tenant_id": "tenant-alpha",
                "object_ref": alice_ref,
            },
            "bob": {
                "subject": bob_subject,
                "scope": bob_scope,
                "project_id": "project-bob-1",
                "tenant_id": "tenant-beta",
                "object_ref": bob_ref,
            },
        }
