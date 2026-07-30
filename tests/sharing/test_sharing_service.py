"""Module-interface tests for the explicit sharing service.

The seam under test: a user can preview a share, create an independent minimized
project copy, grant object-level permissions to collaborators, and issue short-lived
single-use invite tokens bound to a project and an expected identity.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from science_companion.contracts.projects import ObjectDomain
from science_companion.contracts.sharing import (
    GrantPermission,
    InviteAcceptRequest,
    InviteCreateRequest,
    InviteTokenStatus,
    SharedProjectRole,
    ShareExecuteRequest,
    SharePreviewRequest,
)
from science_companion.sharing import SharingService, SharingServiceError
from science_companion.vault import (
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
def alice_id() -> str:
    return "account-alice"


@pytest.fixture
def bob_id() -> str:
    return "account-bob"


def _create_source_object(
    vault_service: VaultService, account_id: str, content: bytes
) -> str:
    obj = vault_service.create_private_object(
        owner_account_id=account_id,
        content=content,
        content_authority="server_replica",
        purpose="research_draft",
    )
    return obj.ref.object_id


class TestSharedProjectCreation:
    def test_create_shared_project_returns_shared_domain(
        self, sharing_service: SharingService, alice_id: str
    ) -> None:
        project = sharing_service.create_shared_project(
            account_id=alice_id, name="协作项目"
        )

        assert project.name == "协作项目"
        assert project.object_domain == ObjectDomain.SHARED_PROJECT
        assert project.account_id == alice_id
        assert project.role == SharedProjectRole.OWNER

    def test_list_shared_projects_includes_membership(
        self, sharing_service: SharingService, alice_id: str, bob_id: str
    ) -> None:
        project = sharing_service.create_shared_project(
            account_id=alice_id, name="成员列表项目"
        )

        alice_list = sharing_service.list_shared_projects(alice_id)
        assert len(alice_list) == 1
        assert alice_list[0].id == project.id

        bob_list = sharing_service.list_shared_projects(bob_id)
        assert len(bob_list) == 0


class TestSharePreview:
    def test_preview_shows_included_excluded_fields_and_independence(
        self,
        sharing_service: SharingService,
        vault_service: VaultService,
        alice_id: str,
        bob_id: str,
    ) -> None:
        project = sharing_service.create_shared_project(
            account_id=alice_id, name="预览项目"
        )
        object_id = _create_source_object(
            vault_service, alice_id, b"Draft paragraph"
        )

        preview = sharing_service.preview_share(
            account_id=alice_id,
            request=SharePreviewRequest(
                source_object_id=object_id,
                target_project_id=project.id,
                grant_purpose="peer_review",
                recipient_account_id=bob_id,
                recipient_role=SharedProjectRole.VIEWER,
            ),
        )

        assert preview.source_object_id == object_id
        assert preview.source_owner_id == alice_id
        assert preview.target_project_id == project.id
        assert preview.recipient_account_id == bob_id
        assert preview.grant_purpose == "peer_review"
        assert preview.independence_note
        assert SharedProjectRole.VIEWER in preview.recipient_role
        included_names = {f.field_name for f in preview.included_fields}
        excluded_names = {f.field_name for f in preview.excluded_fields}
        assert "content_length" in included_names
        assert "content_hash" in included_names
        assert "device_id" in excluded_names

    def test_preview_for_non_member_is_rejected(
        self,
        sharing_service: SharingService,
        vault_service: VaultService,
        alice_id: str,
        bob_id: str,
    ) -> None:
        project = sharing_service.create_shared_project(
            account_id=alice_id, name="私有共享项目"
        )
        object_id = _create_source_object(vault_service, alice_id, b"X")

        with pytest.raises(SharingServiceError):
            sharing_service.preview_share(
                account_id=bob_id,
                request=SharePreviewRequest(
                    source_object_id=object_id,
                    target_project_id=project.id,
                    grant_purpose="peer_review",
                    recipient_account_id=bob_id,
                ),
            )


class TestShareExecution:
    def test_execute_share_creates_independent_project_copy(
        self,
        sharing_service: SharingService,
        vault_service: VaultService,
        alice_id: str,
        bob_id: str,
    ) -> None:
        project = sharing_service.create_shared_project(
            account_id=alice_id, name="分享项目"
        )
        object_id = _create_source_object(vault_service, alice_id, b"Original")

        result = sharing_service.execute_share(
            account_id=alice_id,
            request=ShareExecuteRequest(
                source_object_id=object_id,
                target_project_id=project.id,
                grant_purpose="peer_review",
                recipient_account_id=bob_id,
                recipient_role=SharedProjectRole.VIEWER,
                permissions=[GrantPermission.VIEW],
            ),
        )

        assert result.target_project_id == project.id
        assert result.source_object_id == object_id
        assert result.project_object_domain == ObjectDomain.SHARED_PROJECT.value
        assert result.project_object_id != object_id
        assert result.grant_id
        assert result.independence_note
        assert result.expires_at is None

    def test_execute_share_with_expiry_echoes_expires_at(
        self,
        sharing_service: SharingService,
        vault_service: VaultService,
        alice_id: str,
        bob_id: str,
    ) -> None:
        from datetime import timedelta, datetime, UTC

        project = sharing_service.create_shared_project(
            account_id=alice_id, name="期限回显项目"
        )
        object_id = _create_source_object(vault_service, alice_id, b"Timebound")
        expires_at = datetime.now(UTC) + timedelta(days=7)

        result = sharing_service.execute_share(
            account_id=alice_id,
            request=ShareExecuteRequest(
                source_object_id=object_id,
                target_project_id=project.id,
                grant_purpose="timebound_review",
                recipient_account_id=bob_id,
                expires_at=expires_at,
            ),
        )

        assert result.expires_at is not None
        # Compare with tolerance for serialization round-trip.
        assert abs((result.expires_at - expires_at).total_seconds()) < 1.0

    def test_owner_has_implicit_access_to_shared_object(
        self,
        sharing_service: SharingService,
        vault_service: VaultService,
        alice_id: str,
        bob_id: str,
    ) -> None:
        project = sharing_service.create_shared_project(
            account_id=alice_id, name="所有者权限项目"
        )
        object_id = _create_source_object(vault_service, alice_id, b"Owner content")

        result = sharing_service.execute_share(
            account_id=alice_id,
            request=ShareExecuteRequest(
                source_object_id=object_id,
                target_project_id=project.id,
                grant_purpose="peer_review",
                recipient_account_id=bob_id,
                permissions=[GrantPermission.VIEW],
            ),
        )

        # The owner (Alice) can read the shared object without an explicit grant.
        copy = sharing_service.get_shared_object(
            project_id=project.id,
            account_id=alice_id,
            object_id=result.project_object_id,
        )
        assert copy.ref.domain.value == ObjectDomain.SHARED_PROJECT.value

        content = sharing_service.get_shared_object_content(
            project_id=project.id,
            account_id=alice_id,
            object_id=result.project_object_id,
        )
        assert content == b"Owner content"

    def test_shared_project_copy_is_in_project_domain(
        self,
        sharing_service: SharingService,
        vault_service: VaultService,
        alice_id: str,
        bob_id: str,
    ) -> None:
        project = sharing_service.create_shared_project(
            account_id=alice_id, name="域项目"
        )
        object_id = _create_source_object(vault_service, alice_id, b"Original")

        result = sharing_service.execute_share(
            account_id=alice_id,
            request=ShareExecuteRequest(
                source_object_id=object_id,
                target_project_id=project.id,
                grant_purpose="peer_review",
                recipient_account_id=bob_id,
            ),
        )

        copy = sharing_service.get_shared_object(
            project_id=project.id,
            account_id=bob_id,
            object_id=result.project_object_id,
        )
        assert copy.ref.domain.value == ObjectDomain.SHARED_PROJECT.value
        assert copy.ref.owner_id == project.id
        assert copy.content_authority.value == "project_copy"

    def test_recipient_can_read_content_with_view_grant(
        self,
        sharing_service: SharingService,
        vault_service: VaultService,
        alice_id: str,
        bob_id: str,
    ) -> None:
        project = sharing_service.create_shared_project(
            account_id=alice_id, name="可读项目"
        )
        content = b"Shared content"
        object_id = _create_source_object(vault_service, alice_id, content)

        result = sharing_service.execute_share(
            account_id=alice_id,
            request=ShareExecuteRequest(
                source_object_id=object_id,
                target_project_id=project.id,
                grant_purpose="peer_review",
                recipient_account_id=bob_id,
                permissions=[GrantPermission.VIEW],
            ),
        )

        fetched = sharing_service.get_shared_object_content(
            project_id=project.id,
            account_id=bob_id,
            object_id=result.project_object_id,
        )
        assert fetched == content

    def test_member_without_grant_cannot_read_object(
        self,
        sharing_service: SharingService,
        vault_service: VaultService,
        alice_id: str,
        bob_id: str,
    ) -> None:
        project = sharing_service.create_shared_project(
            account_id=alice_id, name="无授权项目"
        )
        object_id = _create_source_object(vault_service, alice_id, b"Secret")
        result = sharing_service.execute_share(
            account_id=alice_id,
            request=ShareExecuteRequest(
                source_object_id=object_id,
                target_project_id=project.id,
                grant_purpose="peer_review",
                recipient_account_id=bob_id,
                permissions=[GrantPermission.VIEW],
            ),
        )

        sharing_service.revoke_grant(account_id=alice_id, grant_id=result.grant_id)

        with pytest.raises(SharingServiceError):
            sharing_service.get_shared_object_content(
                project_id=project.id,
                account_id=bob_id,
                object_id=result.project_object_id,
            )

    def test_grant_permissions_limited_by_role_baseline(
        self,
        sharing_service: SharingService,
        vault_service: VaultService,
        alice_id: str,
        bob_id: str,
    ) -> None:
        project = sharing_service.create_shared_project(
            account_id=alice_id, name="权限基线项目"
        )
        object_id = _create_source_object(vault_service, alice_id, b"Draft")

        # A viewer cannot receive an edit grant.
        with pytest.raises(SharingServiceError):
            sharing_service.execute_share(
                account_id=alice_id,
                request=ShareExecuteRequest(
                    source_object_id=object_id,
                    target_project_id=project.id,
                    grant_purpose="peer_review",
                    recipient_account_id=bob_id,
                    recipient_role=SharedProjectRole.VIEWER,
                    permissions=[GrantPermission.EDIT],
                ),
            )


class TestInviteTokens:
    def test_invite_token_is_short_lived_single_use_and_bound(
        self,
        sharing_service: SharingService,
        alice_id: str,
        bob_id: str,
    ) -> None:
        project = sharing_service.create_shared_project(
            account_id=alice_id, name="邀请项目"
        )

        token = sharing_service.create_invite(
            account_id=alice_id,
            request=InviteCreateRequest(
                project_id=project.id,
                recipient_account_id=bob_id,
                role=SharedProjectRole.EDITOR,
                ttl_seconds=300,
            ),
        )

        assert token.project_id == project.id
        assert token.expected_recipient_id == bob_id
        assert token.role == SharedProjectRole.EDITOR
        assert token.single_use is True
        assert token.expires_at <= datetime.now(UTC) + timedelta(seconds=300)
        assert token.status == InviteTokenStatus.PENDING

    def test_accept_invite_adds_member_and_consumes_token(
        self,
        sharing_service: SharingService,
        alice_id: str,
        bob_id: str,
    ) -> None:
        project = sharing_service.create_shared_project(
            account_id=alice_id, name="接受邀请项目"
        )
        token = sharing_service.create_invite(
            account_id=alice_id,
            request=InviteCreateRequest(
                project_id=project.id,
                recipient_account_id=bob_id,
                role=SharedProjectRole.EDITOR,
            ),
        )

        member = sharing_service.accept_invite(
            account_id=bob_id,
            request=InviteAcceptRequest(token_secret=token.token_secret),
        )

        assert member.project_id == project.id
        assert member.account_id == bob_id
        assert member.role == SharedProjectRole.EDITOR

        members = sharing_service.list_project_members(project.id, account_id=bob_id)
        assert any(m.account_id == bob_id for m in members)

    def test_accept_wrong_recipient_is_rejected(
        self,
        sharing_service: SharingService,
        alice_id: str,
        bob_id: str,
    ) -> None:
        project = sharing_service.create_shared_project(
            account_id=alice_id, name="绑定身份项目"
        )
        token = sharing_service.create_invite(
            account_id=alice_id,
            request=InviteCreateRequest(
                project_id=project.id,
                recipient_account_id=bob_id,
            ),
        )

        with pytest.raises(SharingServiceError):
            sharing_service.accept_invite(
                account_id="account-eve",
                request=InviteAcceptRequest(token_secret=token.token_secret),
            )

    def test_accept_same_token_twice_is_rejected(
        self,
        sharing_service: SharingService,
        alice_id: str,
        bob_id: str,
    ) -> None:
        project = sharing_service.create_shared_project(
            account_id=alice_id, name="单用令牌项目"
        )
        token = sharing_service.create_invite(
            account_id=alice_id,
            request=InviteCreateRequest(
                project_id=project.id,
                recipient_account_id=bob_id,
            ),
        )

        sharing_service.accept_invite(
            account_id=bob_id,
            request=InviteAcceptRequest(token_secret=token.token_secret),
        )
        with pytest.raises(SharingServiceError):
            sharing_service.accept_invite(
                account_id=bob_id,
                request=InviteAcceptRequest(token_secret=token.token_secret),
            )

    def test_expired_invite_is_rejected(
        self,
        sharing_service: SharingService,
        alice_id: str,
        bob_id: str,
    ) -> None:
        project = sharing_service.create_shared_project(
            account_id=alice_id, name="过期令牌项目"
        )
        token = sharing_service.create_invite(
            account_id=alice_id,
            request=InviteCreateRequest(
                project_id=project.id,
                recipient_account_id=bob_id,
                ttl_seconds=60,
            ),
        )

        # Simulate expiration by advancing the service clock would be ideal; here
        # we manipulate the stored token directly to keep the test focused.
        for stored in sharing_service._invite_tokens.values():
            if stored.token_id == token.token_id:
                stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)

        with pytest.raises(SharingServiceError):
            sharing_service.accept_invite(
                account_id=bob_id,
                request=InviteAcceptRequest(token_secret=token.token_secret),
            )


class TestPermissionEnforcement:
    def test_role_and_grant_jointly_determine_access(
        self,
        sharing_service: SharingService,
        vault_service: VaultService,
        alice_id: str,
        bob_id: str,
    ) -> None:
        project = sharing_service.create_shared_project(
            account_id=alice_id, name="联合权限项目"
        )
        object_id = _create_source_object(vault_service, alice_id, b"Draft")

        # Add Bob as editor but only grant VIEW on this object.
        sharing_service.execute_share(
            account_id=alice_id,
            request=ShareExecuteRequest(
                source_object_id=object_id,
                target_project_id=project.id,
                grant_purpose="limited_review",
                recipient_account_id=bob_id,
                recipient_role=SharedProjectRole.EDITOR,
                permissions=[GrantPermission.VIEW],
            ),
        )

        assert sharing_service.check_object_permission(
            project_id=project.id,
            account_id=bob_id,
            object_id=sharing_service.list_object_grants(project.id, bob_id)[0].object_id,
            permission=GrantPermission.VIEW,
        )
        assert not sharing_service.check_object_permission(
            project_id=project.id,
            account_id=bob_id,
            object_id=sharing_service.list_object_grants(project.id, bob_id)[0].object_id,
            permission=GrantPermission.EDIT,
        )
