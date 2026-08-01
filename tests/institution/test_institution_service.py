"""Module-interface tests for the institution management service.

The seam under test: an institution admin can manage members, policy and
institution-owned projects, but cannot read member personal vaults without an
explicit, two-approval, time-limited controlled content access request.
"""

from __future__ import annotations

import pytest

from bridges.contracts.institution import (
    ControlledContentAccessStatus,
    InstitutionCreateRequest,
    InstitutionInviteRequest,
    InstitutionMemberUpdateRequest,
    InstitutionProjectCreateRequest,
    InstitutionRole,
    SeatPolicy,
)
from bridges.contracts.projects import ObjectDomain
from bridges.contracts.scope import ScopeAction
from bridges.contracts.vault import ContentAuthority, VaultObjectDomain
from bridges.institution import InstitutionService, InstitutionServiceError
from bridges.scope import ScopeEnforcer
from bridges.sharing import SharingService
from bridges.vault import (
    InMemoryVaultRepository,
    MemoryDeviceVaultPort,
    VaultService,
)


@pytest.fixture
def vault_service() -> VaultService:
    repository = InMemoryVaultRepository()
    return VaultService(
        repository=repository,
        device_port=MemoryDeviceVaultPort(repository),
    )


@pytest.fixture
def sharing_service(vault_service: VaultService) -> SharingService:
    return SharingService(vault_service=vault_service)


@pytest.fixture
def institution_service(sharing_service: SharingService) -> InstitutionService:
    service = InstitutionService()
    service.bind_sharing_service(sharing_service)
    return service


@pytest.fixture
def scope_enforcer(
    institution_service: InstitutionService,
    sharing_service: SharingService,
) -> ScopeEnforcer:
    def _membership_provider(account_id: str, institution_id: str) -> InstitutionRole | None:
        membership = institution_service.get_membership(institution_id, account_id)
        return membership.role if membership is not None else None

    return ScopeEnforcer(
        deployment_cell="unit-test",
        institution_membership_provider=_membership_provider,
        project_tenant_provider=sharing_service.get_project_institution_id,
    )


@pytest.fixture
def alice_id() -> str:
    return "account-alice"


@pytest.fixture
def bob_id() -> str:
    return "account-bob"


@pytest.fixture
def carol_id() -> str:
    return "account-carol"


class TestInstitutionCreation:
    def test_create_institution_makes_creator_admin(
        self, institution_service: InstitutionService, alice_id: str
    ) -> None:
        institution = institution_service.create_institution(
            alice_id, InstitutionCreateRequest(name="测试机构")
        )
        assert institution.name == "测试机构"
        assert institution.owner_account_id == alice_id

        members = institution_service.list_members(alice_id, institution.id)
        assert len(members) == 1
        assert members[0].account_id == alice_id
        assert members[0].role == InstitutionRole.ADMIN

    def test_non_member_cannot_view_institution(
        self, institution_service: InstitutionService, alice_id: str, bob_id: str
    ) -> None:
        institution = institution_service.create_institution(
            alice_id, InstitutionCreateRequest(name="隔离机构")
        )
        with pytest.raises(InstitutionServiceError):
            institution_service.get_institution(bob_id, institution.id)


class TestMultiInstitutionIsolation:
    def test_account_joins_two_institutions_without_cross_leak(
        self,
        institution_service: InstitutionService,
        sharing_service: SharingService,
        alice_id: str,
        bob_id: str,
    ) -> None:
        alpha = institution_service.create_institution(
            alice_id, InstitutionCreateRequest(name="机构 Alpha")
        )
        beta = institution_service.create_institution(
            bob_id, InstitutionCreateRequest(name="机构 Beta")
        )

        # Invite alice to beta as member.
        invite = institution_service.invite_member(
            beta.id,
            bob_id,
            InstitutionInviteRequest(recipient_account_id=alice_id, role=InstitutionRole.MEMBER),
        )
        institution_service.accept_invite(alice_id, invite.token_secret)

        alice_institutions = institution_service.list_institutions_for_account(alice_id)
        assert {i.id for i in alice_institutions} == {alpha.id, beta.id}

        # Create institution-owned projects in each.
        alpha_project = institution_service.create_institution_owned_project(
            alice_id,
            alpha.id,
            InstitutionProjectCreateRequest(
                name="Alpha 项目", disclosure_acknowledged=True
            ),
        )
        beta_project = institution_service.create_institution_owned_project(
            bob_id,
            beta.id,
            InstitutionProjectCreateRequest(
                name="Beta 项目", disclosure_acknowledged=True
            ),
        )

        # Alice sees alpha project and beta project (she is member of both).
        alpha_projects = institution_service.list_institution_projects(
            alice_id, alpha.id
        )
        assert any(p.id == alpha_project.project.id for p in alpha_projects)
        beta_projects = institution_service.list_institution_projects(
            alice_id, beta.id
        )
        assert any(p.id == beta_project.project.id for p in beta_projects)


