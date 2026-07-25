"""Stable persistence ports for the personal vault boundary.

Domain modules depend only on these ports, never on device frameworks, the local
file system, or cloud vendor objects directly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from science_companion.contracts.vault import (
    CloudControlProjection,
    CapsuleIssueRequest,
    DeviceUnavailableState,
    TemporaryTaskCapsule,
    VaultObject,
    VaultObjectCreateRequest,
    VaultObjectRef,
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
