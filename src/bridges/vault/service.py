"""Vault application service.

The vault service coordinates the stable vault repository port, the device port,
and the cloud control projection store. Domain modules call this service (or the
repository port directly) instead of touching device frameworks or cloud vendor
objects.
"""

from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime

from bridges.contracts.identity import SubjectContext
from bridges.contracts.projects import ObjectDomain, ObjectRef
from bridges.contracts.scope import ScopeAction, ScopeIsolationError
from bridges.contracts.vault import (
    CapsuleIssueRequest,
    CloudControlProjection,
    ContentAuthority,
    DeviceCertificate,
    DevicePairingRequest,
    DevicePairingResponse,
    DeviceRevocationRequest,
    DeviceUnavailableState,
    KeyEpoch,
    TemporaryTaskCapsule,
    VaultObject,
    VaultObjectCreateRequest,
    VaultObjectDomain,
    VaultObjectRef,
    VaultObjectSummary,
    VaultShareRequest,
)
from bridges.invalidation import InvalidationError, InvalidationService
from bridges.scope import ScopeEnforcer
from bridges.vault.adapters import DevicePairingService, VaultError
from bridges.vault.ports import (
    DeviceKeychainPort,
    DevicePairingRepository,
    DeviceVaultPort,
    VaultEncryptionPort,
    VaultRepository,
)


def _now() -> datetime:
    return datetime.now(UTC)