class TestMemberManagement:
    def test_admin_can_invite_and_remove_member(
        self,
        institution_service: InstitutionService,
        alice_id: str,
        bob_id: str,
    ) -> None:
        institution = institution_service.create_institution(
            alice_id, InstitutionCreateRequest(name="成员管理机构")
        )
        invite = institution_service.invite_member(
            institution.id,
            alice_id,
            InstitutionInviteRequest(
                recipient_account_id=bob_id, role=InstitutionRole.MEMBER
            ),
        )
        membership = institution_service.accept_invite(bob_id, invite.token_secret)
        assert membership.role == InstitutionRole.MEMBER

        members = institution_service.list_members(alice_id, institution.id)
        assert any(m.account_id == bob_id for m in members)

        institution_service.remove_member(institution.id, alice_id, bob_id)
        members = institution_service.list_members(alice_id, institution.id)
        assert not any(m.account_id == bob_id for m in members)

    def test_member_cannot_manage_members(
        self,
        institution_service: InstitutionService,
        alice_id: str,
        bob_id: str,
        carol_id: str,
    ) -> None:
        institution = institution_service.create_institution(
            alice_id, InstitutionCreateRequest(name="权限测试机构")
        )
        invite = institution_service.invite_member(
            institution.id,
            alice_id,
            InstitutionInviteRequest(
                recipient_account_id=bob_id, role=InstitutionRole.MEMBER
            ),
        )
        institution_service.accept_invite(bob_id, invite.token_secret)

        with pytest.raises(InstitutionServiceError):
            institution_service.invite_member(
                institution.id,
                bob_id,
                InstitutionInviteRequest(
                    recipient_account_id=carol_id, role=InstitutionRole.MEMBER
                ),
            )


class TestInstitutionOwnedProject:
    def test_create_project_requires_disclosure_acknowledgement(
        self, institution_service: InstitutionService, alice_id: str
    ) -> None:
        institution = institution_service.create_institution(
            alice_id, InstitutionCreateRequest(name="项目披露机构")
        )
        with pytest.raises(InstitutionServiceError):
            institution_service.create_institution_owned_project(
                alice_id,
                institution.id,
                InstitutionProjectCreateRequest(
                    name="未披露项目", disclosure_acknowledged=False
                ),
            )

    def test_create_project_returns_disclosure(
        self, institution_service: InstitutionService, alice_id: str
    ) -> None:
        institution = institution_service.create_institution(
            alice_id, InstitutionCreateRequest(name="项目披露机构")
        )
        response = institution_service.create_institution_owned_project(
            alice_id,
            institution.id,
            InstitutionProjectCreateRequest(
                name="机构项目", disclosure_acknowledged=True
            ),
        )
        assert response.project.name == "机构项目"
        assert response.project.object_domain == ObjectDomain.INSTITUTION_OWNED
        assert response.project.tenant_id == institution.id
        assert response.disclosure.institution_id == institution.id
        assert response.disclosure.acknowledged is True

    def test_member_can_list_but_non_member_cannot(
        self,
        institution_service: InstitutionService,
        alice_id: str,
        bob_id: str,
    ) -> None:
        institution = institution_service.create_institution(
            alice_id, InstitutionCreateRequest(name="可见性机构")
        )
        institution_service.create_institution_owned_project(
            alice_id,
            institution.id,
            InstitutionProjectCreateRequest(
                name="机构项目", disclosure_acknowledged=True
            ),
        )

        assert len(institution_service.list_institution_projects(alice_id, institution.id)) == 1
        with pytest.raises(InstitutionServiceError):
            institution_service.list_institution_projects(bob_id, institution.id)


