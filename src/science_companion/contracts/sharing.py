"""Explicit sharing, project copy, object grant and invite token contracts.

These models define the public surface of T036: explicit shared projects and
minimized project copies. They are the authoritative shape of SharePreview,
ProjectObjectRef, ObjectGrant and InviteToken.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from science_companion.contracts.projects import ObjectDomain, ObjectRef


class ProjectObjectRef(ObjectRef):
    """Reference to an object inside a shared project.

    The domain is fixed to ``SHARED_PROJECT`` because the reference always
    points to a minimized copy owned by the shared project.
    """

    domain: ObjectDomain = Field(
        default=ObjectDomain.SHARED_PROJECT,
        description="Authority domain is always a shared project.",
    )


class GrantPermission(StrEnum):
    """Fine-grained permission on a shared project object.

    Permissions are additive: a subject may hold multiple permissions on the
    same object through role baseline + explicit object grant.
    """

    VIEW = "view"
    EDIT = "edit"
    REVIEW = "review"
    PUBLISH = "publish"


class ObjectGrantStatus(StrEnum):
    """Lifecycle status of an object grant."""

    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"


class InviteTokenStatus(StrEnum):
    """Lifecycle status of an invite token."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"
    EXPIRED = "expired"


class SharedProjectRole(StrEnum):
    """Role of a member inside a shared project.

    Roles provide a baseline permission range; object-level grants can further
    tighten or extend specific object access within the role's maximum.
    """

    OWNER = "owner"
    EDITOR = "editor"
    REVIEWER = "reviewer"
    COMMENTER = "commenter"
    VIEWER = "viewer"


class SharePreviewField(BaseModel):
    """A single field in the share preview, indicating inclusion or exclusion."""

    field_name: str = Field(description="Name of the field.")
    included: bool = Field(description="Whether this field will be copied.")
    reason: str = Field(
        default="",
        description="Why the field is included or excluded.",
    )


class SharePreview(BaseModel):
    """Preview of what will be shared before the user confirms.

    The preview explicitly shows included and excluded fields, the current
    owner, the target project, the purpose, the expiry, and the post-share
    independence guarantee.
    """

    source_object_id: str = Field(description="Personal vault object to share.")
    source_owner_id: str = Field(description="Current owner of the source object.")
    target_project_id: str = Field(description="Project that will receive the copy.")
    recipient_account_id: str = Field(
        description="Account that will receive access to the project copy.",
    )
    grant_purpose: str = Field(description="Declared purpose of the share.")
    included_fields: list[SharePreviewField] = Field(
        default_factory=list,
        description="Fields that will be copied to the project object.",
    )
    excluded_fields: list[SharePreviewField] = Field(
        default_factory=list,
        description="Fields that will NOT be copied (private metadata, profiles, etc.).",
    )
    independence_note: str = Field(
        default="副本与原件独立；后续修改不自动同步。",
        description="Explains that the copy is independent from the original.",
    )
    recipient_role: SharedProjectRole = Field(
        default=SharedProjectRole.VIEWER,
        description="Role the recipient will hold in the shared project.",
    )
    permissions: list[GrantPermission] = Field(
        default_factory=lambda: [GrantPermission.VIEW],
        description="Explicit object-level permissions being granted.",
    )
    expires_at: datetime | None = Field(
        default=None,
        description="If set, the share grant expires at this time.",
    )


class SharePreviewRequest(BaseModel):
    """Request to preview a share before it is executed."""

    source_object_id: str = Field(description="Personal vault object to share.")
    target_project_id: str = Field(description="Project that will receive the copy.")
    grant_purpose: str = Field(description="Declared purpose of the share.")
    recipient_account_id: str = Field(
        description="Account that will receive access to the project copy.",
    )
    recipient_role: SharedProjectRole = Field(
        default=SharedProjectRole.VIEWER,
        description="Role the recipient will hold.",
    )
    permissions: list[GrantPermission] = Field(
        default_factory=lambda: [GrantPermission.VIEW],
        description="Explicit object-level permissions being requested.",
    )
    expires_at: datetime | None = Field(
        default=None,
        description="If set, the object grant expires at this time.",
    )


class ShareExecuteRequest(BaseModel):
    """Request to execute a share after preview confirmation."""

    source_object_id: str = Field(description="Personal vault object to share.")
    target_project_id: str = Field(description="Project that will receive the copy.")
    grant_purpose: str = Field(description="Declared purpose of the share.")
    recipient_account_id: str = Field(
        description="Account that will receive access to the project copy.",
    )
    recipient_role: SharedProjectRole = Field(
        default=SharedProjectRole.VIEWER,
        description="Role the recipient will hold.",
    )
    permissions: list[GrantPermission] = Field(
        default_factory=lambda: [GrantPermission.VIEW],
        description="Explicit object-level permissions for the recipient.",
    )
    expires_at: datetime | None = Field(
        default=None,
        description="If set, the object grant expires at this time.",
    )


