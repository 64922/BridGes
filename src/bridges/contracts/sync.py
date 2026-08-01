"""Contracts for causal device synchronization.

The sync boundary deliberately separates control-plane state (authorization,
key epochs and tombstones) from ordinary content operations. A device cache is
never authoritative: the server decides whether an operation can be accepted,
merged, quarantined or kept as a conflict branch.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from bridges.contracts.projects import ObjectRef


class SyncOperationType(StrEnum):
    """Mutation kind carried by a device outbox."""

    UPSERT = "upsert"
    DELETE = "delete"


class SyncOperationStatus(StrEnum):
    """Server decision for a submitted operation."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    MERGED = "merged"
    QUARANTINED = "quarantined"
    CONFLICT = "conflict"


class ConflictBranchStatus(StrEnum):
    """Lifecycle of a manually裁决的冲突分支。"""

    OPEN = "open"
    RESOLVED = "resolved"


class DeviceSyncStatus(StrEnum):
    """Whether the device has acknowledged the current control state."""

    ACTIVE = "active"
    REVOKED = "revoked"
    AWAITING_ACK = "awaiting_ack"


class SyncOutboxStatus(StrEnum):
    """Delivery state of a durable synchronization outbox entry."""

    PENDING = "pending"
    DELIVERED = "delivered"


class SyncOperation(BaseModel):
    """A signed, causally versioned device mutation."""

    operation_id: str = Field(description="Stable idempotency key for the operation.")
    account_id: str = Field(description="Account that owns the device replica.")
    device_id: str = Field(description="Device that created the operation.")
    object_ref: ObjectRef = Field(description="Object affected by the operation.")
    operation_type: SyncOperationType = Field(description="Mutation kind.")
    base_version: int = Field(ge=0, description="Version observed before the edit.")
    causal_parent_ids: list[str] = Field(
        default_factory=list,
        description="Operation ids that causally precede this operation.",
    )
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Typed object patch; never treated as an authorization grant.",
    )
    authorization_version: str = Field(description="Authorization snapshot used by the device.")
    key_epoch: str = Field(description="Device key epoch used to authorize the operation.")
    created_at: datetime = Field(description="When the device created the operation.")
    signature: str | None = Field(default=None, description="Base64 device signature.")
    status: SyncOperationStatus = Field(
        default=SyncOperationStatus.PENDING,
        description="Latest server decision.",
    )
    quarantine_reason: str | None = Field(
        default=None,
        description="Safe reason when the operation is isolated.",
    )


class SyncOutboxEntry(BaseModel):
    """Durable operation-log entry waiting for replica delivery."""

    entry_id: str = Field(description="Stable outbox entry identifier.")
    account_id: str = Field(description="Account whose replicas receive the entry.")
    operation: SyncOperation = Field(description="Accepted operation to deliver.")
    status: SyncOutboxStatus = Field(
        default=SyncOutboxStatus.PENDING,
        description="Delivery state of the entry.",
    )
    created_at: datetime = Field(description="When the entry was appended.")


class Tombstone(BaseModel):
    """An immutable deletion marker that wins over offline edits and restore."""

    tombstone_id: str = Field(description="Stable tombstone identifier.")
    object_ref: ObjectRef = Field(description="Deleted object reference.")
    account_id: str = Field(description="Account that owns the deleted object.")
    deleted_by_device_id: str = Field(description="Device that recorded the deletion.")
    deletion_operation_id: str = Field(description="Operation that created the tombstone.")
    deletion_version: int = Field(
        ge=1,
        description="Version at which deletion became authoritative.",
    )
    authorization_version: str = Field(description="Authorization version at deletion time.")
    key_epoch: str = Field(description="Key epoch used to create the tombstone.")
    created_at: datetime = Field(description="When the tombstone was created.")