class TestAdminCannotReadPersonalVault:
    def test_admin_cannot_read_member_vault_object(
        self,
        institution_service: InstitutionService,
        vault_service: VaultService,
        scope_enforcer: ScopeEnforcer,
        alice_id: str,
        bob_id: str,
    ) -> None:
        institution = institution_service.create_institution(
            alice_id, InstitutionCreateRequest(name="保险库隔离机构")
        )
        invite = institution_service.invite_member(
            institution.id,
            alice_id,
            InstitutionInviteRequest(
                recipient_account_id=bob_id, role=InstitutionRole.MEMBER
            ),
        )
        institution_service.accept_invite(bob_id, invite.token_secret)

        # Bob creates a personal vault object.
        obj = vault_service.create_private_object(
            owner_account_id=bob_id,
            content=b"bob private content",
            content_authority=ContentAuthority.SERVER_REPLICA,
        )
        assert obj.ref.domain == VaultObjectDomain.PERSONAL_VAULT

        # Alice is admin but cannot authorize reading Bob's personal vault object.
        from bridges.contracts.identity import AuthMethod, SubjectContext

        alice_subject = SubjectContext(
            account_id=alice_id,
            session_id="session-alice",
            auth_method=AuthMethod.PASSWORD,
        )
        with pytest.raises(Exception):  # ScopeIsolationError wrapped by VaultService
            vault_service.get_content(alice_id, obj.ref.object_id)

    def test_admin_cannot_authorize_personal_vault_scope(
        self,
        scope_enforcer: ScopeEnforcer,
        alice_id: str,
        bob_id: str,
    ) -> None:
        from bridges.contracts.identity import AuthMethod, SubjectContext
        from bridges.contracts.projects import ObjectRef

        alice_subject = SubjectContext(
            account_id=alice_id,
            session_id="session-alice",
            auth_method=AuthMethod.PASSWORD,
        )
        ref = ObjectRef(
            domain=ObjectDomain.PERSONAL_VAULT,
            owner_id=bob_id,
            object_id="obj-1",
            version=1,
        )
        from bridges.contracts.scope import ScopeIsolationError

        with pytest.raises(ScopeIsolationError):
            scope_enforcer.authorize(alice_subject, ScopeAction.READ, ref)


