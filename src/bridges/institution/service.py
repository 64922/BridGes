"""Institution management domain service.

T037: This module implements the institution management domain: institutions,
memberships, policies, institution-owned projects and controlled content access.
It keeps the personal vault boundary intact by default and requires explicit,
two-approval, time-limited access for security incidents.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from bridges.contracts.institution import (
    ControlledContentAccessApproval,
    ControlledContentAccessEvent,
    ControlledContentAccessRequest,
    ControlledContentAccessStatus,
    Institution,
    InstitutionCreateRequest,
    InstitutionDomain,
    InstitutionInviteRequest,
    InstitutionMemberUpdateRequest,
    InstitutionOwnedProjectDisclosure,
    InstitutionPolicy,
    InstitutionProjectCreateRequest,
    InstitutionProjectCreateResponse,
    InstitutionRole,
    InstitutionStatus,
    Membership,
    SeatPolicy,
)
from bridges.contracts.projects import ObjectDomain, Project, ProjectRole
from bridges.contracts.sharing import SharedProjectRole

if TYPE_CHECKING:
    from bridges.sharing import SharingService


class InstitutionServiceError(Exception):
    """Domain exception for institution management failures.

    The message is safe to expose to callers; it never leaks whether an
    institution, account or object exists.
    """


@dataclass
class _StoredInstitution:
    institution: Institution
    policy: InstitutionPolicy


class InstitutionService:
    """In-memory institution management service for T037.

    The interface intentionally mirrors the eventual database-backed adapter so
    that later tickets can swap the implementation without changing callers.
    """

    def __init__(self) -> None:
        self._institutions: dict[str, _StoredInstitution] = {}
        self._memberships: dict[tuple[str, str], Membership] = {}
        self._invites: dict[str, _InstitutionInvite] = {}
        self._invite_secret_to_id: dict[str, str] = {}
        self._controlled_access: dict[str, ControlledContentAccessRequest] = {}
        self._sharing: SharingService | None = None

    def bind_sharing_service(self, sharing_service: SharingService) -> None:
        """Bind the sharing service used to create institution-owned projects."""
        self._sharing = sharing_service

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def _require_institution(self, institution_id: str) -> _StoredInstitution:
        stored = self._institutions.get(institution_id)
        if stored is None:
            raise InstitutionServiceError("机构不存在或没有访问权限。")
        return stored

    def _require_membership(
        self, institution_id: str, account_id: str
    ) -> Membership:
        membership = self._memberships.get((institution_id, account_id))
        if membership is None:
            raise InstitutionServiceError("机构不存在或没有访问权限。")
        return membership

    def _require_admin(self, institution_id: str, account_id: str) -> Membership:
        membership = self._require_membership(institution_id, account_id)
        if membership.role != InstitutionRole.ADMIN:
            raise InstitutionServiceError("当前角色无法执行此操作。")
        return membership

    def _require_admin_or_security_admin(
        self, institution_id: str, account_id: str
    ) -> Membership:
        membership = self._require_membership(institution_id, account_id)
        if membership.role not in {
            InstitutionRole.ADMIN,
            InstitutionRole.SECURITY_ADMIN,
        }:
            raise InstitutionServiceError("当前角色无法执行此操作。")
        return membership

    def create_institution(
        self, admin_account_id: str, request: InstitutionCreateRequest
    ) -> Institution:
        """Create a new institution and make the creator the first admin."""
        now = self._now()
        institution_id = secrets.token_urlsafe(16)
        institution = Institution(
            id=institution_id,
            name=request.name,
            domain=InstitutionDomain.INSTITUTION,
            status=InstitutionStatus.ACTIVE,
            owner_account_id=admin_account_id,
            created_at=now,
            updated_at=now,
        )
        policy = InstitutionPolicy(
            seat_policy=SeatPolicy(),
            updated_at=now,
            updated_by=admin_account_id,
        )
        self._institutions[institution_id] = _StoredInstitution(
            institution=institution, policy=policy
        )
        self._memberships[(institution_id, admin_account_id)] = Membership(
            institution_id=institution_id,
            account_id=admin_account_id,
            role=InstitutionRole.ADMIN,
            joined_at=now,
            invited_by=None,
        )
        return institution

    def get_institution(
        self, account_id: str, institution_id: str
    ) -> Institution:
        """Return an institution projection if the account is a member."""
        stored = self._require_institution(institution_id)
        self._require_membership(institution_id, account_id)
        return stored.institution

    def list_institutions_for_account(
        self, account_id: str
    ) -> list[Institution]:
        """Return all institutions the account is a member of."""
        institution_ids = {
            institution_id
            for (institution_id, member_account_id) in self._memberships
            if member_account_id == account_id
        }
        institutions = [
            self._institutions[iid].institution for iid in institution_ids
        ]
        institutions.sort(key=lambda i: i.created_at, reverse=True)
        return institutions

    def invite_member(
        self,
        institution_id: str,
        inviter_account_id: str,
        request: InstitutionInviteRequest,
    ) -> _InstitutionInvite:
        """Create a short-lived, single-use invite token for an institution."""
        self._require_admin(institution_id, inviter_account_id)

        stored = self._require_institution(institution_id)
        policy = stored.policy
        if (
            policy.seat_policy.max_seats is not None
            and len(self.list_members(inviter_account_id, institution_id))
            >= policy.seat_policy.max_seats
        ):
            raise InstitutionServiceError("机构席位已满。")

        now = self._now()
        token_id = secrets.token_urlsafe(16)
        token_secret = secrets.token_urlsafe(32)
        invite = _InstitutionInvite(
            token_id=token_id,
            token_secret=token_secret,
            institution_id=institution_id,
            invited_by=inviter_account_id,
            expected_recipient_id=request.recipient_account_id,
            role=request.role,
            created_at=now,
            expires_at=now + timedelta(seconds=request.ttl_seconds),
        )
        self._invites[token_id] = invite
        self._invite_secret_to_id[token_secret] = token_id
        return invite

    def accept_invite(
        self, account_id: str, token_secret: str
    ) -> Membership:
        """Accept an institution invite and join the institution."""
        token_id = self._invite_secret_to_id.get(token_secret)
        if token_id is None:
            raise InstitutionServiceError("邀请不存在或无效。")
        invite = self._invites.get(token_id)
        if invite is None:
            raise InstitutionServiceError("邀请不存在或无效。")
        if invite.expected_recipient_id != account_id:
            raise InstitutionServiceError("邀请与当前账户不匹配。")
        if invite.expires_at <= self._now():
            raise InstitutionServiceError("邀请已过期。")
        if invite.accepted_at is not None:
            raise InstitutionServiceError("邀请已被使用。")

        invite.accepted_at = self._now()
        membership = Membership(
            institution_id=invite.institution_id,
            account_id=account_id,
            role=invite.role,
            joined_at=self._now(),
            invited_by=invite.invited_by,
        )
        self._memberships[(invite.institution_id, account_id)] = membership
        return membership

    def list_members(
        self, account_id: str, institution_id: str
    ) -> list[Membership]:
        """List members of an institution."""
        self._require_membership(institution_id, account_id)
        return [
            membership
            for (iid, _), membership in self._memberships.items()
            if iid == institution_id
        ]

    def update_member_role(
        self,
        institution_id: str,
        admin_account_id: str,
        target_account_id: str,
        request: InstitutionMemberUpdateRequest,
    ) -> Membership:
        """Update an institution member's role."""
        self._require_admin(institution_id, admin_account_id)
        if target_account_id == admin_account_id:
            raise InstitutionServiceError("不能修改自己的角色。")
        membership = self._require_membership(institution_id, target_account_id)
        membership.role = request.role
        return membership

    def remove_member(
        self,
        institution_id: str,
        admin_account_id: str,
        target_account_id: str,
    ) -> Membership:
        """Remove a member from the institution.

        Removal stops new access but does not touch the member's personal vault.
        """
        self._require_admin(institution_id, admin_account_id)
        if target_account_id == admin_account_id:
            raise InstitutionServiceError("不能移除自己。")
        membership = self._require_membership(institution_id, target_account_id)
        del self._memberships[(institution_id, target_account_id)]
        # Revoke any active institution project grants is handled by callers
        # through the sharing service; we return the removed membership for
        # audit and propagation here.
        return membership

    def get_policy(
        self, account_id: str, institution_id: str
    ) -> InstitutionPolicy:
        """Return the institution policy for members."""
        self._require_membership(institution_id, account_id)
        stored = self._require_institution(institution_id)
        return stored.policy

    def update_policy(
        self,
        institution_id: str,
        admin_account_id: str,
        policy: InstitutionPolicy,
    ) -> InstitutionPolicy:
        """Update the institution policy."""
        self._require_admin(institution_id, admin_account_id)
        stored = self._require_institution(institution_id)
        new_policy = policy.model_copy(
            update={"updated_at": self._now(), "updated_by": admin_account_id}
        )
        stored.policy = new_policy
        return new_policy

    def get_membership(
        self, institution_id: str, account_id: str
    ) -> Membership | None:
        """Return membership without raising; used by scope enforcer."""
        return self._memberships.get((institution_id, account_id))

    def list_memberships_for_account(
        self, account_id: str
    ) -> list[Membership]:
        """Return all memberships for an account; used by API subject resolver."""
        return [
            membership
            for (iid, aid), membership in self._memberships.items()
            if aid == account_id
        ]

    def build_project_disclosure(
        self, institution_id: str
    ) -> InstitutionOwnedProjectDisclosure:
        """Build the disclosure shown before creating an institution-owned project."""
        stored = self._require_institution(institution_id)
        institution = stored.institution
        policy = stored.policy
        recovery_enabled = policy.recovery_key_enabled
        recovery_statement = (
            "机构已启用恢复密钥；在项目负责人无法访问时可按策略恢复。"
            if recovery_enabled
            else "机构未启用恢复密钥；项目负责人可决定冻结或迁移。"
        )
        return InstitutionOwnedProjectDisclosure(
            institution_id=institution.id,
            institution_name=institution.name,
            recovery_key_enabled=recovery_enabled,
            recovery_statement=recovery_statement,
        )

    def create_institution_owned_project(
        self,
        admin_account_id: str,
        institution_id: str,
        request: InstitutionProjectCreateRequest,
    ) -> InstitutionProjectCreateResponse:
        """Create an institution-owned project after disclosure acknowledgement."""
        self._require_admin(institution_id, admin_account_id)
        if not request.disclosure_acknowledged:
            raise InstitutionServiceError("必须先确认机构项目所有权披露。")
        if self._sharing is None:
            raise InstitutionServiceError("共享服务未绑定，无法创建机构项目。")

        disclosure = self.build_project_disclosure(institution_id)
        disclosure = disclosure.model_copy(update={"acknowledged": True})
        project = self._sharing.create_shared_project(
            account_id=admin_account_id,
            name=request.name,
            description=request.description,
            institution_id=institution_id,
        )
        return InstitutionProjectCreateResponse(
            project=project,
            disclosure=disclosure,
        )

    def list_institution_projects(
        self, account_id: str, institution_id: str
    ) -> list[Project]:
        """List institution-owned projects visible to the account.

        Institution-owned projects are visible to all institution members; the
        caller's project-level role is resolved separately when accessing objects.
        """
        membership = self._require_membership(institution_id, account_id)
        if self._sharing is None:
            return []
        projects = self._sharing.list_projects_by_institution(institution_id)
        # Return projections with the caller's institution role mapped to the
        # API-facing ProjectRole for consistency with shared project listings.
        role_mapping = {
            InstitutionRole.ADMIN: ProjectRole.OWNER,
            InstitutionRole.SECURITY_ADMIN: ProjectRole.REVIEWER,
            InstitutionRole.BILLING_ADMIN: ProjectRole.VIEWER,
            InstitutionRole.MEMBER: ProjectRole.VIEWER,
        }
        mapped_role = role_mapping.get(membership.role, ProjectRole.VIEWER)
        return [
            project.model_copy(update={"role": mapped_role})
            for project in projects
        ]

    def request_controlled_content_access(
        self,
        institution_id: str,
        requester_account_id: str,
        target_account_id: str,
        object_refs: list[str],
        purpose: str,
        duration_minutes: int = 60,
    ) -> ControlledContentAccessRequest:
        """Request controlled access to member content for a security incident."""
        self._require_admin_or_security_admin(institution_id, requester_account_id)

        if target_account_id == requester_account_id:
            raise InstitutionServiceError("不能对自己申请受控正文访问。")

        if not object_refs:
            raise InstitutionServiceError("必须指定访问对象。")

        now = self._now()
        access_request = ControlledContentAccessRequest(
            request_id=secrets.token_urlsafe(16),
            institution_id=institution_id,
            requester_account_id=requester_account_id,
            target_account_id=target_account_id,
            object_refs=list(object_refs),
            purpose=purpose,
            status=ControlledContentAccessStatus.PENDING,
            requested_at=now,
            expires_at=now + timedelta(minutes=duration_minutes),
        )
        self._controlled_access[access_request.request_id] = access_request
        return access_request

    def approve_controlled_content_access(
        self,
        institution_id: str,
        approver_account_id: str,
        request_id: str,
        reason: str,
    ) -> ControlledContentAccessRequest:
        """Second-approval for a controlled content access request."""
        self._require_admin_or_security_admin(institution_id, approver_account_id)

        access_request = self._controlled_access.get(request_id)
        if access_request is None or access_request.institution_id != institution_id:
            raise InstitutionServiceError("访问申请不存在。")
        if access_request.requester_account_id == approver_account_id:
            raise InstitutionServiceError("申请人不能批准自己的申请。")
        if any(
            a.approver_account_id == approver_account_id
            for a in access_request.approvals
        ):
            raise InstitutionServiceError("不能重复批准。")
        if access_request.status != ControlledContentAccessStatus.PENDING:
            raise InstitutionServiceError("访问申请状态不允许批准。")
        if access_request.expires_at <= self._now():
            access_request.status = ControlledContentAccessStatus.EXPIRED
            raise InstitutionServiceError("访问申请已过期。")

        access_request.approvals.append(
            ControlledContentAccessApproval(
                approver_account_id=approver_account_id,
                approved_at=self._now(),
                reason=reason,
            )
        )
        if len(access_request.approvals) >= 2:
            access_request.status = ControlledContentAccessStatus.APPROVED
        return access_request

    def revoke_controlled_content_access(
        self,
        institution_id: str,
        admin_account_id: str,
        request_id: str,
    ) -> ControlledContentAccessRequest:
        """Revoke an approved or pending controlled content access request."""
        self._require_admin(institution_id, admin_account_id)
        access_request = self._controlled_access.get(request_id)
        if access_request is None or access_request.institution_id != institution_id:
            raise InstitutionServiceError("访问申请不存在。")
        if access_request.status in {
            ControlledContentAccessStatus.COMPLETED,
            ControlledContentAccessStatus.EXPIRED,
        }:
            raise InstitutionServiceError("访问申请已结束。")
        access_request.status = ControlledContentAccessStatus.REVOKED
        return access_request

    def log_controlled_content_access(
        self,
        institution_id: str,
        actor_account_id: str,
        request_id: str,
        object_ref: str,
        action: str,
        reason: str,
    ) -> ControlledContentAccessRequest:
        """Log an actual content access under an approved request.

        This is intended to be called by the vault or sharing layer when an
        administrator exercises an approved controlled access grant.
        """
        self._require_membership(institution_id, actor_account_id)
        access_request = self._controlled_access.get(request_id)
        if access_request is None or access_request.institution_id != institution_id:
            raise InstitutionServiceError("访问申请不存在。")
        if access_request.status != ControlledContentAccessStatus.APPROVED:
            raise InstitutionServiceError("访问申请未获批准。")
        if access_request.expires_at <= self._now():
            access_request.status = ControlledContentAccessStatus.EXPIRED
            raise InstitutionServiceError("访问申请已过期。")
        if object_ref not in access_request.object_refs:
            raise InstitutionServiceError("对象不在申请范围内。")

        access_request.access_log.append(
            ControlledContentAccessEvent(
                actor_account_id=actor_account_id,
                object_ref=object_ref,
                accessed_at=self._now(),
                action=action,
                reason=reason,
            )
        )
        return access_request

    def list_controlled_access_requests(
        self, account_id: str, institution_id: str
    ) -> list[ControlledContentAccessRequest]:
        """List controlled access requests visible to the caller."""
        self._require_membership(institution_id, account_id)
        requests = [
            req
            for req in self._controlled_access.values()
            if req.institution_id == institution_id
        ]
        try:
            self._require_admin_or_security_admin(institution_id, account_id)
        except InstitutionServiceError:
            requests = [
                req
                for req in requests
                if req.requester_account_id == account_id
                or req.target_account_id == account_id
            ]
        return requests


@dataclass
class _InstitutionInvite:
    token_id: str
    token_secret: str
    institution_id: str
    invited_by: str
    expected_recipient_id: str
    role: InstitutionRole
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None = None