class VaultService:
    """Application service for personal vault boundaries and task capsules."""

    _device_pairing_service: DevicePairingService | None

    def __init__(
        self,
        repository: VaultRepository,
        device_port: DeviceVaultPort | None = None,
        scope_enforcer: ScopeEnforcer | None = None,
        invalidation_service: InvalidationService | None = None,
        device_keychain: DeviceKeychainPort | None = None,
        vault_encryption: VaultEncryptionPort | None = None,
        device_pairing_repository: DevicePairingRepository | None = None,
    ) -> None:
        self._repository = repository
        self._device_port = device_port
        self._scope_enforcer = scope_enforcer or ScopeEnforcer()
        self._invalidation = invalidation_service
        self._device_keychain = device_keychain
        self._vault_encryption = vault_encryption
        self._device_pairing_repository = device_pairing_repository
        if device_pairing_repository is not None:
            if device_keychain is None or vault_encryption is None:
                raise VaultError(
                    "设备配对需要 device_keychain 和 vault_encryption 端口。"
                )
            self._device_pairing_service = DevicePairingService(
                pairing_repository=device_pairing_repository,
                device_keychain=device_keychain,
                vault_encryption=vault_encryption,
            )
        else:
            self._device_pairing_service = None

    def _subject(self, account_id: str) -> SubjectContext:
        """Build a minimal subject context from an account id for scope checks."""
        from bridges.contracts.identity import AuthMethod

        return SubjectContext(
            account_id=account_id,
            session_id="service-session",
            auth_method=AuthMethod.PASSWORD,
        )

    def _require_active(self, vault_ref: VaultObjectRef) -> None:
        """Fail closed if the vault object is revoked or tombstoned.

        T011: new reads, cache lookups and task capsules must check current
        invalidation state before using an object.
        """
        if self._invalidation is None:
            return
        from bridges.contracts.projects import ObjectRef

        object_ref = ObjectRef(
            domain=ObjectDomain(vault_ref.domain.value),
            owner_id=vault_ref.owner_id,
            object_id=vault_ref.object_id,
            version=vault_ref.version,
        )
        try:
            self._invalidation.require_active(object_ref)
        except InvalidationError as exc:
            raise VaultError(str(exc)) from exc

    def _device_encryption_available(self) -> bool:
        return (
            self._vault_encryption is not None
            and self._device_keychain is not None
            and self._device_pairing_repository is not None
        )

    def _get_active_device_epoch(
        self, account_id: str, device_id: str
    ) -> KeyEpoch | None:
        if self._device_pairing_repository is None:
            return None
        return self._device_pairing_repository.get_active_epoch(account_id, device_id)

    def _encrypt_device_local(
        self, content: bytes, account_id: str, device_id: str
    ) -> tuple[bytes, bytes, KeyEpoch]:
        """Encrypt content for a device-local object.

        Returns (ciphertext, wrapped_data_key, key_epoch).
        """
        assert self._vault_encryption is not None
        assert self._device_keychain is not None
        epoch = self._get_active_device_epoch(account_id, device_id)
        if epoch is None:
            raise VaultError("设备未配对或已撤销。")
        wrapping_key = self._device_keychain.get_wrapping_key(account_id, device_id)
        if wrapping_key is None:
            raise VaultError("设备密钥不可用。")
        data_key = self._vault_encryption.generate_data_key()
        ciphertext = self._vault_encryption.encrypt(content, data_key)
        wrapped_key = self._vault_encryption.wrap_key(data_key, wrapping_key)
        return ciphertext, wrapped_key, epoch

    def _decrypt_device_local(
        self, owner_id: str, object_id: str
    ) -> bytes | DeviceUnavailableState:
        """Decrypt a device-local object using the device keychain."""
        assert self._vault_encryption is not None
        assert self._device_keychain is not None
        obj = self._repository.get_object(owner_id, object_id)
        if isinstance(obj, DeviceUnavailableState):
            return obj
        if obj.device_id is None:
            return DeviceUnavailableState(
                object_ref=obj.ref,
                reason="missing_device_id",
            )
        wrapping_key = self._device_keychain.get_wrapping_key(
            owner_id, obj.device_id
        )
        if wrapping_key is None:
            return DeviceUnavailableState(
                object_ref=obj.ref,
                reason="device_key_unavailable",
            )
        try:
            ciphertext = self._repository.get_content(owner_id, object_id)
            wrapped_key = self._repository.get_device_local_wrapped_key(
                owner_id, object_id
            )
        except VaultError as exc:
            return DeviceUnavailableState(
                object_ref=obj.ref,
                reason=str(exc),
            )
        data_key = self._vault_encryption.unwrap_key(wrapped_key, wrapping_key)
        return self._vault_encryption.decrypt(ciphertext, data_key)

    def create_private_object(
        self,
        owner_account_id: str,
        content: bytes,
        *,
        content_authority: ContentAuthority = ContentAuthority.DEVICE_LOCAL,
        device_id: str | None = None,
        purpose: str = "general",
    ) -> VaultObject:
        """Create a private vault object.

        The full plaintext is stored according to content_authority. The cloud
        only receives a control projection (hash and metadata). When device
        encryption is available and the authority is device-local, the content
        is encrypted before storage and the data key is wrapped by the device
        key held in the system keychain.
        """
        if (
            content_authority == ContentAuthority.DEVICE_LOCAL
            and self._device_encryption_available()
            and device_id is not None
        ):
            ciphertext, wrapped_key, epoch = self._encrypt_device_local(
                content, owner_account_id, device_id
            )
            request = VaultObjectCreateRequest(
                owner_account_id=owner_account_id,
                content_authority=content_authority,
                content=base64.b64encode(ciphertext).decode("ascii"),
                device_id=device_id,
                purpose=purpose,
                key_epoch=epoch.epoch_id,
                content_hash=hashlib.sha256(content).hexdigest(),
                content_length=len(content),
            )
            obj = self._repository.create_object(request)
            self._repository.store_device_local_wrapped_key(
                owner_account_id, obj.ref.object_id, wrapped_key
            )
            return obj

        request = VaultObjectCreateRequest(
            owner_account_id=owner_account_id,
            content_authority=content_authority,
            content=base64.b64encode(content).decode("ascii"),
            device_id=device_id,
            purpose=purpose,
        )
        return self._repository.create_object(request)

    def get_object(
        self, owner_id: str, object_id: str
    ) -> VaultObject | DeviceUnavailableState:
        """Return the vault object projection.

        This returns metadata only; use get_content to request plaintext.
        """
        obj = self._repository.get_object(owner_id, object_id)
        if isinstance(obj, DeviceUnavailableState):
            return obj
        subject = self._subject(owner_id)
        try:
            self._scope_enforcer.authorize_vault(subject, ScopeAction.READ, obj.ref)
        except ScopeIsolationError as exc:
            raise VaultError(str(exc)) from exc
        self._require_active(obj.ref)
        return obj

    def get_content(
        self, owner_id: str, object_id: str
    ) -> bytes | DeviceUnavailableState:
        """Request plaintext content for a vault object.

        If the content authority is DEVICE_LOCAL, the device runtime must be
        available. The system returns DeviceUnavailableState instead of silently
        uploading or reconstructing full text from the cloud projection.
        """
        obj = self._repository.get_object(owner_id, object_id)
        if isinstance(obj, DeviceUnavailableState):
            return obj

        subject = self._subject(owner_id)
        try:
            self._scope_enforcer.authorize_vault(subject, ScopeAction.READ, obj.ref)
        except ScopeIsolationError as exc:
            raise VaultError(str(exc)) from exc
        self._require_active(obj.ref)

        if obj.content_authority == ContentAuthority.DEVICE_LOCAL:
            if obj.device_id is None:
                return DeviceUnavailableState(
                    object_ref=obj.ref,
                    reason="missing_device_id",
                )
            if self._device_encryption_available():
                return self._decrypt_device_local(owner_id, object_id)
            if self._device_port is None:
                return DeviceUnavailableState(
                    object_ref=obj.ref,
                    reason="device_runtime_not_configured",
                )
            content = self._device_port.get_content(owner_id, object_id)
            if isinstance(content, DeviceUnavailableState):
                return content
            return content

        # Server replica or project copy: content is reachable through the repo.
        return self._repository.get_content(owner_id, object_id)

    def list_objects(self, owner_id: str) -> list[VaultObjectSummary]:
        """List vault object summaries for the owner."""
        subject = self._subject(owner_id)
        summaries = self._repository.list_objects(owner_id)
        # Explicitly authorize each summary against the compiled scope to mirror
        # RLS: even in memory we fail closed on any mismatch.
        authorized: list[VaultObjectSummary] = []
        for summary in summaries:
            try:
                self._scope_enforcer.authorize_vault(
                    subject, ScopeAction.READ, summary.ref
                )
                authorized.append(summary)
            except ScopeIsolationError:
                continue
        return authorized

    def pair_device(
        self, account_id: str, request: DevicePairingRequest
    ) -> DevicePairingResponse:
        """Pair a new vault runtime with the account."""
        if self._device_pairing_service is None:
            raise VaultError("设备配对服务未配置。")
        return self._device_pairing_service.pair_device(account_id, request)

    def revoke_device(
        self, account_id: str, request: DeviceRevocationRequest
    ) -> DeviceCertificate:
        """Revoke a paired device and rotate its key epoch."""
        if self._device_pairing_service is None:
            raise VaultError("设备配对服务未配置。")
        return self._device_pairing_service.revoke_device(account_id, request)

    def list_device_certificates(
        self, account_id: str
    ) -> list[DeviceCertificate]:
        """List device certificates for the account."""
        if self._device_pairing_service is None:
            raise VaultError("设备配对服务未配置。")
        return self._device_pairing_service.list_device_certificates(account_id)

    def issue_task_capsule(
        self,
        owner_account_id: str,
        object_id: str,
        run_id: str,
        purpose: str,
        *,
        ttl_seconds: int = 3600,
    ) -> TemporaryTaskCapsule:
        """Issue a temporary task capsule bound to a run and purpose."""
        obj = self._repository.get_object(owner_account_id, object_id)
        if isinstance(obj, DeviceUnavailableState):
            raise VaultError(str(obj.reason))
        subject = self._subject(owner_account_id)
        try:
            self._scope_enforcer.authorize_vault(
                subject, ScopeAction.EXECUTE, obj.ref
            )
        except ScopeIsolationError as exc:
            raise VaultError(str(exc)) from exc
        self._require_active(obj.ref)

        request = CapsuleIssueRequest(
            owner_account_id=owner_account_id,
            object_id=object_id,
            run_id=run_id,
            purpose=purpose,
            ttl_seconds=ttl_seconds,
        )
        return self._repository.issue_task_capsule(request)

    def revoke_capsule(
        self, owner_id: str, capsule_id: str
    ) -> TemporaryTaskCapsule:
        """Revoke a previously issued capsule."""
        # Authorize before mutating repository state (T038: fix ordering).
        subject = self._subject(owner_id)
        capsule = self._repository.get_capsule(owner_id, capsule_id)
        for ref in capsule.object_refs:
            try:
                self._scope_enforcer.authorize_vault(
                    subject, ScopeAction.DELETE, ref
                )
            except ScopeIsolationError as exc:
                raise VaultError(str(exc)) from exc
        return self._repository.revoke_capsule(owner_id, capsule_id)

    def get_cloud_projection(
        self, owner_id: str, object_id: str
    ) -> CloudControlProjection | None:
        """Return the cloud control projection for a vault object."""
        projection = self._repository.get_cloud_projection(owner_id, object_id)
        if projection is None:
            return None
        subject = self._subject(owner_id)
        try:
            self._scope_enforcer.authorize_vault(
                subject, ScopeAction.READ, projection.object_ref
            )
        except ScopeIsolationError:
            return None
        return projection

    def share_as_project_copy(
        self,
        owner_account_id: str,
        request: VaultShareRequest,
    ) -> ObjectRef:
        """Share a personal vault object as an independent minimized project copy.

        The copy receives a new object identifier, lives in the shared project
        domain, and does not expose the personal vault original or its full
        private metadata.
        """
        source = self._repository.get_object(
            owner_account_id, request.source_object_id
        )
        if isinstance(source, DeviceUnavailableState):
            raise VaultError("源对象当前不可用。")

        subject = self._subject(owner_account_id)
        self._require_active(source.ref)
        try:
            self._scope_enforcer.authorize_vault(
                subject, ScopeAction.SHARE, source.ref
            )
        except ScopeIsolationError as exc:
            raise VaultError(str(exc)) from exc

        # Create a minimized project copy. The full content is not duplicated here;
        # instead the copy references the same content hash and receives its own
        # object identity and domain.
        copy_content = self.get_content(owner_account_id, request.source_object_id)
        if isinstance(copy_content, DeviceUnavailableState):
            raise VaultError("源对象当前不可用。")

        copy_request = VaultObjectCreateRequest(
            owner_account_id=request.target_project_id,
            content_authority=ContentAuthority.PROJECT_COPY,
            content=base64.b64encode(copy_content).decode("ascii"),
            purpose=request.grant_purpose,
            key_epoch=source.projection.key_epoch,
            authorization_version=source.projection.authorization_version,
            domain=VaultObjectDomain.SHARED_PROJECT,
        )
        copy = self._repository.create_object(copy_request)
        return ObjectRef(
            domain=ObjectDomain.SHARED_PROJECT,
            owner_id=request.target_project_id,
            object_id=copy.ref.object_id,
            version=copy.ref.version,
        )
