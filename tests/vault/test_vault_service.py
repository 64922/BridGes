"""Module-interface tests for the vault boundary and persistence ports.

The seam under test: private content authority stays in the vault port, the
cloud only receives a minimal control projection, task capsules bind run,
purpose, TTL, authorization version and key epoch, and device unavailability
returns a legal wait state instead of silently uploading full text.
"""

import pytest

from science_companion.contracts.projects import ObjectDomain
from science_companion.contracts.vault import (
    CapsuleStatus,
    CloudProjectionStatus,
    ContentAuthority,
    DeviceUnavailableState,
    VaultObjectDomain,
)
from science_companion.vault import (
    InMemoryVaultRepository,
    MemoryDeviceVaultPort,
    UnavailableDeviceVaultPort,
    VaultService,
)


@pytest.fixture
def repository() -> InMemoryVaultRepository:
    return InMemoryVaultRepository()


@pytest.fixture
def available_service(repository: InMemoryVaultRepository) -> VaultService:
    return VaultService(repository, MemoryDeviceVaultPort(repository))


@pytest.fixture
def unavailable_service(repository: InMemoryVaultRepository) -> VaultService:
    return VaultService(
        repository,
        UnavailableDeviceVaultPort(repository, reason="device_offline"),
    )


@pytest.fixture
def alice_id() -> str:
    return "account-alice"


class TestPrivateObjectAndCloudProjectionBoundary:
    def test_create_private_object_returns_owned_projection(
        self, available_service: VaultService, alice_id: str
    ) -> None:
        content = b"My private research notes"
        obj = available_service.create_private_object(
            owner_account_id=alice_id,
            content=content,
            content_authority=ContentAuthority.DEVICE_LOCAL,
            device_id="device-alice-1",
        )

        assert obj.ref.domain == VaultObjectDomain.PERSONAL_VAULT
        assert obj.ref.owner_id == alice_id
        assert obj.owner_account_id == alice_id
        assert obj.content_authority == ContentAuthority.DEVICE_LOCAL
        assert obj.device_id == "device-alice-1"
        assert obj.status == CloudProjectionStatus.ACTIVE

    def test_cloud_projection_contains_only_hash_and_metadata(
        self, available_service: VaultService, alice_id: str
    ) -> None:
        content = b"Sensitive personal material"
        obj = available_service.create_private_object(
            owner_account_id=alice_id,
            content=content,
            content_authority=ContentAuthority.DEVICE_LOCAL,
            device_id="device-alice-1",
        )

        projection = available_service._repository.get_cloud_projection(
            alice_id, obj.ref.object_id
        )
        assert projection is not None
        assert projection.content_hash
        assert projection.content_length == len(content)
        assert projection.object_ref.object_id == obj.ref.object_id
        # The cloud projection must never carry the full plaintext.
        assert content not in projection.model_dump_json().encode()

    def test_get_object_returns_metadata_not_full_content(
        self, available_service: VaultService, alice_id: str
    ) -> None:
        content = b"Personal content"
        obj = available_service.create_private_object(
            owner_account_id=alice_id,
            content=content,
            content_authority=ContentAuthority.DEVICE_LOCAL,
            device_id="device-alice-1",
        )

        fetched = available_service.get_object(alice_id, obj.ref.object_id)
        assert isinstance(fetched, type(obj))
        assert fetched.ref.object_id == obj.ref.object_id
        # The projection does not include plaintext.
        assert content not in fetched.model_dump_json().encode()


