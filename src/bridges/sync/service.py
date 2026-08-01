"""Causal synchronization, conflict and tombstone service."""

from __future__ import annotations

import base64
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey

from bridges.contracts.projects import ObjectRef
from bridges.contracts.sync import (
    ConflictBranch,
    ConflictBranchStatus,
    DeviceAck,
    DeviceSyncStatus,
    SyncControlSnapshot,
    SyncExchangeRequest,
    SyncExchangeResponse,
    SyncOperation,
    SyncOperationStatus,
    SyncOperationType,
    SyncOutboxEntry,
    SyncOutboxStatus,
    Tombstone,
)
from bridges.contracts.vault import DevicePairingStatus
from bridges.persistence import StateStore
from bridges.vault.ports import DevicePairingRepository


class SyncError(Exception):
    """Safe error raised at the synchronization boundary."""


@dataclass
class _ObjectState:
    version: int
    payload: dict[str, Any]
    last_operation_id: str


_SEMANTIC_FIELDS = {
    "claim",
    "claim_id",
    "causal_relation",
    "conclusion",
    "evidence_ids",
    "fact_lock",
    "formula",
    "owner_id",
    "permission",
    "publish_status",
    "unit",
    "visibility",
    "wording_strength",
}


def _now() -> datetime:
    return datetime.now(UTC)


def _object_key(ref: ObjectRef) -> str:
    return f"{ref.domain.value}:{ref.owner_id}:{ref.object_id}"


