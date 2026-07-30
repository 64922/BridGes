"""In-memory adapters for exercising the vault port contracts.

These adapters are suitable for tests and logical prototypes. Production
implementations will replace them with device-local, encrypted server replica,
and persistent cloud control stores while keeping the same port signatures.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from cryptography.fernet import Fernet
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from science_companion.contracts.vault import (
    CapsuleIssueRequest,
    CapsuleStatus,
    CloudControlProjection,
    CloudProjectionStatus,
    DeviceCertificate,
    DevicePairingRequest,
    DevicePairingResponse,
    DevicePairingStatus,
    DeviceRevocationRequest,
    DeviceUnavailableState,
    KeyEpoch,
    TemporaryTaskCapsule,
    VaultObject,
    VaultObjectCreateRequest,
    VaultObjectRef,
    VaultObjectSummary,
    VaultRuntime,
)
from science_companion.persistence import StateStore
from science_companion.vault.ports import (
    CloudControlProjectionStore,
    DeviceKeychainPort,
    DevicePairingRepository,
    DeviceVaultPort,
    VaultEncryptionPort,
    VaultRepository,
)


class VaultError(Exception):
    """Domain exception for vault failures.

    The message is safe to expose to callers; it never leaks whether an object
    exists or belongs to another account.
    """


def _now() -> datetime:
    return datetime.now(UTC)


def _hash_content(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _new_id() -> str:
    return secrets.token_urlsafe(16)


class InMemoryCloudControlProjectionStore(CloudControlProjectionStore):
    """In-memory cloud control projection store for tests.

    Stores only hashes and metadata; full plaintext is held by the vault
    repository or device port.
    """

    def __init__(self, state_store: StateStore | None = None) -> None:
        self._projections: dict[str, CloudControlProjection] = {}
        self._state_store = state_store
        self._load_state()

    def _load_state(self) -> None:
        if self._state_store is None:
            return
        state = self._state_store.load("vault_projections") or {}
        self._projections = {
            key: CloudControlProjection.model_validate(value)
            for key, value in state.get("projections", {}).items()
        }

    def _persist(self) -> None:
        if self._state_store is None:
            return
        self._state_store.save(
            "vault_projections",
            {
                "projections": {
                    key: projection.model_dump(mode="json")
                    for key, projection in self._projections.items()
                }
            },
        )

    def _key(self, owner_id: str, object_id: str) -> str:
        return f"{owner_id}:{object_id}"

    def save_projection(
        self, projection: CloudControlProjection
    ) -> CloudControlProjection:
        key = self._key(
            projection.object_ref.owner_id, projection.object_ref.object_id
        )
        self._projections[key] = projection
        self._persist()
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
        state_store: StateStore | None = None,
    ) -> None:
        self._objects: dict[str, VaultObject] = {}
        self._capsules: dict[str, TemporaryTaskCapsule] = {}
        self._contents: dict[str, bytes] = {}
        self._device_local_wrapped_keys: dict[str, bytes] = {}
        self._state_store = state_store
        self._projection_store = projection_store or InMemoryCloudControlProjectionStore(
            state_store=state_store
        )
        self._load_state()

    def _load_state(self) -> None:
        if self._state_store is None:
            return
        state = self._state_store.load("vault") or {}
        self._objects = {
            key: VaultObject.model_validate(value)
            for key, value in state.get("objects", {}).items()
        }
        self._capsules = {
            key: TemporaryTaskCapsule.model_validate(value)
            for key, value in state.get("capsules", {}).items()
        }
        self._contents = {
            key: base64.b64decode(value)
            for key, value in state.get("contents", {}).items()
        }
        self._device_local_wrapped_keys = {
            key: base64.b64decode(value)
            for key, value in state.get("wrapped_keys", {}).items()
        }

    def _persist(self) -> None:
        if self._state_store is None:
            return
        self._state_store.save(
            "vault",
            {
                "objects": {
                    key: value.model_dump(mode="json")
                    for key, value in self._objects.items()
                },
                "capsules": {
                    key: value.model_dump(mode="json")
                    for key, value in self._capsules.items()
                },
                "contents": {
                    key: base64.b64encode(value).decode("ascii")
                    for key, value in self._contents.items()
                },
                "wrapped_keys": {
                    key: base64.b64encode(value).decode("ascii")
                    for key, value in self._device_local_wrapped_keys.items()
                },
            },
        )

    def _key(self, owner_id: str, object_id: str) -> str:
        return f"{owner_id}:{object_id}"

    def create_object(self, request: VaultObjectCreateRequest) -> VaultObject:
        content_bytes = base64.b64decode(request.content)
        # When content is encrypted locally, the caller supplies the hash and
        # length of the authoritative plaintext so the cloud projection stays
        # minimal and accurate.
        content_hash = request.content_hash or _hash_content(content_bytes)
        content_length = (
            request.content_length
            if request.content_length is not None
            else len(content_bytes)
        )
        now = _now()
        object_id = _new_id()
        ref = VaultObjectRef(
            domain=request.domain,
            owner_id=request.owner_account_id,
            object_id=object_id,
            version=1,
        )
        projection = CloudControlProjection(
            projection_id=_new_id(),
            object_ref=ref,
            content_hash=content_hash,
            content_length=content_length,
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
        self._persist()
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
        self._persist()
        return capsule

    def get_capsule(self, owner_id: str, capsule_id: str) -> TemporaryTaskCapsule:
        """Return a capsule without mutating state."""
        capsule = self._capsules.get(capsule_id)
        if capsule is None:
            raise VaultError("胶囊不存在或没有访问权限。")
        if any(ref.owner_id != owner_id for ref in capsule.object_refs):
            raise VaultError("胶囊不存在或没有访问权限。")
        return capsule

    def revoke_capsule(self, owner_id: str, capsule_id: str) -> TemporaryTaskCapsule:
        capsule = self._capsules.get(capsule_id)
        if capsule is None:
            raise VaultError("胶囊不存在或没有访问权限。")
        if any(ref.owner_id != owner_id for ref in capsule.object_refs):
            raise VaultError("胶囊不存在或没有访问权限。")
        capsule.status = CapsuleStatus.REVOKED
        self._persist()
        return capsule

    def get_cloud_projection(
        self, owner_id: str, object_id: str
    ) -> CloudControlProjection | None:
        return self._projection_store.get_projection(owner_id, object_id)

    def store_device_local_wrapped_key(
        self, owner_id: str, object_id: str, wrapped_key: bytes
    ) -> None:
        """Store the wrapped data key for a device-local encrypted object."""
        self._device_local_wrapped_keys[self._key(owner_id, object_id)] = wrapped_key
        self._persist()

    def get_device_local_wrapped_key(
        self, owner_id: str, object_id: str
    ) -> bytes:
        """Return the wrapped data key for a device-local encrypted object."""
        wrapped = self._device_local_wrapped_keys.get(self._key(owner_id, object_id))
        if wrapped is None:
            raise VaultError("对象不存在或没有访问权限。")
        return wrapped

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


class InMemoryDeviceKeychain(DeviceKeychainPort):
    """Test adapter that simulates a system keychain.

    Production implementations must use OS-specific secure storage (Windows
    DPAPI, macOS Keychain, Linux Secret Service, or a secure enclave) and must
    never write the private key to ordinary configuration or logs.
    """

    def __init__(self) -> None:
        self._private_keys: dict[str, str] = {}
        self._wrapping_keys: dict[str, bytes] = {}

    def _key(self, account_id: str, device_id: str) -> str:
        return f"{account_id}:{device_id}"

    def store_private_key(
        self, account_id: str, device_id: str, private_key_pem: str
    ) -> None:
        self._private_keys[self._key(account_id, device_id)] = private_key_pem

    def get_private_key(self, account_id: str, device_id: str) -> str | None:
        return self._private_keys.get(self._key(account_id, device_id))

    def delete_private_key(self, account_id: str, device_id: str) -> None:
        self._private_keys.pop(self._key(account_id, device_id), None)

    def store_wrapping_key(
        self, account_id: str, device_id: str, wrapping_key: bytes
    ) -> None:
        self._wrapping_keys[self._key(account_id, device_id)] = wrapping_key

    def get_wrapping_key(self, account_id: str, device_id: str) -> bytes | None:
        return self._wrapping_keys.get(self._key(account_id, device_id))

    def delete_wrapping_key(self, account_id: str, device_id: str) -> None:
        self._wrapping_keys.pop(self._key(account_id, device_id), None)


class FernetVaultEncryptionAdapter(VaultEncryptionPort):
    """Symmetric encryption adapter using Fernet.

    This adapter satisfies the VaultEncryptionPort contract for tests and
    logical prototypes. Production may replace it with an AEAD scheme that
    matches the target threat model and compliance requirements.
    """

    def generate_data_key(self) -> bytes:
        return Fernet.generate_key()

    def generate_wrapping_key(self) -> bytes:
        return Fernet.generate_key()

    def encrypt(self, plaintext: bytes, key: bytes) -> bytes:
        return Fernet(key).encrypt(plaintext)

    def decrypt(self, ciphertext: bytes, key: bytes) -> bytes:
        return Fernet(key).decrypt(ciphertext)

    def wrap_key(self, data_key: bytes, wrapping_key: bytes) -> bytes:
        return Fernet(wrapping_key).encrypt(data_key)

    def unwrap_key(self, wrapped_key: bytes, wrapping_key: bytes) -> bytes:
        return Fernet(wrapping_key).decrypt(wrapped_key)


class InMemoryDevicePairingRepository(DevicePairingRepository):
    """In-memory store for device certificates and key epochs."""

    def __init__(self, state_store: StateStore | None = None) -> None:
        self._certificates: dict[str, DeviceCertificate] = {}
        self._epochs: dict[str, KeyEpoch] = {}
        self._state_store = state_store
        self._load_state()

    def _load_state(self) -> None:
        if self._state_store is None:
            return
        state = self._state_store.load("device_pairing") or {}
        self._certificates = {
            key: DeviceCertificate.model_validate(value)
            for key, value in state.get("certificates", {}).items()
        }
        self._epochs = {
            key: KeyEpoch.model_validate(value)
            for key, value in state.get("epochs", {}).items()
        }

    def _persist(self) -> None:
        if self._state_store is None:
            return
        self._state_store.save(
            "device_pairing",
            {
                "certificates": {
                    key: value.model_dump(mode="json")
                    for key, value in self._certificates.items()
                },
                "epochs": {
                    key: value.model_dump(mode="json")
                    for key, value in self._epochs.items()
                },
            },
        )

    def _key(self, account_id: str, device_id: str) -> str:
        return f"{account_id}:{device_id}"

    def save_certificate(
        self, certificate: DeviceCertificate
    ) -> DeviceCertificate:
        self._certificates[self._key(certificate.account_id, certificate.device_id)] = certificate
        self._persist()
        return certificate

    def get_certificate(
        self, account_id: str, device_id: str
    ) -> DeviceCertificate | None:
        return self._certificates.get(self._key(account_id, device_id))

    def list_certificates(self, account_id: str) -> list[DeviceCertificate]:
        return [
            cert
            for cert in self._certificates.values()
            if cert.account_id == account_id
        ]

    def save_key_epoch(self, epoch: KeyEpoch) -> KeyEpoch:
        self._epochs[self._key(epoch.account_id, epoch.device_id)] = epoch
        self._persist()
        return epoch

    def get_active_epoch(
        self, account_id: str, device_id: str
    ) -> KeyEpoch | None:
        epoch = self._epochs.get(self._key(account_id, device_id))
        if epoch is None or epoch.status != DevicePairingStatus.PAIRED:
            return None
        return epoch


def _generate_key_pair() -> tuple[str, str]:
    """Generate an RSA key pair and return (private_key_pem, public_key_pem)."""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    return private_pem, public_pem


def _fingerprint_public_key(public_key_pem: str) -> str:
    """Return a deterministic fingerprint of a PEM public key."""
    return hashlib.sha256(public_key_pem.encode("utf-8")).hexdigest()


class DevicePairingService:
    """Application service for pairing devices and managing key epochs.

    This service is intentionally separate from VaultService so that the
    identity/security boundary (certificates and epochs) can be tested and
    replaced independently from object encryption.
    """

    def __init__(
        self,
        pairing_repository: DevicePairingRepository,
        device_keychain: DeviceKeychainPort,
        vault_encryption: VaultEncryptionPort,
    ) -> None:
        self._pairing_repository = pairing_repository
        self._device_keychain = device_keychain
        self._vault_encryption = vault_encryption

    def pair_device(
        self, account_id: str, request: DevicePairingRequest
    ) -> DevicePairingResponse:
        device_id = _new_id()
        private_pem, public_pem = _generate_key_pair()
        wrapping_key = self._vault_encryption.generate_wrapping_key()
        fingerprint = _fingerprint_public_key(public_pem)
        now = _now()
        epoch = KeyEpoch(
            epoch_id=_new_id(),
            account_id=account_id,
            device_id=device_id,
            status=DevicePairingStatus.PAIRED,
            created_at=now,
        )
        certificate = DeviceCertificate(
            certificate_id=_new_id(),
            account_id=account_id,
            device_id=device_id,
            device_name=request.device_name,
            device_type=request.device_type,
            public_key_pem=public_pem,
            fingerprint=fingerprint,
            status=DevicePairingStatus.PAIRED,
            key_epoch=epoch.epoch_id,
            paired_at=now,
        )
        runtime = VaultRuntime(
            runtime_id=_new_id(),
            account_id=account_id,
            device_id=device_id,
            certificate_id=certificate.certificate_id,
            version="0.1.0",
            capabilities=["encrypted_storage"],
            paired_at=now,
        )
        self._device_keychain.store_private_key(account_id, device_id, private_pem)
        self._device_keychain.store_wrapping_key(account_id, device_id, wrapping_key)
        self._pairing_repository.save_key_epoch(epoch)
        self._pairing_repository.save_certificate(certificate)
        return DevicePairingResponse(
            certificate=certificate,
            key_epoch=epoch,
            runtime=runtime,
        )

    def revoke_device(
        self, account_id: str, request: DeviceRevocationRequest
    ) -> DeviceCertificate:
        certificate = self._pairing_repository.get_certificate(
            account_id, request.device_id
        )
        if certificate is None:
            raise VaultError("设备不存在或没有访问权限。")
        now = _now()
        certificate.status = DevicePairingStatus.REVOKED
        certificate.revoked_at = now
        self._device_keychain.delete_private_key(account_id, request.device_id)
        self._device_keychain.delete_wrapping_key(account_id, request.device_id)
        active_epoch = self._pairing_repository.get_active_epoch(
            account_id, request.device_id
        )
        if active_epoch is not None:
            active_epoch.status = DevicePairingStatus.REVOKED
            active_epoch.revoked_at = now
            self._pairing_repository.save_key_epoch(active_epoch)
        return self._pairing_repository.save_certificate(certificate)

    def list_device_certificates(
        self, account_id: str
    ) -> list[DeviceCertificate]:
        return self._pairing_repository.list_certificates(account_id)

    def get_active_certificate(
        self, account_id: str, device_id: str
    ) -> DeviceCertificate | None:
        certificate = self._pairing_repository.get_certificate(account_id, device_id)
        if certificate is None or certificate.status != DevicePairingStatus.PAIRED:
            return None
        return certificate
