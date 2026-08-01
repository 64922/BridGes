"""Regression tests for control-first causal synchronization."""

from datetime import UTC, datetime

from bridges.contracts.projects import ObjectDomain, ObjectRef
from bridges.contracts.sync import (
    DeviceSyncStatus,
    SyncExchangeRequest,
    SyncOperation,
    SyncOperationStatus,
    SyncOperationType,
)
from bridges.sync import SyncService


def _operation(
    *,
    account_id: str = "alice",
    device_id: str = "laptop",
    object_id: str = "object-1",
    base_version: int = 0,
    payload: dict[str, object] | None = None,
    operation_type: SyncOperationType = SyncOperationType.UPSERT,
    key_epoch: str = "epoch-1",
    authorization_version: str = "authz-1",
    operation_id: str = "op-1",
) -> SyncOperation:
    return SyncOperation(
        operation_id=operation_id,
        account_id=account_id,
        device_id=device_id,
        object_ref=ObjectRef(
            domain=ObjectDomain.PERSONAL_VAULT,
            owner_id=account_id,
            object_id=object_id,
            version=base_version,
        ),
        operation_type=operation_type,
        base_version=base_version,
        payload=payload or {"title": "draft"},
        authorization_version=authorization_version,
        key_epoch=key_epoch,
        created_at=datetime.now(UTC),
        signature="test-signature",
    )


def _exchange(service: SyncService, operation: SyncOperation):
    return service.exchange(
        operation.account_id,
        SyncExchangeRequest(
            device_id=operation.device_id,
            key_epoch=operation.key_epoch,
            authorization_version=operation.authorization_version,
            operations=[operation],
        ),
    )


def test_control_state_is_pulled_before_accepting_an_operation() -> None:
    service = SyncService()
    service.register_device("alice", "laptop", "epoch-1")

    first = _exchange(service, _operation())

    assert first.control.key_epoch == "epoch-1"
    assert [operation.operation_id for operation in first.accepted_operations] == ["op-1"]
    assert first.device_ack.status == DeviceSyncStatus.ACTIVE


def test_unpaired_device_is_not_allowed_to_submit_operations() -> None:
    result = _exchange(SyncService(), _operation())

    assert result.control.device_status == DeviceSyncStatus.AWAITING_ACK
    assert result.accepted_operations == []
    assert result.quarantined_operations[0].status == SyncOperationStatus.QUARANTINED
    assert result.device_ack.status == DeviceSyncStatus.AWAITING_ACK


def test_replaying_same_operation_is_idempotent() -> None:
    service = SyncService()
    service.register_device("alice", "laptop", "epoch-1")
    operation = _operation()

    first = _exchange(service, operation)
    replay = _exchange(service, operation)

    assert first.accepted_operations[0].object_ref.version == 1
    assert replay.accepted_operations[0].object_ref.version == 1
    assert replay.control.latest_versions["personal_vault:alice:object-1"] == 1


def test_operation_id_collision_cannot_replace_an_accepted_operation() -> None:
    service = SyncService()
    service.register_device("alice", "laptop", "epoch-1")
    original = _operation()
    _exchange(service, original)

    collision = _operation(payload={"title": "forged"})
    result = _exchange(service, collision)
    replay = _exchange(service, original)

    assert result.accepted_operations == []
    assert result.quarantined_operations[0].status == SyncOperationStatus.QUARANTINED
    assert replay.accepted_operations[0].payload == original.payload
    assert replay.control.latest_versions["personal_vault:alice:object-1"] == 1


def test_old_key_epoch_is_quarantined_before_content_is_applied() -> None:
    service = SyncService()
    service.register_device("alice", "laptop", "epoch-2")

    result = _exchange(service, _operation(key_epoch="epoch-1"))

    assert result.accepted_operations == []
    assert result.quarantined_operations[0].status == SyncOperationStatus.QUARANTINED
    assert "旧密钥时期" in (result.quarantined_operations[0].quarantine_reason or "")


def test_missing_device_signature_is_quarantined() -> None:
    service = SyncService()
    service.register_device("alice", "laptop", "epoch-1")
    operation = _operation().model_copy(update={"signature": None})

    result = _exchange(service, operation)

    assert result.accepted_operations == []
    assert "签名" in (result.quarantined_operations[0].quarantine_reason or "")


def test_revoked_device_operations_are_isolated() -> None:
    service = SyncService()
    service.register_device("alice", "laptop", "epoch-1")
    service.revoke_device("alice", "laptop")

    result = _exchange(service, _operation())

    assert result.control.device_status == DeviceSyncStatus.REVOKED
    assert result.device_ack.status == DeviceSyncStatus.REVOKED
    assert result.quarantined_operations[0].status == SyncOperationStatus.QUARANTINED


def test_tombstone_wins_over_an_offline_edit() -> None:
    service = SyncService()
    service.register_device("alice", "laptop", "epoch-1")
    service.register_device("alice", "phone", "epoch-2")
    _exchange(service, _operation())

    deleted = _exchange(
        service,
        _operation(
            device_id="phone",
            key_epoch="epoch-2",
            operation_id="delete-1",
            base_version=1,
            operation_type=SyncOperationType.DELETE,
            payload={},
        ),
    )
    assert deleted.tombstones

    stale_edit = _exchange(
        service,
        _operation(
            operation_id="offline-edit",
            base_version=1,
            payload={"title": "offline resurrection"},
        ),
    )
    assert stale_edit.accepted_operations == []
    assert "墓碑" in (stale_edit.quarantined_operations[0].quarantine_reason or "")


def test_scientific_semantic_concurrent_edit_creates_two_branches() -> None:
    service = SyncService()
    service.register_device("alice", "laptop", "epoch-1")
    service.register_device("alice", "phone", "epoch-2")
    _exchange(service, _operation())
    _exchange(
        service,
        _operation(
            device_id="phone",
            key_epoch="epoch-2",
            operation_id="remote-claim",
            base_version=1,
            payload={"claim": "有限证据提示相关"},
        ),
    )

    result = _exchange(
        service,
        _operation(
            operation_id="local-claim",
            base_version=1,
            payload={"claim": "已确立因果"},
        ),
    )

    assert result.accepted_operations == []
    assert len(result.conflicts) == 1
    conflict = result.conflicts[0]
    assert conflict.local_operation_id == "local-claim"
    assert conflict.remote_operation_id == "remote-claim"


def test_concurrent_delete_creates_conflict_and_resolution_writes_new_version() -> None:
    service = SyncService()
    service.register_device("alice", "laptop", "epoch-1")
    service.register_device("alice", "phone", "epoch-2")
    _exchange(service, _operation())
    _exchange(
        service,
        _operation(
            device_id="phone",
            key_epoch="epoch-2",
            operation_id="remote-edit",
            base_version=1,
            payload={"claim": "remote"},
        ),
    )

    conflict_result = _exchange(
        service,
        _operation(
            operation_id="local-delete",
            operation_type=SyncOperationType.DELETE,
            base_version=1,
            payload={},
        ),
    )

    assert conflict_result.tombstones == []
    assert len(conflict_result.conflicts) == 1
    resolved = service.resolve_conflict(
        "alice",
        conflict_result.conflicts[0].conflict_id,
        "local-delete",
    )

    assert resolved.status.value == "resolved"
    assert resolved.resolution_operation_id is not None
    assert service.pull_control("alice", "laptop").tombstones
    assert service.pull_control("alice", "laptop").latest_versions[
        "personal_vault:alice:object-1"
    ] == 3
