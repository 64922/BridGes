"""Institution management domain contracts shared by API and Web.

These models define the public surface of the institution management domain:
institution identity, membership, seats, policy, institution-owned projects and
controlled content access. They are the authoritative shape of the institution
management boundary (T037).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from science_companion.contracts.projects import Project


class InstitutionRole(StrEnum):
    """Role of an account inside an institution.

    - ADMIN: can manage members, policy and institution-owned projects.
    - MEMBER: ordinary institution member.
    - SECURITY_ADMIN: can manage security policy and request controlled content
      access, but still cannot read member personal vaults without the
      two-approval controlled access flow.
    - BILLING_ADMIN: can manage seats and billing-related policy.
    """

    ADMIN = "admin"
    MEMBER = "member"
    SECURITY_ADMIN = "security_admin"
    BILLING_ADMIN = "billing_admin"


class InstitutionStatus(StrEnum):
    """Lifecycle status of an institution."""

    ACTIVE = "active"
    SUSPENDED = "suspended"


class InstitutionDomain(StrEnum):
    """Authority domain for an institution."""

    INSTITUTION = "institution"


class Institution(BaseModel):
    """Public institution projection.

    An institution is a management domain, not a data container for member
    personal vaults.
    """

    id: str = Field(description="Stable institution identifier.")
    name: str = Field(
        description="Human-readable institution name.", min_length=1, max_length=200
    )
    domain: InstitutionDomain = Field(
        default=InstitutionDomain.INSTITUTION,
        description="Authority domain is always institution.",
    )
    status: InstitutionStatus = Field(description="Lifecycle status.")
    owner_account_id: str = Field(
        description="Account that created the institution.",
    )
    created_at: datetime = Field(description="Institution creation timestamp.")
    updated_at: datetime = Field(description="Last institution update timestamp.")


class Membership(BaseModel):
    """Relationship between an account and an institution."""

    institution_id: str = Field(description="Institution identifier.")
    account_id: str = Field(description="Member account identifier.")
    role: InstitutionRole = Field(description="Member's role in the institution.")
    joined_at: datetime = Field(description="When the member joined.")
    invited_by: str | None = Field(
        default=None,
        description="Account that invited this member, if applicable.",
    )


class MembershipContext(BaseModel):
    """Lightweight membership context carried in the subject."""

    institution_id: str = Field(description="Institution identifier.")
    role: InstitutionRole = Field(description="Member's role in the institution.")


class SeatPolicy(BaseModel):
    """Seat and usage policy for an institution."""

    max_seats: int | None = Field(
        default=None,
        description="Maximum number of member seats, if enforced.",
    )
    enforce_mfa: bool = Field(
        default=False,
        description="Whether members must use multi-factor authentication.",
    )
    allow_guest_invites: bool = Field(
        default=True,
        description="Whether project-level guest invites are allowed.",
    )


class InstitutionPolicy(BaseModel):
    """Governance policy for an institution."""

    seat_policy: SeatPolicy = Field(
        default_factory=SeatPolicy,
        description="Seat and authentication policy.",
    )
    data_retention_days: int | None = Field(
        default=None,
        description="Retention period for institution-owned objects, if set.",
    )
    recovery_key_enabled: bool = Field(
        default=False,
        description="Whether the institution holds a recovery key for its projects.",
    )
    policy_version: str = Field(
        default="inst-policy-1.0",
        description="Version of the institution policy.",
    )
    updated_at: datetime = Field(description="Last policy update timestamp.")
    updated_by: str = Field(description="Account that last updated the policy.")


class InstitutionOwnedProjectDisclosure(BaseModel):
    """Disclosure shown before creating an institution-owned project.

    Creating an institution-owned project must make ownership, retention and
    recovery boundaries explicit.
    """

    institution_id: str = Field(description="Owning institution identifier.")
    institution_name: str = Field(description="Owning institution name.")
    ownership_statement: str = Field(
        default="该项目为机构所有，成员离开机构后保留的项目访问由机构策略决定。",
        description="Ownership statement.",
    )
    retention_statement: str = Field(
        default="机构对象受机构数据保留策略约束；个人账户不保留恢复权威。",
        description="Retention statement.",
    )
    recovery_statement: str = Field(
        default="机构未启用恢复密钥；项目负责人可决定冻结或迁移。",
        description="Recovery statement.",
    )
    recovery_key_enabled: bool = Field(
        description="Whether a recovery key is enabled for this institution.",
    )
    acknowledged: bool = Field(
        default=False,
        description="Whether the creator has acknowledged the disclosure.",
    )


class ControlledContentAccessStatus(StrEnum):
    """Status of a controlled content access request."""

    PENDING = "pending"
    APPROVED = "approved"
    EXPIRED = "expired"
    REVOKED = "revoked"
    COMPLETED = "completed"


class ControlledContentAccessCreateRequest(BaseModel):
    """Request body to create a controlled content access request."""

    target_account_id: str = Field(description="Account whose content may be accessed.")
    object_refs: list[str] = Field(
        default_factory=list,
        description="Specific object identifiers requested for access.",
    )
    purpose: str = Field(description="Documented purpose of the access.")
    duration_minutes: int = Field(
        default=60,
        ge=1,
        le=1440,
        description="How long the access lasts after approval.",
    )


class ControlledContentAccessRequest(BaseModel):
    """Request to access member content for a security incident.

    Access is purpose-limited, time-limited, requires two approvals and is fully
    audited. It never grants blanket access to a member's personal vault.
    """

    request_id: str = Field(description="Stable request identifier.")
    institution_id: str = Field(description="Institution identifier.")
    requester_account_id: str = Field(description="Account requesting access.")
    target_account_id: str = Field(
        description="Account whose content may be accessed.",
    )
    object_refs: list[str] = Field(
        default_factory=list,
        description="Specific object identifiers requested for access.",
    )
    purpose: str = Field(description="Documented purpose of the access.")
    status: ControlledContentAccessStatus = Field(
        default=ControlledContentAccessStatus.PENDING,
        description="Current request status.",
    )
    requested_at: datetime = Field(description="When the request was created.")
    expires_at: datetime = Field(description="When the approved access expires.")
    approvals: list[ControlledContentAccessApproval] = Field(
        default_factory=list,
        description="Approval records.",
    )
    access_log: list[ControlledContentAccessEvent] = Field(
        default_factory=list,
        description="Audit log of actual access events.",
    )


class ControlledContentAccessApproval(BaseModel):
    """Single approval for a controlled content access request."""

    approver_account_id: str = Field(description="Account that approved.")
    approved_at: datetime = Field(description="When the approval was given.")
    reason: str = Field(description="Reason for approval.")


class ControlledContentAccessEvent(BaseModel):
    """Audit record of an actual content access under controlled access."""

    actor_account_id: str = Field(description="Account that performed the access.")
    object_ref: str = Field(description="Object identifier accessed.")
    accessed_at: datetime = Field(description="When the access occurred.")
    action: str = Field(description="Action performed, e.g. read_content.")
    reason: str = Field(description="Reason recorded at access time.")


class InstitutionCreateRequest(BaseModel):
    """Request to create a new institution."""

    name: str = Field(
        description="Institution name.", min_length=1, max_length=200
    )


class InstitutionInviteRequest(BaseModel):
    """Request to invite an account to join an institution."""

    recipient_account_id: str = Field(description="Expected recipient account.")
    role: InstitutionRole = Field(
        default=InstitutionRole.MEMBER,
        description="Role the recipient will hold.",
    )
    ttl_seconds: int = Field(
        default=3600,
        ge=60,
        le=86400,
        description="Token time-to-live in seconds.",
    )


class InstitutionMemberUpdateRequest(BaseModel):
    """Request to update an institution member's role."""

    role: InstitutionRole = Field(description="New role for the member.")


class InstitutionProjectCreateRequest(BaseModel):
    """Request to create an institution-owned project.

    The request must include an explicit disclosure acknowledgement so the UI
    cannot silently create an institution-owned project.
    """

    name: str = Field(
        description="Project name.", min_length=1, max_length=200
    )
    description: str | None = Field(default=None, description="Optional project goal.")
    disclosure_acknowledged: bool = Field(
        description="Creator has acknowledged ownership, retention and recovery boundaries.",
    )


class InstitutionProjectCreateResponse(BaseModel):
    """Response when creating an institution-owned project."""

    project: Project = Field(description="Created institution-owned project.")
    disclosure: InstitutionOwnedProjectDisclosure = Field(
        description="Disclosure that was acknowledged.",
    )


class InstitutionError(BaseModel):
    """Uniform institution error response."""

    error: str = Field(description="Stable error code.")
    message: str = Field(description="Human-readable, non-leaking message.")
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Opaque detail safe for logging; must not expose internal state.",
    )
