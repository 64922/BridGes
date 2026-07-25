"""In-memory adapters for exercising the vault port contracts.

These adapters are suitable for tests and logical prototypes. Production
implementations will replace them with device-local, encrypted server replica,
and persistent cloud control stores while keeping the same port signatures.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from science_companion.contracts.projects import ObjectDomain, ObjectRef
from science_companion.contracts.vault import (
    CapsuleIssueRequest,
    CapsuleStatus,
    CloudControlProjection,
    CloudProjectionStatus,
    ContentAuthority,
    DeviceUnavailableState,
    TemporaryTaskCapsule,
    VaultObject,
    VaultObjectCreateRequest,
    VaultObjectDomain,
    VaultObjectRef,
    VaultObjectSummary,
)
from science_companion.vault.ports import (
    CloudControlProjectionStore,
    DeviceVaultPort,
    VaultRepository,
)


class VaultError(Exception):
    """Domain exception for vault failures.

    The message is safe to expose to callers; it never leaks whether an object
    exists or belongs to another account.
    """


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash_content(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _new_id() -> str:
    return secrets.token_urlsafe(16)


class InMemoryCloudControlProjectionStore(CloudControlProjectionStore):
    """In-memory cloud control projection store for tests.

    Stores only hashes and metadata; full plaintext is held by the vault
    repository or device port.
    """

    def __init__(self) -> None:
        self._projections: dict[str, CloudControlProjection] = {}

    def _key(self, owner_id: str, object_id: str) -> str:
        return f"{owner_id}:{object_id}"

    def save_projection(
        self, projection: CloudControlProjection
    ) -> CloudControlProjection:
        self._projections[self._key(projection.object_ref.owner_id, projection.object_ref.object_id)] = projection
        return projection

    def get_projection(
        self, owner_id: str, object_id: str
    ) -> CloudControlProjection | None:
        return self._projections.get(self._key(owner_id, object_id))


class InMemoryVaultRepository(VaultRepository):
    """In-memory vault repository for tests and prototypes.

    This adapter can represent either a server-side encrypted replica or a
    device-local test store. Full plaintext is stored in memory only for test
    convenience; production implementations encrypt plaintext at rest.
    """

    def __init__(
        self,
        projection_store: CloudControlProjectionStore | None = None,
    ) -> None:
        self._objects: dict[str, VaultObject] = {}
        self._capsules: dict[str, TemporaryTaskCapsule] = {}
        self._contents: dict[str, bytes] = {}
        self._projection_store = projection_store or InMemoryCloudControlProjectionStore()

    def _key(self, owner_id: str, object_id: str) -> str:
        return f"{owner_id}:{object_id}"

    def create_object(self, request: VaultObjectCreateRequest) -> VaultObject:
        content_bytes = base64.b64decode(request.content)
        now = _now()
        object_id = _new_id()
        ref = VaultObjectRef(
            domain=VaultObjectDomain.PERSONAL_VAULT,
            owner_id=request.owner_account_id,
            object_id=object_id,
            version=1,
        )
        projection = CloudControlProjection(
            projection_id=_new_id(),
            object_ref=ref,
            content_hash=_hash_content(content_bytes),
            content_length=len(content_bytes),
            key_epoch=request.key_epoch,
            authorization_version=request.authorization_version,
            status=CloudProjectionStatus.ACTIVE,
            created_at=now,
            updated_at=now,
            expires_at=None,
        )
        obj = VaultObject(
            ref=ref,
            owner_account_id=request.owner_account_id,
            content_authority=request.content_authority,
            device_id=request.device_id,
            projection=projection,
            status=CloudProjectionStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        self._objects[self._key(ref.owner_id, ref.object_id)] = obj
        self._contents[self._key(ref.owner_id, ref.object_id)] = content_bytes
        self._projection_store.save_projection(projection)
        return obj

    def get_object(
        self, owner_id: str, object_id: str
    ) -> VaultObject | DeviceUnavailableState:
        key = self._key(owner_id, object_id)
        obj = self._objects.get(key)
        if obj is None:
            raise VaultError("对象不存在或没有访问权限。")
        return obj

    def list_objects(self, owner_id: str) -> list[VaultObjectSummary]:
        summaries: list[VaultObjectSummary] = []
        for obj in self._objects.values():
            if obj.owner_account_id != owner_id:
                continue
            summaries.append(
                VaultObjectSummary(
                    ref=obj.ref,
                    content_authority=obj.content_authority,
                    status=obj.status,
                    updated_at=obj.updated_at,
                )
            )
        summaries.sort(key=lambda s: s.updated_at, reverse=True)
        return summaries

    def issue_task_capsule(
        self, request: CapsuleIssueRequest
    ) -> TemporaryTaskCapsule:
        key = self._key(request.owner_account_id, request.object_id)
        obj = self._objects.get(key)
        if obj is None:
            raise VaultError("对象不存在或没有访问权限。")
        if obj.status != CloudProjectionStatus.ACTIVE:
            raise VaultError("对象当前不可用。")

        now = _now()
        expires = now + timedelta(seconds=request.ttl_seconds)
        capsule = TemporaryTaskCapsule(
            capsule_id=_new_id(),
            run_id=request.run_id,
            purpose=request.purpose,
            object_refs=[obj.ref],
            authorization_snapshot=obj.projection.authorization_version,
            key_epoch=obj.projection.key_epoch,
            issued_at=now,
            expires_at=expires,
            status=CapsuleStatus.ISSUED,
        )
        self._capsules[capsule.capsule_id] = capsule
        return capsule

    def revoke_capsule(self, owner_id: str, capsule_id: str) -> TemporaryTaskCapsule:
        capsule = self._capsules.get(capsule_id)
        if capsule is None:
            raise VaultError("胶囊不存在或没有访问权限。")
        if any(ref.owner_id != owner_id for ref in capsule.object_refs):
            raise VaultError("胶囊不存在或没有访问权限。")
        capsule.status = CapsuleStatus.REVOKED
        return capsule

    def get_cloud_projection(
        self, owner_id: str, object_id: str
    ) -> CloudControlProjection | None:
        return self._projection_store.get_projection(owner_id, object_id)

    # ------------------------------------------------------------------
    # Internal helpers used by the vault service or tests
    # ------------------------------------------------------------------

    def get_content(self, owner_id: str, object_id: str) -> bytes:
        """Return plaintext bytes stored in this repository."""
        key = self._key(owner_id, object_id)
        content = self._contents.get(key)
        if content is None:
            raise VaultError("对象不存在或没有访问权限。")
        return content


class MemoryDeviceVaultPort(DeviceVaultPort):
    """Device port that is always available and stores content in memory."""

    def __init__(self, repository: InMemoryVaultRepository) -> None:
        self._repository = repository

    def is_available(self, owner_id: str, device_id: str) -> bool:
        return True

    def get_content(
        self, owner_id: str, object_id: str
    ) -> bytes | DeviceUnavailableState:
        return self._repository.get_content(owner_id, object_id)


class UnavailableDeviceVaultPort(DeviceVaultPort):
    """Device port that simulates an unreachable device runtime."""

    def __init__(
        self,
        repository: InMemoryVaultRepository,
        *,
        reason: str = "device_offline",
    ) -> None:
        self._repository = repository
        self._reason = reason

    def is_available(self, owner_id: str, device_id: str) -> bool:
        return False

    def get_content(
        self, owner_id: str, object_id: str
    ) -> bytes | DeviceUnavailableState:
        obj = self._repository.get_object(owner_id, object_id)
        if isinstance(obj, DeviceUnavailableState):
            return obj
        return DeviceUnavailableState(
            object_ref=obj.ref,
            reason=self._reason,
            can_retry_at=None,
        )
