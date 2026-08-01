"""Stable persistence ports for the personal vault boundary.

Domain modules depend only on these ports, never on device frameworks, the local
file system, or cloud vendor objects directly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from bridges.contracts.vault import (
    CapsuleIssueRequest,
    CloudControlProjection,
    DeviceCertificate,
    DeviceUnavailableState,
    KeyEpoch,
    TemporaryTaskCapsule,
    VaultObject,
    VaultObjectCreateRequest,
    VaultObjectSummary,
)


class VaultRepository(ABC):
    """Stable port for reading and writing vault objects and task capsules.

    Implementations may store data in memory, on a local device, or in an
    encrypted server replica, but they all obey the same contract. Callers never
    receive full plaintext for device-local objects; they may receive
    DeviceUnavailableState instead.
    """

    @abstractmethod
    def create_object(self, request: VaultObjectCreateRequest) -> VaultObject:
        """Create a new vault object and its cloud control projection."""

    @abstractmethod
    def get_object(
        self, owner_id: str, object_id: str
    ) -> VaultObject | DeviceUnavailableState:
        """Return the vault object projection or a device-unavailable state."""

    @abstractmethod
    def list_objects(self, owner_id: str) -> list[VaultObjectSummary]:
        """List vault object summaries for the owner."""

    @abstractmethod
    def issue_task_capsule(
        self, request: CapsuleIssueRequest
    ) -> TemporaryTaskCapsule:
        """Issue a temporary task capsule bound to a run and purpose."""

    @abstractmethod
    def get_capsule(self, owner_id: str, capsule_id: str) -> TemporaryTaskCapsule:
        """Return a capsule without mutating its state.

        Raises VaultError if the capsule does not exist or does not belong to
        the given owner.
        """

    @abstractmethod
    def revoke_capsule(self, owner_id: str, capsule_id: str) -> TemporaryTaskCapsule:
        """Revoke a previously issued capsule."""

    @abstractmethod
    def get_cloud_projection(
        self, owner_id: str, object_id: str
    ) -> CloudControlProjection | None:
        """Return the cloud control projection, or None if it does not exist."""

    @abstractmethod
    def get_content(self, owner_id: str, object_id: str) -> bytes:
        """Return plaintext bytes when the repository is the authority.

        Device-local content is served through DeviceVaultPort; this method is
        used for server replicas and project copies.
        """

    def store_device_local_wrapped_key(
        self, owner_id: str, object_id: str, wrapped_key: bytes
    ) -> None:
        """Store the wrapped data key for a device-local encrypted object.

        Adapters that support encrypted device-local storage override this
        method. The default raises NotImplementedError.
        """
        raise NotImplementedError(
            "This repository does not support encrypted device-local storage."
        )

    def get_device_local_wrapped_key(
        self, owner_id: str, object_id: str
    ) -> bytes:
        """Return the wrapped data key for a device-local encrypted object.

        Adapters that support encrypted device-local storage override this
        method. The default raises NotImplementedError.
        """
        raise NotImplementedError(
            "This repository does not support encrypted device-local storage."
        )


class DeviceVaultPort(ABC):
    """Port for accessing the device-side vault runtime.

    The device runtime is the authoritative location for device-local plaintext.
    When it is unavailable, the system waits legally instead of silently
    uploading full text from another source.
    """

    @abstractmethod
    def is_available(self, owner_id: str, device_id: str) -> bool:
        """Return True when the device runtime can respond."""

    @abstractmethod
    def get_content(
        self, owner_id: str, object_id: str
    ) -> bytes | DeviceUnavailableState:
        """Return plaintext bytes from the device, or an unavailable state."""


class CloudControlProjectionStore(ABC):
    """Port for the cloud-side control projection store.

    The store keeps only metadata: hashes, lengths, epochs, authorization
    versions, and status. It never stores full private plaintext.
    """

    @abstractmethod
    def save_projection(
        self, projection: CloudControlProjection
    ) -> CloudControlProjection:
        """Persist a cloud control projection."""

    @abstractmethod
    def get_projection(
        self, owner_id: str, object_id: str
    ) -> CloudControlProjection | None:
        """Return the projection if it exists."""


class DeviceKeychainPort(ABC):
    """Port for storing and retrieving device private keys.

    The private key must live in a system keychain or secure enclave and must
    never be written to ordinary configuration files or logs. Implementations
    may use OS-specific keychains (Windows DPAPI, macOS Keychain, Linux
    Secret Service) or a secure enclave.
    """

    @abstractmethod
    def store_private_key(
        self, account_id: str, device_id: str, private_key_pem: str
    ) -> None:
        """Store a device private key in the system keychain."""

    @abstractmethod
    def get_private_key(self, account_id: str, device_id: str) -> str | None:
        """Retrieve a device private key, or None if not present."""

    @abstractmethod
    def delete_private_key(self, account_id: str, device_id: str) -> None:
        """Delete a device private key from the system keychain."""

    @abstractmethod
    def store_wrapping_key(
        self, account_id: str, device_id: str, wrapping_key: bytes
    ) -> None:
        """Store the symmetric key-wrapping key for a device."""

    @abstractmethod
    def get_wrapping_key(self, account_id: str, device_id: str) -> bytes | None:
        """Retrieve the symmetric key-wrapping key for a device."""

    @abstractmethod
    def delete_wrapping_key(self, account_id: str, device_id: str) -> None:
        """Delete the symmetric key-wrapping key for a device."""


class VaultEncryptionPort(ABC):
    """Port for symmetric encryption of vault content and key wrapping.

    Domain modules depend on this port rather than a specific cryptographic
    library. Implementations choose the algorithm and key length; the contract
    only requires deterministic encrypt/decrypt and wrap/unwrap behavior.
    """

    @abstractmethod
    def generate_data_key(self) -> bytes:
        """Generate a new data encryption key."""

    @abstractmethod
    def generate_wrapping_key(self) -> bytes:
        """Generate a new key-wrapping key."""

    @abstractmethod
    def encrypt(self, plaintext: bytes, key: bytes) -> bytes:
        """Encrypt plaintext bytes and return ciphertext bytes."""

    @abstractmethod
    def decrypt(self, ciphertext: bytes, key: bytes) -> bytes:
        """Decrypt ciphertext bytes and return plaintext bytes."""

    @abstractmethod
    def wrap_key(self, data_key: bytes, wrapping_key: bytes) -> bytes:
        """Wrap a data key with a wrapping key."""

    @abstractmethod
    def unwrap_key(self, wrapped_key: bytes, wrapping_key: bytes) -> bytes:
        """Unwrap a data key with a wrapping key."""


class DevicePairingRepository(ABC):
    """Stable port for device certificates and key epochs."""

    @abstractmethod
    def save_certificate(self, certificate: DeviceCertificate) -> DeviceCertificate:
        """Persist a device certificate."""

    @abstractmethod
    def get_certificate(
        self, account_id: str, device_id: str
    ) -> DeviceCertificate | None:
        """Return the active or revoked certificate for a device."""

    @abstractmethod
    def list_certificates(self, account_id: str) -> list[DeviceCertificate]:
        """List all certificates for an account."""

    @abstractmethod
    def save_key_epoch(self, epoch: KeyEpoch) -> KeyEpoch:
        """Persist a key epoch."""

    @abstractmethod
    def get_active_epoch(
        self, account_id: str, device_id: str
    ) -> KeyEpoch | None:
        """Return the active key epoch for a device."""