class SyncService:
    """Apply signed device operations after control-plane synchronization.

    This implementation is intentionally a small authoritative adapter. Its
    public methods are independent of storage, so the operation log can later
    move to the PostgreSQL ``sync`` schema without changing callers.
    """

    def __init__(
        self,
        device_pairing_repository: DevicePairingRepository | None = None,
        state_store: StateStore | None = None,
        object_authorizer: Callable[[str, ObjectRef], bool] | None = None,
    ) -> None:
        self._pairing_repository = device_pairing_repository
        self._state_store = state_store
        self._object_authorizer = object_authorizer
        self._operations: dict[str, SyncOperation] = {}
        self._outbox: dict[str, SyncOutboxEntry] = {}
        self._objects: dict[str, _ObjectState] = {}
        self._tombstones: dict[str, Tombstone] = {}
        self._conflicts: dict[str, ConflictBranch] = {}
        self._authorization_versions: dict[str, str] = {}
        self._registered_epochs: dict[tuple[str, str], str] = {}
        self._revoked_devices: set[tuple[str, str]] = set()
        self._device_acks: dict[tuple[str, str], DeviceAck] = {}
        self._load_state()

    def _load_state(self) -> None:
        if self._state_store is None:
            return
        state = self._state_store.load("sync") or {}
        self._operations = {
            operation_id: SyncOperation.model_validate(value)
            for operation_id, value in state.get("operations", {}).items()
        }
        self._outbox = {
            entry_id: SyncOutboxEntry.model_validate(value)
            for entry_id, value in state.get("outbox", {}).items()
        }
        self._objects = {
            key: _ObjectState(
                version=int(value["version"]),
                payload=dict(value["payload"]),
                last_operation_id=str(value["last_operation_id"]),
            )
            for key, value in state.get("objects", {}).items()
        }
        self._tombstones = {
            key: Tombstone.model_validate(value)
            for key, value in state.get("tombstones", {}).items()
        }
        self._conflicts = {
            conflict_id: ConflictBranch.model_validate(value)
            for conflict_id, value in state.get("conflicts", {}).items()
        }
        self._authorization_versions = {
            str(account_id): str(version)
            for account_id, version in state.get("authorization_versions", {}).items()
        }
        self._registered_epochs = {
            tuple(key.split("\u001f", 1)): str(epoch)
            for key, epoch in state.get("registered_epochs", {}).items()
        }
        self._revoked_devices = {
            tuple(key.split("\u001f", 1))
            for key in state.get("revoked_devices", [])
        }
        self._device_acks = {
            tuple(key.split("\u001f", 1)): DeviceAck.model_validate(value)
            for key, value in state.get("device_acks", {}).items()
        }

    def _persist(self) -> None:
        if self._state_store is None:
            return
        self._state_store.save(
            "sync",
            {
                "operations": {
                    operation_id: operation.model_dump(mode="json")
                    for operation_id, operation in self._operations.items()
                },
                "outbox": {
                    entry_id: entry.model_dump(mode="json")
                    for entry_id, entry in self._outbox.items()
                },
                "objects": {
                    key: {
                        "version": state.version,
                        "payload": state.payload,
                        "last_operation_id": state.last_operation_id,
                    }
                    for key, state in self._objects.items()
                },
                "tombstones": {
                    key: tombstone.model_dump(mode="json")
                    for key, tombstone in self._tombstones.items()
                },
                "conflicts": {
                    conflict_id: conflict.model_dump(mode="json")
                    for conflict_id, conflict in self._conflicts.items()
                },
                "authorization_versions": self._authorization_versions,
                "registered_epochs": {
                    "\u001f".join(key): epoch
                    for key, epoch in self._registered_epochs.items()
                },
                "revoked_devices": ["\u001f".join(key) for key in self._revoked_devices],
                "device_acks": {
                    "\u001f".join(key): ack.model_dump(mode="json")
                    for key, ack in self._device_acks.items()
                },
            },
        )

    def register_device(self, account_id: str, device_id: str, key_epoch: str) -> None:
        """Register a replica for tests or a device runtime adapter."""
        self._registered_epochs[(account_id, device_id)] = key_epoch
        self._persist()

    def set_authorization_version(self, account_id: str, version: str) -> None:
        """Publish a new authorization version before accepting edits."""
        self._authorization_versions[account_id] = version
        self._persist()

    def revoke_device(self, account_id: str, device_id: str) -> None:
        """Deny future sync from a device; its queued edits remain quarantined."""
        self._revoked_devices.add((account_id, device_id))
        self._persist()

    def _pairing_state(
        self, account_id: str, device_id: str
    ) -> tuple[DeviceSyncStatus, str | None]:
        if (account_id, device_id) in self._revoked_devices:
            return DeviceSyncStatus.REVOKED, None
        if self._pairing_repository is not None:
            certificate = self._pairing_repository.get_certificate(account_id, device_id)
            if certificate is not None:
                if certificate.status != DevicePairingStatus.PAIRED:
                    return DeviceSyncStatus.REVOKED, None
                return DeviceSyncStatus.ACTIVE, certificate.key_epoch
        epoch = self._registered_epochs.get((account_id, device_id))
        if epoch is not None:
            return DeviceSyncStatus.ACTIVE, epoch
        return DeviceSyncStatus.AWAITING_ACK, None

    def _current_authorization_version(self, account_id: str) -> str:
        return self._authorization_versions.get(account_id, "authz-1")

    def pull_control(self, account_id: str, device_id: str) -> SyncControlSnapshot:
        """Return policy, key epoch, revocations and tombstones first."""
        status, device_epoch = self._pairing_state(account_id, device_id)
        if device_epoch is None:
            device_epoch = self._registered_epochs.get((account_id, device_id), "epoch-0")
        latest_versions = {
            key: state.version
            for key, state in self._objects.items()
            if key.startswith(f"personal_vault:{account_id}:")
            or key.startswith(f"shared_project:{account_id}:")
        }
        outbox = [
            entry
            for entry in self._outbox.values()
            if entry.account_id == account_id and entry.status == SyncOutboxStatus.PENDING
        ]
        return SyncControlSnapshot(
            account_id=account_id,
            device_id=device_id,
            authorization_version=self._current_authorization_version(account_id),
            key_epoch=device_epoch,
            device_status=status,
            revoked_device_ids=sorted(
                device for owner, device in self._revoked_devices if owner == account_id
            ),
            tombstones=[
                tombstone for tombstone in self._tombstones.values()
                if tombstone.account_id == account_id
            ],
            latest_versions=latest_versions,
            outbox=outbox,
        )

    def _quarantine(self, operation: SyncOperation, reason: str) -> SyncOperation:
        quarantined = operation.model_copy(
            update={
                "status": SyncOperationStatus.QUARANTINED,
                "quarantine_reason": reason,
            }
        )
        # 已落库的决定不能被同一幂等键的伪造请求覆盖，否则攻击者可以
        # 把已接受操作改写成隔离记录，破坏审计链和重放语义。
        if operation.operation_id not in self._operations:
            self._operations[operation.operation_id] = quarantined
        return quarantined

    def _operation_matches(
        self,
        existing: SyncOperation,
        candidate: SyncOperation,
    ) -> bool:
        """Check that a retry has the same immutable operation contents."""
        return (
            existing.account_id == candidate.account_id
            and existing.device_id == candidate.device_id
            and _object_key(existing.object_ref) == _object_key(candidate.object_ref)
            and existing.operation_type == candidate.operation_type
            and existing.base_version == candidate.base_version
            and existing.causal_parent_ids == candidate.causal_parent_ids
            and existing.payload == candidate.payload
            and existing.authorization_version == candidate.authorization_version
            and existing.key_epoch == candidate.key_epoch
            and existing.created_at == candidate.created_at
            and existing.signature == candidate.signature
        )

    def _is_object_authorized(self, account_id: str, object_ref: ObjectRef) -> bool:
        if self._object_authorizer is not None:
            return self._object_authorizer(account_id, object_ref)
        return (
            object_ref.domain.value == "personal_vault"
            and object_ref.owner_id == account_id
        )

    @staticmethod
    def _canonical_operation(operation: SyncOperation) -> bytes:
        unsigned = operation.model_dump(
            mode="json",
            exclude={"signature", "status", "quarantine_reason"},
        )
        return json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def _verify_signature(self, operation: SyncOperation) -> bool:
        """Verify paired RSA keys; test-only registrations still require a token."""
        if not operation.signature:
            return False
        certificate = (
            self._pairing_repository.get_certificate(operation.account_id, operation.device_id)
            if self._pairing_repository is not None
            else None
        )
        if certificate is None:
            return True
        if certificate.status != DevicePairingStatus.PAIRED:
            return False
        try:
            public_key = serialization.load_pem_public_key(
                certificate.public_key_pem.encode("ascii")
            )
            if not isinstance(public_key, RSAPublicKey):
                return False
            signature = base64.b64decode(operation.signature, validate=True)
            public_key.verify(
                signature,
                self._canonical_operation(operation),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        except (ValueError, TypeError, UnicodeError):
            return False
        except Exception:
            return False
        return True

    def _record_outbox(self, operation: SyncOperation) -> SyncOutboxEntry:
        existing = next(
            (
                entry
                for entry in self._outbox.values()
                if entry.operation.operation_id == operation.operation_id
            ),
            None,
        )
        if existing is not None:
            return existing
        entry = SyncOutboxEntry(
            entry_id=secrets.token_urlsafe(16),
            account_id=operation.account_id,
            operation=operation,
            created_at=_now(),
        )
        self._outbox[entry.entry_id] = entry
        return entry

    def _is_semantic_conflict(
        self,
        operation: SyncOperation,
        current: _ObjectState,
        remote_operation: SyncOperation | None,
    ) -> bool:
        if remote_operation is None:
            return True
        changed_fields = set(operation.payload) | set(remote_operation.payload)
        if changed_fields & _SEMANTIC_FIELDS:
            return True
        overlap = set(operation.payload) & set(remote_operation.payload)
        return any(operation.payload[field] != remote_operation.payload[field] for field in overlap)

    def _apply_upsert(
        self,
        operation: SyncOperation,
        current: _ObjectState | None,
    ) -> SyncOperation:
        next_version = (current.version if current else 0) + 1
        payload = dict(current.payload) if current else {}
        payload.update(operation.payload)
        accepted = operation.model_copy(
            update={
                "status": (
                    SyncOperationStatus.ACCEPTED
                    if current is None
                    or operation.base_version == (current.version if current else 0)
                    else SyncOperationStatus.MERGED
                ),
                "object_ref": operation.object_ref.model_copy(update={"version": next_version}),
            }
        )
        self._objects[_object_key(operation.object_ref)] = _ObjectState(
            version=next_version,
            payload=payload,
            last_operation_id=operation.operation_id,
        )
        self._operations[operation.operation_id] = accepted
        self._record_outbox(accepted)
        return accepted

    def _apply_delete(
        self,
        operation: SyncOperation,
        current: _ObjectState | None,
    ) -> tuple[SyncOperation, Tombstone]:
        deletion_version = (current.version if current else operation.base_version) + 1
        tombstone = Tombstone(
            tombstone_id=secrets.token_urlsafe(16),
            object_ref=operation.object_ref.model_copy(update={"version": deletion_version}),
            account_id=operation.account_id,
            deleted_by_device_id=operation.device_id,
            deletion_operation_id=operation.operation_id,
            deletion_version=deletion_version,
            authorization_version=operation.authorization_version,
            key_epoch=operation.key_epoch,
            created_at=_now(),
        )
        accepted = operation.model_copy(
            update={
                "status": SyncOperationStatus.ACCEPTED,
                "object_ref": tombstone.object_ref,
            }
        )
        self._tombstones[_object_key(operation.object_ref)] = tombstone
        self._objects[_object_key(operation.object_ref)] = _ObjectState(
            version=deletion_version,
            payload={},
            last_operation_id=operation.operation_id,
        )
        self._operations[operation.operation_id] = accepted
        self._record_outbox(accepted)
        return accepted, tombstone

    def _accept_operation(
        self,
        operation: SyncOperation,
    ) -> tuple[SyncOperation | None, ConflictBranch | None, Tombstone | None]:
        key = _object_key(operation.object_ref)
        if key in self._tombstones:
            return (
                self._quarantine(operation, "对象已有删除墓碑，离线修改不能复活对象。"),
                None,
                None,
            )

        current = self._objects.get(key)

        if current is None:
            if operation.base_version != 0:
                return (
                    self._quarantine(operation, "缺少共同祖先版本，等待先同步远端版本。"),
                    None,
                    None,
                )
            return self._apply_upsert(operation, None), None, None

        if operation.operation_type == SyncOperationType.DELETE:
            if operation.base_version > current.version:
                return (
                    self._quarantine(operation, "删除操作领先于服务端版本，等待因果前序操作。"),
                    None,
                    None,
                )
            if operation.base_version < current.version:
                remote_operation = self._operations.get(current.last_operation_id)
                conflict = ConflictBranch(
                    conflict_id=secrets.token_urlsafe(16),
                    account_id=operation.account_id,
                    object_ref=operation.object_ref.model_copy(update={"version": current.version}),
                    common_ancestor_version=operation.base_version,
                    local_operation_id=operation.operation_id,
                    remote_operation_id=current.last_operation_id,
                    local_payload={},
                    remote_payload=(
                        dict(remote_operation.payload)
                        if remote_operation
                        else dict(current.payload)
                    ),
                    reason="删除与编辑并发，必须人工裁决，不能自动删除或覆盖有效编辑。",
                    created_at=_now(),
                )
                quarantined = self._quarantine(
                    operation,
                    "删除与远端编辑并发，已创建冲突分支。",
                )
                self._operations[operation.operation_id] = quarantined.model_copy(
                    update={"status": SyncOperationStatus.CONFLICT}
                )
                self._conflicts[conflict.conflict_id] = conflict
                return None, conflict, None
            accepted, tombstone = self._apply_delete(operation, current)
            return accepted, None, tombstone

        if operation.base_version > current.version:
            return self._quarantine(operation, "本地版本领先服务端，无法建立因果关系。"), None, None
        if operation.base_version == current.version:
            return self._apply_upsert(operation, current), None, None

        remote_operation = self._operations.get(current.last_operation_id)
        if self._is_semantic_conflict(operation, current, remote_operation):
            conflict = ConflictBranch(
                conflict_id=secrets.token_urlsafe(16),
                account_id=operation.account_id,
                object_ref=operation.object_ref.model_copy(update={"version": current.version}),
                common_ancestor_version=operation.base_version,
                local_operation_id=operation.operation_id,
                remote_operation_id=current.last_operation_id,
                local_payload=dict(operation.payload),
                remote_payload=(
                    dict(remote_operation.payload)
                    if remote_operation
                    else dict(current.payload)
                ),
                reason="科学语义或重叠字段存在并发修改，不能使用最后写入者获胜。",
                created_at=_now(),
            )
            quarantined = self._quarantine(operation, "已创建冲突分支，等待人工裁决。")
            self._operations[operation.operation_id] = quarantined.model_copy(
                update={"status": SyncOperationStatus.CONFLICT}
            )
            self._conflicts[conflict.conflict_id] = conflict
            return None, conflict, None
        return self._apply_upsert(operation, current), None, None

    def exchange(
        self,
        account_id: str,
        request: SyncExchangeRequest,
    ) -> SyncExchangeResponse:
        """Pull control state, then validate and apply the device outbox."""
        control = self.pull_control(account_id, request.device_id)
        accepted: list[SyncOperation] = []
        quarantined: list[SyncOperation] = []
        conflicts: list[ConflictBranch] = []
        tombstones: list[Tombstone] = list(control.tombstones)

        status = control.device_status
        if status == DeviceSyncStatus.REVOKED:
            for operation in request.operations:
                quarantined.append(self._quarantine(operation, "设备已撤权，离线操作进入隔离区。"))
        elif status != DeviceSyncStatus.ACTIVE:
            for operation in request.operations:
                quarantined.append(self._quarantine(operation, "设备尚未完成配对确认。"))
        elif request.key_epoch != control.key_epoch:
            for operation in request.operations:
                quarantined.append(self._quarantine(operation, "设备使用旧密钥时期，操作被隔离。"))
        elif request.authorization_version != control.authorization_version:
            for operation in request.operations:
                quarantined.append(
                    self._quarantine(operation, "授权版本已变化，操作需要重新授权。")
                )
        else:
            for operation in request.operations:
                if operation.account_id != account_id or operation.device_id != request.device_id:
                    quarantined.append(self._quarantine(operation, "操作主体与同步设备不一致。"))
                    continue
                if not self._is_object_authorized(account_id, operation.object_ref):
                    quarantined.append(
                        self._quarantine(operation, "同步对象不属于当前账户或没有项目授权。")
                    )
                    continue
                if operation.key_epoch != control.key_epoch:
                    quarantined.append(
                        self._quarantine(operation, "操作使用旧密钥时期，操作被隔离。")
                    )
                    continue
                if operation.authorization_version != control.authorization_version:
                    quarantined.append(
                        self._quarantine(operation, "操作使用旧授权版本，操作被隔离。")
                    )
                    continue
                if not self._verify_signature(operation):
                    quarantined.append(self._quarantine(operation, "设备签名缺失或校验失败。"))
                    continue
                existing = self._operations.get(operation.operation_id)
                if existing is not None:
                    if not self._operation_matches(existing, operation):
                        quarantined.append(
                            self._quarantine(operation, "操作幂等键已被其他同步主体占用。")
                        )
                    elif existing.status in {
                        SyncOperationStatus.ACCEPTED,
                        SyncOperationStatus.MERGED,
                    }:
                        accepted.append(existing)
                    elif existing.status == SyncOperationStatus.CONFLICT:
                        conflict = next(
                            (
                                item
                                for item in self._conflicts.values()
                                if item.local_operation_id == operation.operation_id
                            ),
                            None,
                        )
                        if conflict is not None:
                            conflicts.append(conflict)
                        else:
                            quarantined.append(existing)
                    else:
                        quarantined.append(existing)
                    continue
                accepted_operation, conflict, new_tombstone = self._accept_operation(operation)
                if accepted_operation is not None:
                    if accepted_operation.status in {
                        SyncOperationStatus.ACCEPTED,
                        SyncOperationStatus.MERGED,
                    }:
                        accepted.append(accepted_operation)
                    else:
                        quarantined.append(accepted_operation)
                if conflict is not None:
                    conflicts.append(conflict)
                if new_tombstone is not None:
                    tombstones.append(new_tombstone)

        ack = DeviceAck(
            ack_id=secrets.token_urlsafe(16),
            account_id=account_id,
            device_id=request.device_id,
            key_epoch=control.key_epoch,
            authorization_version=control.authorization_version,
            last_operation_id=request.last_operation_id,
            acknowledged_at=_now(),
            status=status,
        )
        self._device_acks[(account_id, request.device_id)] = ack
        self._persist()
        return SyncExchangeResponse(
            control=control,
            accepted_operations=accepted,
            quarantined_operations=quarantined,
            conflicts=conflicts,
            tombstones=tombstones,
            device_ack=ack,
        )

    def list_conflicts(self, account_id: str) -> list[ConflictBranch]:
        """List open and resolved conflicts for one account."""
        return [
            conflict
            for conflict in self._conflicts.values()
            if conflict.account_id == account_id
        ]

    def resolve_conflict(
        self,
        account_id: str,
        conflict_id: str,
        selected_operation_id: str,
    ) -> ConflictBranch:
        """Apply a human choice as a new version without deleting either branch."""
        conflict = self._conflicts.get(conflict_id)
        if conflict is None or conflict.account_id != account_id:
            raise SyncError("冲突不存在或没有访问权限。")
        if conflict.status == ConflictBranchStatus.RESOLVED:
            raise SyncError("冲突已经裁决。")
        if selected_operation_id not in {
            conflict.local_operation_id,
            conflict.remote_operation_id,
        }:
            raise SyncError("只能选择冲突分支中的一个操作。")
        source_operation = self._operations.get(selected_operation_id)
        current = self._objects.get(_object_key(conflict.object_ref))
        if source_operation is None or current is None:
            raise SyncError("冲突历史或当前对象版本不存在。")
        selected_payload = (
            conflict.local_payload
            if selected_operation_id == conflict.local_operation_id
            else conflict.remote_payload
        )
        resolution_operation = SyncOperation(
            operation_id=f"resolution-{conflict_id}",
            account_id=account_id,
            device_id="server-human-review",
            object_ref=conflict.object_ref.model_copy(update={"version": current.version}),
            operation_type=source_operation.operation_type,
            base_version=current.version,
            causal_parent_ids=[
                conflict.local_operation_id,
                conflict.remote_operation_id,
            ],
            payload=dict(selected_payload),
            authorization_version=source_operation.authorization_version,
            key_epoch=source_operation.key_epoch,
            created_at=_now(),
            signature="server-resolution",
        )
        if source_operation.operation_type == SyncOperationType.DELETE:
            accepted, _ = self._apply_delete(resolution_operation, current)
        else:
            accepted = self._apply_upsert(resolution_operation, current)
        resolved = conflict.model_copy(
            update={
                "status": ConflictBranchStatus.RESOLVED,
                "resolved_at": _now(),
                "selected_operation_id": selected_operation_id,
                "resolution_operation_id": accepted.operation_id,
                "object_ref": accepted.object_ref,
            }
        )
        self._conflicts[conflict_id] = resolved
        self._persist()
        return resolved

    def device_ack(self, account_id: str, device_id: str) -> DeviceAck | None:
        """Return the latest acknowledgement for a device."""
        return self._device_acks.get((account_id, device_id))
