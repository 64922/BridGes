"""Module-interface tests for vault device pairing and encrypted local objects.

The seam under test: a user pairs a device with their account, saves personal
content as an encrypted device-local object, and authorizes a minimal task
capsule. The device holds plaintext authority; the cloud only sees control
projections and temporary capsules.
"""

from __future__ import annotations

import pytest

from bridges.contracts.vault import (
    CapsuleStatus,
    ContentAuthority,
    DevicePairingRequest,
    DevicePairingStatus,
    DeviceRevocationRequest,
    DeviceType,
)
from bridges.vault import (
    FernetVaultEncryptionAdapter,
    InMemoryDeviceKeychain,
    InMemoryDevicePairingRepository,
    InMemoryVaultRepository,
    VaultService,
)
from bridges.vault.adapters import VaultError


@pytest.fixture
def repository() -> InMemoryVaultRepository:
    return InMemoryVaultRepository()


@pytest.fixture
def keychain() -> InMemoryDeviceKeychain:
    return InMemoryDeviceKeychain()


@pytest.fixture
def encryption() -> FernetVaultEncryptionAdapter:
    return FernetVaultEncryptionAdapter()


@pytest.fixture
def pairing_repo() -> InMemoryDevicePairingRepository:
    return InMemoryDevicePairingRepository()


@pytest.fixture
def paired_service(
    repository: InMemoryVaultRepository,
    keychain: InMemoryDeviceKeychain,
    encryption: FernetVaultEncryptionAdapter,
    pairing_repo: InMemoryDevicePairingRepository,
) -> VaultService:
    return VaultService(
        repository=repository,
        device_port=None,  # device-local content goes through encrypted repository path
        device_keychain=keychain,
        vault_encryption=encryption,
        device_pairing_repository=pairing_repo,
    )


@pytest.fixture
def alice_id() -> str:
    return "account-alice"


def _pair_device(service: VaultService, account_id: str) -> str:
    response = service.pair_device(
        account_id=account_id,
        request=DevicePairingRequest(
            device_name="Alice's laptop",
            device_type=DeviceType.DESKTOP,
        ),
    )
    assert response.certificate.status == DevicePairingStatus.PAIRED
    assert response.key_epoch.status == DevicePairingStatus.PAIRED
    assert response.runtime.account_id == account_id
    return response.certificate.device_id


class TestDevicePairing:
    def test_pair_device_creates_certificate_bound_to_account(
        self,
        paired_service: VaultService,
        alice_id: str,
    ) -> None:
        device_id = _pair_device(paired_service, alice_id)

        certificates = paired_service.list_device_certificates(alice_id)
        assert len(certificates) == 1
        cert = certificates[0]
        assert cert.account_id == alice_id
        assert cert.device_id == device_id
        assert cert.device_name == "Alice's laptop"
        assert cert.device_type == DeviceType.DESKTOP
        assert cert.public_key_pem
        assert cert.fingerprint
        assert cert.status == DevicePairingStatus.PAIRED
        assert cert.key_epoch

    def test_private_key_stored_in_keychain_not_in_repository(
        self,
        paired_service: VaultService,
        repository: InMemoryVaultRepository,
        keychain: InMemoryDeviceKeychain,
        alice_id: str,
    ) -> None:
        device_id = _pair_device(paired_service, alice_id)

        private_key = keychain.get_private_key(alice_id, device_id)
        assert private_key is not None
        assert "PRIVATE KEY" in private_key
        # The private key must not be reachable through the repository.
        assert private_key not in str(repository._objects)
        assert private_key not in str(repository._projection_store)

    def test_revoke_device_rotates_key_epoch(
        self,
        paired_service: VaultService,
        pairing_repo: InMemoryDevicePairingRepository,
        alice_id: str,
    ) -> None:
        device_id = _pair_device(paired_service, alice_id)
        old_epoch = pairing_repo.get_active_epoch(alice_id, device_id)
        assert old_epoch is not None

        revoked = paired_service.revoke_device(
            account_id=alice_id,
            request=DeviceRevocationRequest(device_id=device_id, reason="lost"),
        )

        assert revoked.status == DevicePairingStatus.REVOKED
        assert revoked.revoked_at is not None
        new_epoch = pairing_repo.get_active_epoch(alice_id, device_id)
        assert new_epoch is None or new_epoch.epoch_id != old_epoch.epoch_id


