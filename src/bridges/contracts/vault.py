"""Vault boundary and persistence port contracts.

These models define the public surface of the personal vault, cloud control
projections, temporary task capsules, and device-unavailability states. They are
the authoritative shape of the vault boundary contracts.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class VaultObjectDomain(StrEnum):
    """Authority domain that owns the vault object.

    - PERSONAL_VAULT: owned by an individual account; not visible to collaborators
      or admins by default.
    - SHARED_PROJECT: owned by a project, created from an explicit share decision.
    - INSTITUTION_OWNED: owned by an institution management domain.
    """

    PERSONAL_VAULT = "personal_vault"
    SHARED_PROJECT = "shared_project"
    INSTITUTION_OWNED = "institution_owned"


class ContentAuthority(StrEnum):
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


class CloudProjectionStatus(StrEnum):
    """Lifecycle status of a cloud control projection."""

    ACTIVE = "active"
    PENDING_DEVICE = "pending_device"
    REVOKED = "revoked"
    EXPIRED = "expired"


class CapsuleStatus(StrEnum):
    """Lifecycle status of a temporary task capsule."""

    ISSUED = "issued"
    REVOKED = "revoked"
    EXPIRED = "expired"
    CONSUMED = "consumed"


class DevicePairingStatus(StrEnum):
    """Lifecycle status of a device pairing and its key epoch."""

    PAIRED = "paired"
    REVOKED = "revoked"


class DeviceType(StrEnum):
    """Category of device running the vault runtime."""

    BROWSER = "browser"
    DESKTOP = "desktop"
    MOBILE = "mobile"

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
    content_hash: str | None = Field(
        default=None,
        description="Optional pre-computed hash of the authoritative plaintext.",
    )
    content_length: int | None = Field(
        default=None,
        description="Optional pre-computed length of the authoritative plaintext.",
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


class DeviceCertificate(BaseModel):
    """Binding between a user account and a trusted device.

    The certificate carries only the public key and metadata; the corresponding
    private key is held in the device system keychain and never written to
    ordinary configuration or logs.
    """

    certificate_id: str = Field(description="Stable certificate identifier.")
    account_id: str = Field(description="Owning account identifier.")
    device_id: str = Field(description="Stable device identifier.")
    device_name: str = Field(description="Human-readable device label.")
    device_type: DeviceType = Field(description="Category of device.")
    public_key_pem: str = Field(description="PEM-encoded device public key.")
    fingerprint: str = Field(description="Deterministic fingerprint of the public key.")
    status: DevicePairingStatus = Field(description="Current pairing status.")
    key_epoch: str = Field(description="Active key epoch for this device.")
    paired_at: datetime = Field(description="Pairing creation timestamp.")
    revoked_at: datetime | None = Field(
        default=None,
        description="If set, the device has been revoked.",
    )


class KeyEpoch(BaseModel):
    """A key epoch under which device-local objects are encrypted.

    Revoking a device creates a new key epoch so that old device keys can no
    longer unwrap newly created capsules or decrypt fresh content.
    """

    epoch_id: str = Field(description="Stable epoch identifier.")
    account_id: str = Field(description="Owning account identifier.")
    device_id: str = Field(description="Device to which the epoch belongs.")
    status: DevicePairingStatus = Field(description="Current epoch status.")
    created_at: datetime = Field(description="Epoch creation timestamp.")
    revoked_at: datetime | None = Field(
        default=None,
        description="If set, the epoch has been rotated out.",
    )


class VaultRuntime(BaseModel):
    """Projection of a device-side vault runtime.

    The runtime is the authoritative location for encrypted personal content,
    private indexes and offline operations. It binds to exactly one account and
    device certificate.
    """

    runtime_id: str = Field(description="Stable runtime identifier.")
    account_id: str = Field(description="Owning account identifier.")
    device_id: str = Field(description="Bound device identifier.")
    certificate_id: str = Field(description="Bound device certificate.")
    version: str = Field(description="Runtime version.")
    capabilities: list[str] = Field(
        default_factory=list,
        description="Supported capabilities, e.g. encrypted_storage, offline_index.",
    )
    paired_at: datetime = Field(description="When the runtime was paired.")


class DevicePairingRequest(BaseModel):
    """Request to pair a new vault runtime with an account."""

    device_name: str = Field(description="Human-readable device label.")
    device_type: DeviceType = Field(
        default=DeviceType.BROWSER,
        description="Category of device.",
    )
    public_key_pem: str | None = Field(
        default=None,
        description=(
            "Optional client-supplied public key; "
            "otherwise generated server-side for tests."
        ),
    )


class DevicePairingResponse(BaseModel):
    """Result of a successful device pairing."""

    certificate: DeviceCertificate = Field(description="Device certificate.")
    key_epoch: KeyEpoch = Field(description="Initial active key epoch.")
    runtime: VaultRuntime = Field(description="Bound vault runtime.")


class DeviceRevocationRequest(BaseModel):
    """Request to revoke a paired device."""

    device_id: str = Field(description="Device to revoke.")
    reason: str = Field(default="user_request", description="Reason for revocation.")


class VaultLocalEncryptedObject(BaseModel):
    """Metadata for an object whose ciphertext is authoritative on a device.

    The cloud projection stores only the content hash and this record's
    reference; the wrapped data key and ciphertext stay on the device.
    """

    object_ref: VaultObjectRef = Field(description="Reference to the vault object.")
    device_id: str = Field(description="Authoritative device identifier.")
    key_epoch: str = Field(description="Key epoch used to wrap the object key.")
    wrapped_key: str = Field(
        description="Base64-encoded data encryption key wrapped by the device key."
    )
    ciphertext: str = Field(
        description="Base64-encoded encrypted content bytes."
    )


class VaultError(BaseModel):
    """Uniform vault error response."""

    error: str = Field(description="Stable error code.")
    message: str = Field(description="Human-readable, non-leaking message.")
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Opaque detail safe for logging; must not expose internal state.",
    )