class TestTaskCapsuleBinding:
    def test_capsule_binds_run_purpose_object_ttl_and_epochs(
        self, available_service: VaultService, alice_id: str
    ) -> None:
        content = b"Material for one task"
        obj = available_service.create_private_object(
            owner_account_id=alice_id,
            content=content,
            content_authority=ContentAuthority.DEVICE_LOCAL,
            device_id="device-alice-1",
        )

        capsule = available_service.issue_task_capsule(
            owner_account_id=alice_id,
            object_id=obj.ref.object_id,
            run_id="run-42",
            purpose="summarize_notes",
            ttl_seconds=600,
        )

        assert capsule.status == CapsuleStatus.ISSUED
        assert capsule.run_id == "run-42"
        assert capsule.purpose == "summarize_notes"
        assert len(capsule.object_refs) == 1
        assert capsule.object_refs[0].object_id == obj.ref.object_id
        assert capsule.key_epoch == obj.projection.key_epoch
        assert capsule.authorization_snapshot == obj.projection.authorization_version
        assert (capsule.expires_at - capsule.issued_at).total_seconds() == 600

    def test_revoke_capsule_makes_it_unusable(
        self, available_service: VaultService, alice_id: str
    ) -> None:
        obj = available_service.create_private_object(
            owner_account_id=alice_id,
            content=b"Material",
            content_authority=ContentAuthority.SERVER_REPLICA,
        )
        capsule = available_service.issue_task_capsule(
            owner_account_id=alice_id,
            object_id=obj.ref.object_id,
            run_id="run-1",
            purpose="test",
        )

        revoked = available_service.revoke_capsule(alice_id, capsule.capsule_id)

        assert revoked.status == CapsuleStatus.REVOKED
        assert revoked.capsule_id == capsule.capsule_id


class TestDeviceUnavailableState:
    def test_device_unavailable_returns_wait_state_not_full_text(
        self, unavailable_service: VaultService, alice_id: str
    ) -> None:
        content = b"Only on my device"
        obj = unavailable_service.create_private_object(
            owner_account_id=alice_id,
            content=content,
            content_authority=ContentAuthority.DEVICE_LOCAL,
            device_id="device-alice-1",
        )

        result = unavailable_service.get_content(alice_id, obj.ref.object_id)

        assert isinstance(result, DeviceUnavailableState)
        assert result.object_ref.object_id == obj.ref.object_id
        assert result.reason == "device_offline"
        # The full content must not leak through the unavailable path.
        assert content not in result.model_dump_json().encode()

    def test_server_replica_remains_available_when_device_is_offline(
        self, unavailable_service: VaultService, alice_id: str
    ) -> None:
        content = b"Encrypted server replica"
        obj = unavailable_service.create_private_object(
            owner_account_id=alice_id,
            content=content,
            content_authority=ContentAuthority.SERVER_REPLICA,
        )

        result = unavailable_service.get_content(alice_id, obj.ref.object_id)

        assert result == content


class TestProjectCopyBoundary:
    def test_share_as_project_copy_creates_separate_authority(
        self, available_service: VaultService, alice_id: str
    ) -> None:
        from science_companion.contracts.vault import VaultShareRequest

        content = b"Draft to share"
        source = available_service.create_private_object(
            owner_account_id=alice_id,
            content=content,
            content_authority=ContentAuthority.SERVER_REPLICA,
        )

        project_ref = available_service.share_as_project_copy(
            owner_account_id=alice_id,
            request=VaultShareRequest(
                source_object_id=source.ref.object_id,
                target_project_id="project-shared-1",
                grant_purpose="peer_review",
            ),
        )

        assert project_ref.domain == ObjectDomain.SHARED_PROJECT
        assert project_ref.owner_id == "project-shared-1"
        assert project_ref.object_id != source.ref.object_id


class TestCrossAccountIsolation:
    def test_one_account_cannot_read_anothers_vault_object(
        self, available_service: VaultService, alice_id: str
    ) -> None:
        obj = available_service.create_private_object(
            owner_account_id=alice_id,
            content=b"Alice private",
            content_authority=ContentAuthority.SERVER_REPLICA,
        )

        from science_companion.vault.adapters import VaultError

        with pytest.raises(VaultError, match="对象不存在"):
            available_service.get_object("account-bob", obj.ref.object_id)
