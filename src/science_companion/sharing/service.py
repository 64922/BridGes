"""Explicit sharing and minimized project copy domain service.

T036: This module implements the sharing service that coordinates share preview,
project copy creation, object grants, invite tokens and permission enforcement.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from science_companion.contracts.identity import SubjectContext
from science_companion.contracts.projects import (
    ObjectDomain,
    Project,
    ProjectRole,
    ProjectStatus,
)
from science_companion.contracts.sharing import (
    GrantPermission,
    InviteAcceptRequest,
    InviteCreateRequest,
    InviteToken,
    InviteTokenStatus,
    ObjectGrant,
    ObjectGrantStatus,
    SharedProjectMember,
    SharedProjectRole,
    ShareExecuteRequest,
    ShareExecuteResult,
    SharePreview,
    SharePreviewField,
    SharePreviewRequest,
)
from science_companion.contracts.vault import (
    DeviceUnavailableState,
    VaultObject,
    VaultShareRequest,
)
from science_companion.identity import IdentityService
from science_companion.scope import ScopeEnforcer
from science_companion.vault import VaultService
from science_companion.vault.adapters import VaultError


class SharingServiceError(Exception):
    """Domain exception for sharing failures.

    The message is safe to expose to callers; it never leaks whether a project,
    object or account exists.
    """


_ROLE_BASELINE: dict[SharedProjectRole, set[GrantPermission]] = {
    SharedProjectRole.OWNER: {
        GrantPermission.VIEW,
        GrantPermission.EDIT,
        GrantPermission.REVIEW,
        GrantPermission.PUBLISH,
    },
    SharedProjectRole.EDITOR: {
        GrantPermission.VIEW,
        GrantPermission.EDIT,
        GrantPermission.REVIEW,
    },
    SharedProjectRole.REVIEWER: {
        GrantPermission.VIEW,
        GrantPermission.REVIEW,
    },
    SharedProjectRole.COMMENTER: {
        GrantPermission.VIEW,
        GrantPermission.REVIEW,
    },
    SharedProjectRole.VIEWER: {GrantPermission.VIEW},
}

# Roles that may share objects into a project or invite new members.
_CAN_ADMINISTER: set[SharedProjectRole] = {
    SharedProjectRole.OWNER,
    SharedProjectRole.EDITOR,
}


@dataclass
class _SharedProject:
    project: Project
    owner_account_id: str


class SharingService:
    """Service for explicit sharing, project copies, grants and invites."""

    def __init__(
        self,
        vault_service: VaultService,
        scope_enforcer: ScopeEnforcer | None = None,
        identity_service: IdentityService | None = None,
    ) -> None:
        self._vault = vault_service
        self._enforcer = scope_enforcer or ScopeEnforcer()
        self._identity = identity_service
        self._projects: dict[str, _SharedProject] = {}
        self._members: dict[tuple[str, str], SharedProjectMember] = {}
        self._grants: dict[str, ObjectGrant] = {}
        self._invite_tokens: dict[str, InviteToken] = {}
        self._invite_secret_to_id: dict[str, str] = {}

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def _subject(self, account_id: str) -> SubjectContext:
        from science_companion.contracts.identity import AuthMethod

        return SubjectContext(
            account_id=account_id,
            session_id="sharing-service",
            auth_method=AuthMethod.PASSWORD,
        )

    def _require_member(
        self, project_id: str, account_id: str
    ) -> SharedProjectMember:
        member = self._members.get((project_id, account_id))
        if member is None:
            raise SharingServiceError("项目不存在或没有访问权限。")
        return member

    def _require_administer_role(
        self, project_id: str, account_id: str
    ) -> SharedProjectMember:
        member = self._require_member(project_id, account_id)
        if member.role not in _CAN_ADMINISTER:
            raise SharingServiceError("当前角色无法执行此操作。")
        return member

    def create_shared_project(
        self,
        account_id: str,
        name: str,
        description: str | None = None,
    ) -> Project:
        """Create a new explicit shared project owned by the account."""
        now = self._now()
        project_id = secrets.token_urlsafe(16)
        project = Project(
            id=project_id,
            account_id=account_id,
            name=name,
            description=description,
            object_domain=ObjectDomain.SHARED_PROJECT,
            role=ProjectRole.OWNER,
            status=ProjectStatus.ACTIVE,
            version=1,
            created_at=now,
            updated_at=now,
            archived_at=None,
        )
        self._projects[project_id] = _SharedProject(
            project=project, owner_account_id=account_id
        )
        self._members[(project_id, account_id)] = SharedProjectMember(
            project_id=project_id,
            account_id=account_id,
            role=SharedProjectRole.OWNER,
            joined_at=now,
            invited_by=None,
        )
        return project

    def list_shared_projects(self, account_id: str) -> list[Project]:
        """Return shared projects where the account is a member."""
        projects: list[Project] = []
        for stored in self._projects.values():
            member = self._members.get((stored.project.id, account_id))
            if member is None:
                continue
            # Return the project with the caller's role rather than the stored
            # owner role used at creation time. Project.role is typed as the
            # API-facing ProjectRole enum, so map from the internal shared role.
            projection = stored.project.model_copy(
                update={"role": ProjectRole(member.role.value)}
            )
            projects.append(projection)
        projects.sort(key=lambda p: p.updated_at, reverse=True)
        return projects

    def get_shared_project(self, account_id: str, project_id: str) -> Project:
        """Return a single shared project projection if the account is a member."""
        stored = self._projects.get(project_id)
        if stored is None:
            raise SharingServiceError("项目不存在或没有访问权限。")
        member = self._require_member(project_id, account_id)
        return stored.project.model_copy(
            update={"role": ProjectRole(member.role.value)}
        )

    def _source_object(self, account_id: str, object_id: str) -> VaultObject:
        """Fetch the source vault object, failing closed on any error."""
        try:
            obj = self._vault.get_object(account_id, object_id)
        except VaultError as exc:
            raise SharingServiceError(str(exc)) from exc
        if isinstance(obj, VaultObject):
            return obj
        raise SharingServiceError("源对象当前不可用。")

    def preview_share(
        self, account_id: str, request: SharePreviewRequest
    ) -> SharePreview:
        """Build a share preview before the user confirms the copy."""
        source = self._source_object(account_id, request.source_object_id)

        # The sharer must be a member of the target shared project.
        self._require_member(request.target_project_id, account_id)

        # Validate that requested permissions fit inside the recipient role.
        baseline = _ROLE_BASELINE.get(request.recipient_role, set())
        if not set(request.permissions).issubset(baseline):
            raise SharingServiceError(
                "请求的权限超出目标角色允许的范围。"
            )

        included: list[SharePreviewField] = [
            SharePreviewField(
                field_name="content",
                included=True,
                reason="按最小化副本复制对象正文。",
            ),
            SharePreviewField(
                field_name="content_length",
                included=True,
                reason="用于副本完整性校验。",
            ),
            SharePreviewField(
                field_name="content_hash",
                included=True,
                reason="用于副本完整性校验。",
            ),
            SharePreviewField(
                field_name="key_epoch",
                included=True,
                reason="副本继承源对象的密钥时期。",
            ),
            SharePreviewField(
                field_name="authorization_version",
                included=True,
                reason="副本继承源对象的授权版本。",
            ),
            SharePreviewField(
                field_name="purpose",
                included=True,
                reason="共享用途写入副本元数据。",
            ),
        ]
        excluded: list[SharePreviewField] = [
            SharePreviewField(
                field_name="device_id",
                included=False,
                reason="设备标识属于个人保险库元数据，不复制到项目。",
            ),
            SharePreviewField(
                field_name="personal_notes",
                included=False,
                reason="画像、学习记录和私人批注不进入共享项目。",
            ),
            SharePreviewField(
                field_name="vault_object_id",
                included=False,
                reason="源对象标识保留在预览中；副本获得新的项目对象标识。",
            ),
        ]

        return SharePreview(
            source_object_id=request.source_object_id,
            source_owner_id=source.owner_account_id,
            target_project_id=request.target_project_id,
            recipient_account_id=request.recipient_account_id,
            grant_purpose=request.grant_purpose,
            included_fields=included,
            excluded_fields=excluded,
            recipient_role=request.recipient_role,
            permissions=list(request.permissions),
            expires_at=request.expires_at,
        )

    def execute_share(
        self, account_id: str, request: ShareExecuteRequest
    ) -> ShareExecuteResult:
        """Create a minimized project copy and an object grant for the recipient."""
        # Validates that the source object exists and is owned by the sharer.
        self._source_object(account_id, request.source_object_id)

        # Only project owners or editors may share objects into the project.
        self._require_administer_role(request.target_project_id, account_id)

        baseline = _ROLE_BASELINE.get(request.recipient_role, set())
        if not set(request.permissions).issubset(baseline):
            raise SharingServiceError(
                "请求的权限超出目标角色允许的范围。"
            )

        # Ensure the recipient is a member of the shared project.
        recipient_member = self._members.get(
            (request.target_project_id, request.recipient_account_id)
        )
        if recipient_member is None:
            now = self._now()
            recipient_member = SharedProjectMember(
                project_id=request.target_project_id,
                account_id=request.recipient_account_id,
                role=request.recipient_role,
                joined_at=now,
                invited_by=account_id,
            )
            self._members[
                (request.target_project_id, request.recipient_account_id)
            ] = recipient_member

        # Create the minimized project copy through the vault service.
        try:
            project_ref = self._vault.share_as_project_copy(
                owner_account_id=account_id,
                request=VaultShareRequest(
                    source_object_id=request.source_object_id,
                    target_project_id=request.target_project_id,
                    grant_purpose=request.grant_purpose,
                ),
            )
        except VaultError as exc:
            raise SharingServiceError(str(exc)) from exc

        # Issue an object-level grant to the recipient.
        grant = self._create_grant(
            project_id=request.target_project_id,
            object_id=project_ref.object_id,
            grantee_account_id=request.recipient_account_id,
            permissions=list(request.permissions),
            granted_by=account_id,
            purpose=request.grant_purpose,
            expires_at=request.expires_at,
        )

        return ShareExecuteResult(
            project_object_id=project_ref.object_id,
            project_object_domain=project_ref.domain.value,
            target_project_id=request.target_project_id,
            source_object_id=request.source_object_id,
            grant_id=grant.grant_id,
            expires_at=request.expires_at,
        )

    def _create_grant(
        self,
        project_id: str,
        object_id: str,
        grantee_account_id: str,
        permissions: list[GrantPermission],
        granted_by: str,
        purpose: str,
        expires_at: datetime | None,
    ) -> ObjectGrant:
        now = self._now()
        grant = ObjectGrant(
            grant_id=secrets.token_urlsafe(16),
            project_id=project_id,
            object_id=object_id,
            grantee_account_id=grantee_account_id,
            permissions=permissions,
            granted_by=granted_by,
            purpose=purpose,
            status=ObjectGrantStatus.ACTIVE,
            created_at=now,
            expires_at=expires_at,
        )
        self._grants[grant.grant_id] = grant
        return grant

    def create_invite(
        self, account_id: str, request: InviteCreateRequest
    ) -> InviteToken:
        """Create a short-lived, single-use invite token bound to project/identity."""
        self._require_administer_role(request.project_id, account_id)

        if (
            self._identity is not None
            and self._identity.get_account(request.recipient_account_id) is None
        ):
            raise SharingServiceError("受邀账户不存在。")

        now = self._now()
        token_id = secrets.token_urlsafe(16)
        token_secret = secrets.token_urlsafe(32)
        token = InviteToken(
            token_id=token_id,
            token_secret=token_secret,
            project_id=request.project_id,
            invited_by=account_id,
            expected_recipient_id=request.recipient_account_id,
            role=request.role,
            status=InviteTokenStatus.PENDING,
            created_at=now,
            expires_at=now + timedelta(seconds=request.ttl_seconds),
            single_use=True,
        )
        self._invite_tokens[token_id] = token
        self._invite_secret_to_id[token_secret] = token_id
        return token

    def accept_invite(
        self, account_id: str, request: InviteAcceptRequest
    ) -> SharedProjectMember:
        """Accept an invite token and add the recipient to the project."""
        token_id = self._invite_secret_to_id.get(request.token_secret)
        if token_id is None:
            raise SharingServiceError("邀请不存在或无效。")
        token = self._invite_tokens.get(token_id)
        if token is None:
            raise SharingServiceError("邀请不存在或无效。")

        if token.expected_recipient_id != account_id:
            raise SharingServiceError("邀请与当前账户不匹配。")
        if token.status != InviteTokenStatus.PENDING:
            raise SharingServiceError("邀请已被使用或撤销。")
        if token.expires_at <= self._now():
            token.status = InviteTokenStatus.EXPIRED
            raise SharingServiceError("邀请已过期。")

        token.status = InviteTokenStatus.ACCEPTED
        token.accepted_at = self._now()

        member = self._members.get((token.project_id, account_id))
        if member is None:
            member = SharedProjectMember(
                project_id=token.project_id,
                account_id=account_id,
                role=token.role,
                joined_at=self._now(),
                invited_by=token.invited_by,
            )
            self._members[(token.project_id, account_id)] = member
        return member

    def get_shared_object(
        self, project_id: str, account_id: str, object_id: str
    ) -> VaultObject:
        """Return a project copy object projection if the account has a grant."""
        self._require_member(project_id, account_id)
        if not self.check_object_permission(
            project_id, account_id, object_id, GrantPermission.VIEW
        ):
            raise SharingServiceError("对象不存在或没有访问权限。")

        try:
            obj = self._vault.get_object(project_id, object_id)
        except VaultError as exc:
            raise SharingServiceError(str(exc)) from exc
        if isinstance(obj, DeviceUnavailableState):
            raise SharingServiceError("对象当前不可用。")
        return obj

    def get_shared_object_content(
        self, project_id: str, account_id: str, object_id: str
    ) -> bytes:
        """Return the plaintext of a project copy object if authorized."""
        self._require_member(project_id, account_id)
        if not self.check_object_permission(
            project_id, account_id, object_id, GrantPermission.VIEW
        ):
            raise SharingServiceError("对象不存在或没有访问权限。")

        try:
            content = self._vault.get_content(project_id, object_id)
        except VaultError as exc:
            raise SharingServiceError(str(exc)) from exc
        if isinstance(content, DeviceUnavailableState):
            raise SharingServiceError("对象当前不可用。")
        return content

    def check_object_permission(
        self,
        project_id: str,
        account_id: str,
        object_id: str,
        permission: GrantPermission,
    ) -> bool:
        """Check whether the account holds a permission on a project object.

        Effective permission is the intersection of project role baseline and
        active object grant permissions. The project owner has implicit access
        to all project objects within their role baseline — no explicit grant
        needed. Non-owner roles always require an object grant to access a
        specific project object.
        """
        member = self._members.get((project_id, account_id))
        if member is None:
            return False
        baseline = _ROLE_BASELINE.get(member.role, set())
        if permission not in baseline:
            return False

        # Project owner has implicit access to all project objects within
        # their role baseline — no explicit grant needed.  Non-owner roles
        # (editor, reviewer, commenter, viewer) require explicit object grants
        # for specific object access.
        if member.role == SharedProjectRole.OWNER:
            return True

        now = self._now()
        for grant in self._grants.values():
            if grant.project_id != project_id:
                continue
            if grant.object_id != object_id:
                continue
            if grant.grantee_account_id != account_id:
                continue
            if grant.status != ObjectGrantStatus.ACTIVE:
                continue
            if grant.expires_at is not None and grant.expires_at <= now:
                continue
            if permission in grant.permissions:
                return True
        return False

    def list_project_members(
        self, project_id: str, account_id: str
    ) -> list[SharedProjectMember]:
        """List members of a shared project."""
        self._require_member(project_id, account_id)
        return [
            member
            for (proj_id, _), member in self._members.items()
            if proj_id == project_id
        ]

    def list_object_grants(
        self, project_id: str, account_id: str
    ) -> list[ObjectGrant]:
        """List object grants visible to the current member.

        Members see grants for objects they can access; owners and editors see
        all project grants.
        """
        member = self._require_member(project_id, account_id)
        can_admin = member.role in _CAN_ADMINISTER
        grants: list[ObjectGrant] = []
        for grant in self._grants.values():
            if grant.project_id != project_id:
                continue
            if can_admin or grant.grantee_account_id == account_id:
                grants.append(grant)
        return grants

    def revoke_grant(self, account_id: str, grant_id: str) -> ObjectGrant:
        """Revoke an object grant.

        Only project owners, editors who created the grant, or the project owner
        may revoke a grant.
        """
        grant = self._grants.get(grant_id)
        if grant is None:
            raise SharingServiceError("授权不存在或没有访问权限。")

        member = self._require_member(grant.project_id, account_id)
        if member.role not in _CAN_ADMINISTER and grant.granted_by != account_id:
            raise SharingServiceError("当前角色无法撤销此授权。")

        if grant.status != ObjectGrantStatus.ACTIVE:
            raise SharingServiceError("授权已失效。")

        grant.status = ObjectGrantStatus.REVOKED
        grant.revoked_at = self._now()
        return grant