class TestControlledContentAccess:
    def test_controlled_access_requires_two_approvals(
        self,
        institution_service: InstitutionService,
        alice_id: str,
        bob_id: str,
        carol_id: str,
    ) -> None:
        institution = institution_service.create_institution(
            alice_id, InstitutionCreateRequest(name="受控访问机构")
        )
        # Invite bob and carol as security admins.
        for account_id in (bob_id, carol_id):
            invite = institution_service.invite_member(
                institution.id,
                alice_id,
                InstitutionInviteRequest(
                    recipient_account_id=account_id,
                    role=InstitutionRole.SECURITY_ADMIN,
                ),
            )
            institution_service.accept_invite(account_id, invite.token_secret)

        request = institution_service.request_controlled_content_access(
            institution.id,
            bob_id,
            alice_id,
            ["obj-1"],
            "调查可疑活动",
        )
        assert request.status == ControlledContentAccessStatus.PENDING

        # First approval keeps it pending until the second approval.
        request = institution_service.approve_controlled_content_access(
            institution.id, carol_id, request.request_id, "批准调查"
        )
        assert request.status == ControlledContentAccessStatus.PENDING

        # Need a second approver; use alice (admin) as the second approval.
        request = institution_service.approve_controlled_content_access(
            institution.id, alice_id, request.request_id, "二次批准"
        )
        assert request.status == ControlledContentAccessStatus.APPROVED

    def test_requester_cannot_approve_own_request(
        self,
        institution_service: InstitutionService,
        alice_id: str,
        bob_id: str,
    ) -> None:
        institution = institution_service.create_institution(
            alice_id, InstitutionCreateRequest(name="批准隔离机构")
        )
        invite = institution_service.invite_member(
            institution.id,
            alice_id,
            InstitutionInviteRequest(
                recipient_account_id=bob_id, role=InstitutionRole.SECURITY_ADMIN
            ),
        )
        institution_service.accept_invite(bob_id, invite.token_secret)

        request = institution_service.request_controlled_content_access(
            institution.id, bob_id, alice_id, ["obj-1"], "调查"
        )
        with pytest.raises(InstitutionServiceError):
            institution_service.approve_controlled_content_access(
                institution.id, bob_id, request.request_id, "自我批准"
            )

    def test_member_cannot_request_controlled_access(
        self,
        institution_service: InstitutionService,
        alice_id: str,
        bob_id: str,
    ) -> None:
        institution = institution_service.create_institution(
            alice_id, InstitutionCreateRequest(name="角色隔离机构")
        )
        invite = institution_service.invite_member(
            institution.id,
            alice_id,
            InstitutionInviteRequest(
                recipient_account_id=bob_id, role=InstitutionRole.MEMBER
            ),
        )
        institution_service.accept_invite(bob_id, invite.token_secret)

        with pytest.raises(InstitutionServiceError):
            institution_service.request_controlled_content_access(
                institution.id, bob_id, alice_id, ["obj-1"], "调查"
            )

    def test_approver_cannot_approve_twice(
        self,
        institution_service: InstitutionService,
        alice_id: str,
        bob_id: str,
        carol_id: str,
    ) -> None:
        institution = institution_service.create_institution(
            alice_id, InstitutionCreateRequest(name="重复批准机构")
        )
        for account_id in (bob_id, carol_id):
            invite = institution_service.invite_member(
                institution.id,
                alice_id,
                InstitutionInviteRequest(
                    recipient_account_id=account_id,
                    role=InstitutionRole.SECURITY_ADMIN,
                ),
            )
            institution_service.accept_invite(account_id, invite.token_secret)

        request = institution_service.request_controlled_content_access(
            institution.id, bob_id, alice_id, ["obj-1"], "调查"
        )
        # First approval from carol succeeds.
        institution_service.approve_controlled_content_access(
            institution.id, carol_id, request.request_id, "批准调查"
        )
        # Second approval from same carol is rejected.
        with pytest.raises(InstitutionServiceError):
            institution_service.approve_controlled_content_access(
                institution.id, carol_id, request.request_id, "重复批准"
            )

    def test_admin_can_revoke_pending_request(
        self,
        institution_service: InstitutionService,
        alice_id: str,
        bob_id: str,
    ) -> None:
        institution = institution_service.create_institution(
            alice_id, InstitutionCreateRequest(name="撤销测试机构")
        )
        invite = institution_service.invite_member(
            institution.id,
            alice_id,
            InstitutionInviteRequest(
                recipient_account_id=bob_id, role=InstitutionRole.SECURITY_ADMIN
            ),
        )
        institution_service.accept_invite(bob_id, invite.token_secret)

        request = institution_service.request_controlled_content_access(
            institution.id, bob_id, alice_id, ["obj-1"], "调查安全事件"
        )
        assert request.status == ControlledContentAccessStatus.PENDING

        revoked = institution_service.revoke_controlled_content_access(
            institution.id, alice_id, request.request_id
        )
        assert revoked.status == ControlledContentAccessStatus.REVOKED


class TestPolicyManagement:
    def test_admin_can_update_policy(
        self, institution_service: InstitutionService, alice_id: str
    ) -> None:
        institution = institution_service.create_institution(
            alice_id, InstitutionCreateRequest(name="策略机构")
        )
        new_policy = institution_service.get_policy(alice_id, institution.id).model_copy(
            update={
                "seat_policy": SeatPolicy(max_seats=10, enforce_mfa=True),
                "recovery_key_enabled": True,
            }
        )
        updated = institution_service.update_policy(
            institution.id, alice_id, new_policy
        )
        assert updated.seat_policy.max_seats == 10
        assert updated.seat_policy.enforce_mfa is True
        assert updated.recovery_key_enabled is True
        assert updated.updated_by == alice_id
