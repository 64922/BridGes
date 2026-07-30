"""Vault boundary and persistence port contracts.

These models define the public surface of the personal vault, cloud control
projections, temporary task capsules, and device-unavailability states. They are
the authoritative shape of the vault boundary contracts.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class VaultObjectDomain(str, Enum):
    """Authority domain that owns the vault object.

    - PERSONAL_VAULT: owned by an individual account; not visible to collaborators
      or admins by default.
    - SHARED_PROJECT: owned by a project, created from an explicit share decision.
    - INSTITUTION_OWNED: owned by an institution management domain.
    """

    PERSONAL_VAULT = "personal_vault"
    SHARED_PROJECT = "shared_project"
    INSTITUTION_OWNED = "institution_owned"


class ContentAuthority(str, Enum):
    """Where the authoritative plaintext of a vault object resides.

    - DEVICE_LOCAL: the full content is authoritative on the user's device. The
      cloud only holds a control projection (hash and metadata).
    - SERVER_REPLICA: the server holds an encrypted replica for synchronization;
      the device is not required for every read.
    - PROJECT_COPY: a minimized copy owned by a shared project, detached from the
      personal vault original.
    """

    DEVICE_LOCAL = "device_local"
    SERVER_REPLICA = "server_replica"
    PROJECT_COPY = "project_copy"


class CloudProjectionStatus(str, Enum):
    """Lifecycle status of a cloud control projection."""

    ACTIVE = "active"
    PENDING_DEVICE = "pending_device"
    REVOKED = "revoked"
    EXPIRED = "expired"


class CapsuleStatus(str, Enum):
    """Lifecycle status of a temporary task capsule."""

    ISSUED = "issued"
    REVOKED = "revoked"
    EXPIRED = "expired"
    CONSUMED = "consumed"


class VaultObjectRef(BaseModel):
    """Stable reference to a vault-owned object.

    VaultObjectRef carries enough context to re-authenticate and re-authorize a
    deep link without relying on ambient session state alone.
    """

    domain: VaultObjectDomain = Field(description="Authority domain of the object.")
    owner_id: str = Field(
        description="Identifier of the owning account, project, or institution."
    )
    object_id: str = Field(description="Stable object identifier.")
    version: int = Field(default=1, description="Optimistic concurrency version.")


class CloudControlProjection(BaseModel):
    """Minimal cloud-side projection of a vault object.

    The cloud control projection intentionally does not store the full plaintext.
    It carries only enough metadata to authorize, scope, sync, and audit the
    object without exposing private content.
    """

    projection_id: str = Field(description="Stable projection identifier.")
    object_ref: VaultObjectRef = Field(description="Reference to the vault object.")
    content_hash: str = Field(
        description="Cryptographic hash of the authoritative content (sha256)."
    )
    content_length: int = Field(description="Length of the authoritative content in bytes.")
    key_epoch: str = Field(description="Key epoch under which the object is protected.")
    authorization_version: str = Field(
        description="Authorization policy version that governs this projection."
    )
    status: CloudProjectionStatus = Field(description="Current projection status.")
    created_at: datetime = Field(description="Projection creation timestamp.")
    updated_at: datetime = Field(description="Last projection update timestamp.")
    expires_at: datetime | None = Field(
        default=None,
        description="If set, the projection becomes invalid after this time.",
    )


class TemporaryTaskCapsule(BaseModel):
    """Encrypted minimal context for a single task run.

    A temporary task capsule binds subject, purpose, objects, run, authorization
    version, key epoch, and TTL. It is the only long-information carrier that may
    be sent to cloud workers or model gateways.
    """

    capsule_id: str = Field(description="Stable capsule identifier.")
    run_id: str = Field(description="Run to which the capsule is bound.")
    purpose: str = Field(description="Declared processing purpose.")
    object_refs: list[VaultObjectRef] = Field(
        description="Vault objects authorized for this run."
    )
    authorization_snapshot: str = Field(
        description="Opaque authorization snapshot digest/version."
    )
    key_epoch: str = Field(description="Key epoch used to wrap the capsule.")
    issued_at: datetime = Field(description="Capsule issuance timestamp.")
    expires_at: datetime = Field(description="Capsule expiration timestamp.")
    status: CapsuleStatus = Field(description="Current capsule status.")


class DeviceUnavailableState(BaseModel):
    """Legal wait state when the authoritative device is not available.

    The system returns this instead of silently uploading full text from another
    source or falling back to a stale replica.
    """

    object_ref: VaultObjectRef = Field(description="Reference to the affected object.")
    reason: str = Field(description="Human-readable reason for unavailability.")
    can_retry_at: datetime | None = Field(
        default=None,
        description="If known, the earliest time the device may be reachable again.",
    )


class VaultObject(BaseModel):
    """Full vault object projection.

    The projection never includes the full content when the content authority is
    DEVICE_LOCAL; callers must use the vault port to request content, which may
    return DeviceUnavailableState.
    """

    ref: VaultObjectRef = Field(description="Stable object reference.")
    owner_account_id: str = Field(description="Owning account identifier.")
    content_authority: ContentAuthority = Field(
        description="Where the authoritative plaintext resides."
    )
    device_id: str | None = Field(
        default=None,
        description="Device identifier when content authority is device-local.",
    )
    projection: CloudControlProjection = Field(
        description="Cloud control projection for this object."
    )
    status: CloudProjectionStatus = Field(description="Current object status.")
    created_at: datetime = Field(description="Object creation timestamp.")
    updated_at: datetime = Field(description="Last object update timestamp.")


class VaultObjectSummary(BaseModel):
    """List item for the vault object selector."""

    ref: VaultObjectRef = Field(description="Stable object reference.")
    content_authority: ContentAuthority = Field(description="Content authority.")
    status: CloudProjectionStatus = Field(description="Current object status.")
    updated_at: datetime = Field(description="Last update timestamp.")


class VaultObjectCreateRequest(BaseModel):
    """Request to create a private vault object.

    The content is transmitted as a base64-encoded string so that the same
    contract works across JSON APIs and port implementations without relying on
    framework-specific binary handling.
    """

    owner_account_id: str = Field(description="Owning account identifier.")
    content_authority: ContentAuthority = Field(
        description="Where the authoritative plaintext will reside."
    )
    content: str = Field(description="Base64-encoded authoritative content bytes.")
    device_id: str | None = Field(
        default=None,
        description="Device identifier when content authority is device-local.",
    )
    purpose: str = Field(
        default="general",
        description="Declared purpose for creating the object.",
    )
    key_epoch: str = Field(
        default="epoch-0",
        description="Key epoch under which the object is protected.",
    )
    authorization_version: str = Field(
        default="authz-1.0",
        description="Authorization policy version.",
    )
    domain: VaultObjectDomain = Field(
        default=VaultObjectDomain.PERSONAL_VAULT,
        description="Authority domain for the new vault object.",
    )


class VaultShareRequest(BaseModel):
    """Request to share a personal vault object as a minimized project copy."""

    source_object_id: str = Field(description="Personal vault object to share.")
    target_project_id: str = Field(description="Project that will receive the copy.")
    grant_purpose: str = Field(description="Declared purpose of the share.")


class CapsuleIssueRequest(BaseModel):
    """Request to issue a temporary task capsule for a vault object."""

    owner_account_id: str = Field(description="Owning account identifier.")
    object_id: str = Field(description="Vault object identifier.")
    run_id: str = Field(description="Run to which the capsule is bound.")
    purpose: str = Field(description="Declared processing purpose.")
    ttl_seconds: int = Field(
        default=3600,
        ge=1,
        description="Time-to-live in seconds.",
    )


class VaultError(BaseModel):
    """Uniform vault error response."""

    error: str = Field(description="Stable error code.")
    message: str = Field(description="Human-readable, non-leaking message.")
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Opaque detail safe for logging; must not expose internal state.",
    )