class ConflictBranch(BaseModel):
    """Two valid scientific edits retained for human裁决 instead of last-write-wins."""

    conflict_id: str = Field(description="Stable conflict identifier.")
    account_id: str = Field(description="Account that owns the conflict.")
    object_ref: ObjectRef = Field(description="Object whose edits conflict.")
    common_ancestor_version: int = Field(ge=0, description="Shared base version.")
    local_operation_id: str = Field(description="Offline branch operation id.")
    remote_operation_id: str = Field(description="Already accepted branch operation id.")
    local_payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Offline branch payload.",
    )
    remote_payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Accepted branch payload.",
    )
    reason: str = Field(description="Why automatic merge is unsafe.")
    status: ConflictBranchStatus = Field(
        default=ConflictBranchStatus.OPEN,
        description="Human resolution status.",
    )
    created_at: datetime = Field(description="When the branch was recorded.")
    resolved_at: datetime | None = Field(default=None, description="When a human resolved it.")
    selected_operation_id: str | None = Field(
        default=None,
        description="Operation selected by the human resolver.",
    )
    resolution_operation_id: str | None = Field(
        default=None,
        description="New accepted operation created by the human resolver.",
    )


class ConflictResolutionRequest(BaseModel):
    """人工裁决冲突时选择保留的历史分支。"""

    selected_operation_id: str = Field(description="Chosen local or remote operation id.")


class DeviceAck(BaseModel):
    """Proof that a device received the latest control-plane state."""

    ack_id: str = Field(description="Stable acknowledgement identifier.")
    account_id: str = Field(description="Account that owns the device.")
    device_id: str = Field(description="Device acknowledging the state.")
    key_epoch: str = Field(description="Key epoch observed by the device.")
    authorization_version: str = Field(description="Authorization version observed by the device.")
    last_operation_id: str | None = Field(default=None, description="Newest operation received.")
    acknowledged_at: datetime = Field(description="When the acknowledgement was recorded.")
    status: DeviceSyncStatus = Field(description="Device status at acknowledgement time.")


class SyncControlSnapshot(BaseModel):
    """Control state pulled before the server evaluates device content edits."""

    account_id: str = Field(description="Account scope of the snapshot.")
    device_id: str = Field(description="Device requesting the snapshot.")
    authorization_version: str = Field(description="Current authorization policy version.")
    key_epoch: str = Field(description="Current key epoch expected from this device.")
    device_status: DeviceSyncStatus = Field(description="Current device status.")
    revoked_device_ids: list[str] = Field(
        default_factory=list,
        description="Devices denied new sync.",
    )
    tombstones: list[Tombstone] = Field(
        default_factory=list,
        description="Deletion markers to apply first.",
    )
    latest_versions: dict[str, int] = Field(
        default_factory=dict,
        description="Object reference key to authoritative version.",
    )
    outbox: list[SyncOutboxEntry] = Field(
        default_factory=list,
        description="Accepted operations available to the account's replicas.",
    )


class SyncExchangeRequest(BaseModel):
    """Control snapshot acknowledgement plus a device outbox batch."""

    device_id: str = Field(description="Device submitting the batch.")
    key_epoch: str = Field(description="Key epoch held by the device.")
    authorization_version: str = Field(description="Authorization version held by the device.")
    last_operation_id: str | None = Field(
        default=None,
        description="Newest operation acknowledged by device.",
    )
    operations: list[SyncOperation] = Field(
        default_factory=list,
        description="Offline operations to submit.",
    )


class SyncExchangeResponse(BaseModel):
    """Result of a control-first sync exchange."""

    control: SyncControlSnapshot = Field(
        description="Control state applied before content operations."
    )
    accepted_operations: list[SyncOperation] = Field(
        default_factory=list,
        description="Accepted mutations.",
    )
    quarantined_operations: list[SyncOperation] = Field(
        default_factory=list,
        description="Isolated mutations.",
    )
    conflicts: list[ConflictBranch] = Field(
        default_factory=list,
        description="Conflicts requiring human choice.",
    )
    tombstones: list[Tombstone] = Field(
        default_factory=list,
        description="Tombstones that must be applied locally.",
    )
    device_ack: DeviceAck = Field(description="Server acknowledgement of the exchange.")