class TestEncryptedLocalObject:
    def test_create_device_local_object_stores_ciphertext_not_plaintext(
        self,
        paired_service: VaultService,
        repository: InMemoryVaultRepository,
        alice_id: str,
    ) -> None:
        device_id = _pair_device(paired_service, alice_id)
        content = b"My private research notes"

        obj = paired_service.create_private_object(
            owner_account_id=alice_id,
            content=content,
            content_authority=ContentAuthority.DEVICE_LOCAL,
            device_id=device_id,
        )

        assert obj.content_authority == ContentAuthority.DEVICE_LOCAL
        assert obj.device_id == device_id
        # The repository must hold ciphertext, not plaintext.
        raw = repository.get_content(alice_id, obj.ref.object_id)
        assert raw != content
        assert content not in raw
        # Cloud projection only sees hash and length of plaintext.
        projection = repository.get_cloud_projection(alice_id, obj.ref.object_id)
        assert projection is not None
        assert projection.content_length == len(content)
        assert projection.content_hash
        assert content.decode() not in projection.model_dump_json()

    def test_get_content_returns_plaintext_authority_from_device(
        self,
        paired_service: VaultService,
        alice_id: str,
    ) -> None:
        device_id = _pair_device(paired_service, alice_id)
        content = b"Only on my device"

        obj = paired_service.create_private_object(
            owner_account_id=alice_id,
            content=content,
            content_authority=ContentAuthority.DEVICE_LOCAL,
            device_id=device_id,
        )

        result = paired_service.get_content(alice_id, obj.ref.object_id)
        assert result == content

    def test_device_local_object_without_pairing_is_rejected(
        self,
        paired_service: VaultService,
        alice_id: str,
    ) -> None:
        with pytest.raises(VaultError, match="设备未配对"):
            paired_service.create_private_object(
                owner_account_id=alice_id,
                content=b"No paired device",
                content_authority=ContentAuthority.DEVICE_LOCAL,
                device_id="unpaired-device",
            )


class TestTaskCapsuleMinimalContext:
    def test_capsule_contains_only_run_purpose_object_and_ttl(
        self,
        paired_service: VaultService,
        alice_id: str,
    ) -> None:
        device_id = _pair_device(paired_service, alice_id)
        content = b"Material for one task"
        obj = paired_service.create_private_object(
            owner_account_id=alice_id,
            content=content,
            content_authority=ContentAuthority.DEVICE_LOCAL,
            device_id=device_id,
        )

        capsule = paired_service.issue_task_capsule(
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
        assert (capsule.expires_at - capsule.issued_at).total_seconds() == 600
        # The capsule must not carry the full plaintext content.
        dumped = capsule.model_dump_json()
        assert content.decode() not in dumped

    def test_capsule_after_device_revocation_is_revoked(
        self,
        paired_service: VaultService,
        alice_id: str,
    ) -> None:
        device_id = _pair_device(paired_service, alice_id)
        obj = paired_service.create_private_object(
            owner_account_id=alice_id,
            content=b"Material",
            content_authority=ContentAuthority.DEVICE_LOCAL,
            device_id=device_id,
        )
        capsule = paired_service.issue_task_capsule(
            owner_account_id=alice_id,
            object_id=obj.ref.object_id,
            run_id="run-revoke",
            purpose="test",
        )

        paired_service.revoke_device(
            account_id=alice_id,
            request=DeviceRevocationRequest(device_id=device_id, reason="lost"),
        )

        revoked = paired_service.revoke_capsule(alice_id, capsule.capsule_id)
        assert revoked.status == CapsuleStatus.REVOKED


class TestEncryptedLocalObjectIsolation:
    def test_one_account_cannot_decrypt_anothers_device_local_object(
        self,
        paired_service: VaultService,
        repository: InMemoryVaultRepository,
        keychain: InMemoryDeviceKeychain,
        encryption: FernetVaultEncryptionAdapter,
        pairing_repo: InMemoryDevicePairingRepository,
        alice_id: str,
    ) -> None:
        device_id = _pair_device(paired_service, alice_id)
        obj = paired_service.create_private_object(
            owner_account_id=alice_id,
            content=b"Alice private",
            content_authority=ContentAuthority.DEVICE_LOCAL,
            device_id=device_id,
        )

        # Bob has his own keychain and pairing repo; he cannot access Alice's.
        bob_service = VaultService(
            repository=repository,
            device_port=None,
            device_keychain=keychain,
            vault_encryption=encryption,
            device_pairing_repository=pairing_repo,
        )

        with pytest.raises(VaultError, match="对象不存在"):
            bob_service.get_content("account-bob", obj.ref.object_id)
