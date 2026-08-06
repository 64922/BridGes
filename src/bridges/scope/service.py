"""Scope isolation enforcer and fixture factory.

The scope enforcer is the single interpreter used by API routes, services,
cache builders, and background-task validators. It depends only on contracts and
can be exercised without FastAPI, SQLAlchemy, Temporal, or Redis.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

from bridges.contracts.identity import AuthMethod, SubjectContext
from bridges.contracts.projects import ObjectDomain, ObjectRef
from bridges.contracts.scope import (
    BackgroundTaskEnvelope,
    RLSContext,
    ScopeAction,
    ScopeCacheKey,
    ScopeEnvelope,
    ScopeIsolationError,
    ScopeViolationReport,
)
from bridges.contracts.vault import VaultObjectDomain, VaultObjectRef

if TYPE_CHECKING:
    from bridges.contracts.institution import InstitutionRole


InstitutionMembershipProvider = Callable[[str, str], "InstitutionRole | None"]
ProjectTenantProvider = Callable[[str], str | None]
SharedProjectMembershipProvider = Callable[[str, str], bool]


class ScopeEnforcer:
    """Single interpreter for scope authorization, cache keys and task envelopes.

    The enforcer treats every access as scoped to a subject, tenant, project,
    object domain, authorization version and key epoch. It returns structured
    decisions and reports; callers must not extend scope after authorization.
    """

    def __init__(
        self,
        *,
        deployment_cell: str = "local",
        institution_membership_provider: InstitutionMembershipProvider | None = None,
        project_tenant_provider: ProjectTenantProvider | None = None,
        shared_project_membership_provider: SharedProjectMembershipProvider | None = None,
    ) -> None:
        self._deployment_cell = deployment_cell
        self._institution_membership_provider = institution_membership_provider
        self._project_tenant_provider = project_tenant_provider
        self._shared_project_membership_provider = shared_project_membership_provider

    def set_institution_providers(
        self,
        *,
        institution_membership_provider: InstitutionMembershipProvider | None = None,
        project_tenant_provider: ProjectTenantProvider | None = None,
        shared_project_membership_provider: SharedProjectMembershipProvider | None = None,
    ) -> None:
        """Bind institution and membership providers after construction.

        This breaks dependency cycles: providers are bound once their owning
        services exist.
        """
        if institution_membership_provider is not None:
            self._institution_membership_provider = institution_membership_provider
        if project_tenant_provider is not None:
            self._project_tenant_provider = project_tenant_provider
        if shared_project_membership_provider is not None:
            self._shared_project_membership_provider = shared_project_membership_provider

    def set_shared_project_membership_provider(
        self, provider: SharedProjectMembershipProvider | None
    ) -> None:
        """Bind the shared-project membership provider owned by the sharing service."""
        if provider is not None:
            self._shared_project_membership_provider = provider

    @staticmethod
    def service_subject(account_id: str, service_name: str) -> SubjectContext:
        """Build the privileged service-internal subject context for a domain service.

        Every service asks the enforcer the same access question with the same
        subject shape, so session identifiers cannot drift between services.
        The semantics of an internal privileged context are unchanged from the
        per-service copies this factory replaces.
        """
        return SubjectContext(
            account_id=account_id,
            session_id=f"service:{service_name}",
            auth_method=AuthMethod.SERVICE,
        )

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
        if object_ref.domain == ObjectDomain.PERSONAL_VAULT:
            if subject.account_id != object_ref.owner_id:
                raise ScopeIsolationError("对象不存在或没有访问权限。")

        elif object_ref.domain == ObjectDomain.INSTITUTION_OWNED:
            if not self._is_institution_member(subject.account_id, object_ref.owner_id):
                raise ScopeIsolationError("对象不存在或没有访问权限。")

        elif object_ref.domain == ObjectDomain.SHARED_PROJECT:
            if not self._is_shared_project_member(subject.account_id, object_ref.owner_id):
                raise ScopeIsolationError("对象不存在或没有访问权限。")

        if rls_context is not None:
            if rls_context.subject.account_id != subject.account_id:
                raise ScopeIsolationError("RLS 上下文与主体不一致。")
            if rls_context.scope_envelope.object_domain != object_ref.domain:
                raise ScopeIsolationError("RLS 对象域与目标对象不一致。")

        tenant_id: str | None = None
        project_id: str | None = None
        if object_ref.domain == ObjectDomain.SHARED_PROJECT:
            project_id = object_ref.owner_id
            tenant_id = self._tenant_for_project(object_ref.owner_id)
        elif object_ref.domain == ObjectDomain.INSTITUTION_OWNED:
            tenant_id = self._tenant_for_object_owner(object_ref.owner_id)

        return self.compile_scope(
            subject,
            tenant_id=tenant_id,
            project_id=project_id,
            object_domain=object_ref.domain,
            purpose=action.value,
            requested_object_refs=[object_ref],
        )

    def authorize_membership(self, account_id: str, project_id: str) -> None:
        """Authorize an account as a member of a shared project.

        The membership decision is delegated to the provider bound by the
        sharing service so the "who may access this project" question has a
        single interpreter. Raises ScopeIsolationError when the account is not
        a member or no provider is bound (fail closed).
        """
        if not self._is_shared_project_member(account_id, project_id):
            raise ScopeIsolationError("项目不存在或没有访问权限。")

    def _is_shared_project_member(self, account_id: str, project_id: str) -> bool:
        """Check whether account_id is a member of the shared project."""
        if self._shared_project_membership_provider is None:
            return False
        return self._shared_project_membership_provider(project_id, account_id)

    def _is_institution_member(
        self, account_id: str, owner_id: str
    ) -> bool:
        """Check whether account_id is a member of the institution owning owner_id."""
        if self._institution_membership_provider is None:
            return False
        # owner_id may be the institution itself or a project owned by it.
        role = self._institution_membership_provider(account_id, owner_id)
        if role is not None:
            return True
        if self._project_tenant_provider is None:
            return False
        tenant_id = self._project_tenant_provider(owner_id)
        if tenant_id is None:
            return False
        role = self._institution_membership_provider(account_id, tenant_id)
        return role is not None

    def _tenant_for_project(self, project_id: str) -> str | None:
        """Return the institution tenant for a shared/institution project."""
        if self._project_tenant_provider is None:
            return None
        return self._project_tenant_provider(project_id)

    def _tenant_for_object_owner(self, owner_id: str) -> str | None:
        """Return the institution tenant for an object owner identifier."""
        if self._institution_membership_provider is None:
            return None
        # If the owner_id itself is a known institution, prefer it.
        # Otherwise resolve through the project tenant provider.
        if self._project_tenant_provider is not None:
            return self._project_tenant_provider(owner_id)
        return None

    def authorize_vault(
        self,
        subject: SubjectContext,
        action: ScopeAction,
        vault_ref: VaultObjectRef,
        *,
        rls_context: RLSContext | None = None,
    ) -> ScopeEnvelope:
        """Authorize an action against a vault object reference."""
        if vault_ref.domain == VaultObjectDomain.PERSONAL_VAULT:
            if subject.account_id != vault_ref.owner_id:
                raise ScopeIsolationError("对象不存在或没有访问权限。")
        elif vault_ref.domain == VaultObjectDomain.INSTITUTION_OWNED:
            if not self._is_institution_member(subject.account_id, vault_ref.owner_id):
                raise ScopeIsolationError("对象不存在或没有访问权限。")

        if rls_context is not None:
            if rls_context.subject.account_id != subject.account_id:
                raise ScopeIsolationError("RLS 上下文与主体不一致。")
            vault_domain_value = vault_ref.domain.value
            rls_domain_value = rls_context.scope_envelope.object_domain.value
            if vault_domain_value != rls_domain_value:
                raise ScopeIsolationError("RLS 对象域与目标保险库对象不一致。")

        tenant_id: str | None = None
        project_id: str | None = None
        if vault_ref.domain == VaultObjectDomain.SHARED_PROJECT:
            project_id = vault_ref.owner_id
            tenant_id = self._tenant_for_project(vault_ref.owner_id)
        elif vault_ref.domain == VaultObjectDomain.INSTITUTION_OWNED:
            tenant_id = self._tenant_for_object_owner(vault_ref.owner_id)

        return self.compile_scope(
            subject,
            tenant_id=tenant_id,
            project_id=project_id,
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