class ShareExecuteResult(BaseModel):
    """Result of a successful share execution."""

    project_object_id: str = Field(description="New project copy object identifier.")
    project_object_domain: str = Field(
        default="shared_project",
        description="Domain of the new object.",
    )
    target_project_id: str = Field(description="Project that received the copy.")
    source_object_id: str = Field(description="Original personal vault object.")
    grant_id: str = Field(description="Object grant identifier for the recipient.")
    expires_at: datetime | None = Field(
        default=None,
        description="If set, the object grant expires at this time.",
    )
    independence_note: str = Field(
        default="副本与原件独立；后续修改不自动同步。",
        description="Post-share independence guarantee.",
    )


class ObjectGrant(BaseModel):
    """Per-object authorization in a shared project.

    An object grant binds a subject to specific permissions on a specific
    project object. Role baseline and object grants together determine the
    effective permission set.
    """

    grant_id: str = Field(description="Stable grant identifier.")
    project_id: str = Field(description="Project that owns the object.")
    object_id: str = Field(description="Object to which the grant applies.")
    grantee_account_id: str = Field(description="Account that holds the grant.")
    permissions: list[GrantPermission] = Field(
        description="Permissions granted on the object.",
    )
    granted_by: str = Field(description="Account that created the grant.")
    purpose: str = Field(description="Declared purpose of the grant.")
    status: ObjectGrantStatus = Field(
        default=ObjectGrantStatus.ACTIVE,
        description="Current grant status.",
    )
    created_at: datetime = Field(description="Grant creation timestamp.")
    expires_at: datetime | None = Field(
        default=None,
        description="If set, the grant expires at this time.",
    )
    revoked_at: datetime | None = Field(
        default=None,
        description="If set, the grant has been revoked.",
    )


class InviteToken(BaseModel):
    """Short-lived, single-use invitation token bound to project and identity.

    The token is valid only for the specified project and expected recipient.
    It expires after a short TTL and can only be consumed once.
    """

    token_id: str = Field(description="Stable token identifier.")
    token_secret: str = Field(description="Opaque secret for token redemption.")
    project_id: str = Field(description="Project the invite grants access to.")
    invited_by: str = Field(description="Account that created the invitation.")
    expected_recipient_id: str = Field(
        description="Account expected to redeem the invitation.",
    )
    role: SharedProjectRole = Field(
        description="Role the recipient will hold upon acceptance.",
    )
    status: InviteTokenStatus = Field(
        default=InviteTokenStatus.PENDING,
        description="Current token status.",
    )
    created_at: datetime = Field(description="Token creation timestamp.")
    expires_at: datetime = Field(description="Token expiration timestamp.")
    accepted_at: datetime | None = Field(
        default=None,
        description="If set, the token has been accepted.",
    )
    single_use: bool = Field(
        default=True,
        description="Token can only be redeemed once.",
    )


class InviteCreateRequest(BaseModel):
    """Request to create an invite token for a shared project."""

    project_id: str = Field(description="Project to invite into.")
    recipient_account_id: str = Field(description="Expected recipient account.")
    role: SharedProjectRole = Field(
        default=SharedProjectRole.VIEWER,
        description="Role the recipient will hold.",
    )
    ttl_seconds: int = Field(
        default=3600,
        ge=60,
        le=86400,
        description="Token time-to-live in seconds (min 60, max 86400).",
    )


class InviteAcceptRequest(BaseModel):
    """Request to accept an invite token."""

    token_secret: str = Field(description="Opaque token secret to redeem.")


class SharedProjectMember(BaseModel):
    """A member of a shared project with their role."""

    project_id: str = Field(description="Project identifier.")
    account_id: str = Field(description="Member account identifier.")
    role: SharedProjectRole = Field(description="Member's role in the project.")
    joined_at: datetime = Field(description="When the member joined.")
    invited_by: str | None = Field(
        default=None,
        description="Account that invited this member, if applicable.",
    )


class SharingError(BaseModel):
    """Uniform sharing error response."""

    error: str = Field(description="Stable error code.")
    message: str = Field(description="Human-readable, non-leaking message.")
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Opaque detail safe for logging; must not expose internal state.",
    )
