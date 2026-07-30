"""Integration tests for the vault API seam.

The seam under test: an authenticated user creates a private object, the cloud
only receives a minimal control projection, a temporary task capsule binds run,
purpose, TTL and key epoch, and device unavailability returns a legal wait state
instead of silently uploading full text.
"""

import base64
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from science_companion.api.main import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def _register(client: TestClient, email: str, password: str) -> dict[str, Any]:
    response = client.post(
        "/auth/register",
        json={"email": email, "password": password, "agreed_to_terms": True},
    )
    assert response.status_code == 201
    return cast(dict[str, Any], response.json())


def _create_vault_object(
    client: TestClient,
    account_id: str,
    content: bytes,
    content_authority: str,
    device_id: str | None = None,
) -> dict[str, Any]:
    response = client.post(
        "/vault/objects",
        json={
            "owner_account_id": account_id,
            "content": base64.b64encode(content).decode("ascii"),
            "content_authority": content_authority,
            "device_id": device_id,
            "purpose": "test",
        },
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def _login(client: TestClient, email: str, password: str) -> dict[str, Any]:
    response = client.post(
        "/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200
    return cast(dict[str, Any], response.json())


def _pair_device(client: TestClient) -> str:
    response = client.post(
        "/vault/devices/pair",
        json={"device_name": "Test device"},
    )
    assert response.status_code == 201, response.text
    return cast(str, response.json()["certificate"]["device_id"])


class TestVaultObjectCreationAndProjection:
    def test_create_vault_object_requires_authentication(
        self, client: TestClient
    ) -> None:
        response = client.post(
            "/vault/objects",
            json={
                "owner_account_id": "any",
                "content": [1, 2, 3],
                "content_authority": "server_replica",
            },
        )
        assert response.status_code == 401

    def test_create_vault_object_returns_owned_projection(
        self, client: TestClient
    ) -> None:
        registered = _register(client, "vault-create@example.com", "correct-horse-12")
        account_id = registered["account"]["id"]
        device_id = _pair_device(client)

        body = _create_vault_object(
            client,
            account_id,
            b"Private notes",
            "device_local",
            device_id=device_id,
        )

        assert body["owner_account_id"] == account_id
        assert body["content_authority"] == "device_local"
        assert body["device_id"] == device_id
        assert body["ref"]["domain"] == "personal_vault"
        assert body["ref"]["owner_id"] == account_id
        assert body["status"] == "active"

    def test_cloud_projection_excludes_full_content(
        self, client: TestClient
    ) -> None:
        registered = _register(
            client, "vault-projection@example.com", "correct-horse-12"
        )
        account_id = registered["account"]["id"]
        content = b"Sensitive personal material"
        obj = _create_vault_object(client, account_id, content, "server_replica")
        object_id = obj["ref"]["object_id"]

        response = client.get(f"/vault/objects/{object_id}/projection")
        assert response.status_code == 200
        projection = response.json()

        assert projection["content_length"] == len(content)
        assert projection["content_hash"]
        # The projection must never include the full plaintext.
        assert content.decode() not in response.text

    def test_list_objects_includes_only_own_objects(
        self, client: TestClient
    ) -> None:
        alice = _register(client, "alice-vault@example.com", "correct-horse-12")
        alice_id = alice["account"]["id"]
        _create_vault_object(client, alice_id, b"Alice material", "server_replica")

        bob = _register(client, "bob-vault@example.com", "correct-horse-12")
        bob_id = bob["account"]["id"]
        _create_vault_object(client, bob_id, b"Bob material", "server_replica")

        # Re-authenticate as Alice.
        client.cookies.clear()
        _login(client, "alice-vault@example.com", "correct-horse-12")
        response = client.get("/vault/objects")
        assert response.status_code == 200
        items = response.json()
        assert len(items) == 1
        assert items[0]["ref"]["owner_id"] == alice_id


class TestTaskCapsuleAPI:
    def test_issue_capsule_binds_run_purpose_and_ttl(
        self, client: TestClient
    ) -> None:
        registered = _register(
            client, "vault-capsule@example.com", "correct-horse-12"
        )
        account_id = registered["account"]["id"]
        obj = _create_vault_object(
            client, account_id, b"Task material", "server_replica"
        )
        object_id = obj["ref"]["object_id"]

        response = client.post(
            f"/vault/objects/{object_id}/capsules",
            json={
                "owner_account_id": account_id,
                "object_id": object_id,
                "run_id": "run-api-1",
                "purpose": "answer_question",
                "ttl_seconds": 300,
            },
        )
        assert response.status_code == 201
        capsule = response.json()
        assert capsule["run_id"] == "run-api-1"
        assert capsule["purpose"] == "answer_question"
        assert capsule["object_refs"][0]["object_id"] == object_id
        assert capsule["key_epoch"] == obj["projection"]["key_epoch"]
        assert (
            capsule["authorization_snapshot"]
            == obj["projection"]["authorization_version"]
        )
        assert capsule["status"] == "issued"

    def test_revoke_capsule_makes_it_revoked(self, client: TestClient) -> None:
        registered = _register(
            client, "vault-revoke@example.com", "correct-horse-12"
        )
        account_id = registered["account"]["id"]
        obj = _create_vault_object(client, account_id, b"Material", "server_replica")
        object_id = obj["ref"]["object_id"]
        capsule_response = client.post(
            f"/vault/objects/{object_id}/capsules",
            json={
                "owner_account_id": account_id,
                "object_id": object_id,
                "run_id": "run-revoke",
                "purpose": "test",
            },
        )
        capsule_id = capsule_response.json()["capsule_id"]

        response = client.post(f"/vault/capsules/{capsule_id}/revoke")
        assert response.status_code == 200
        assert response.json()["status"] == "revoked"


class TestDeviceUnavailableAPI:
    def test_device_unavailable_content_returns_wait_state(
        self, client: TestClient
    ) -> None:
        registered = _register(
            client, "vault-device@example.com", "correct-horse-12"
        )
        account_id = registered["account"]["id"]

        # Replace the device port with an unavailable one to simulate offline device.
        from fastapi import FastAPI

        from science_companion.vault import UnavailableDeviceVaultPort, VaultService

        app = cast(FastAPI, client.app)
        repository = app.state.vault_service._repository
        app.state.vault_service = VaultService(
            repository=repository,
            device_port=UnavailableDeviceVaultPort(
                repository, reason="device_offline"
            ),
        )

        obj = _create_vault_object(
            client,
            account_id,
            b"Only on device",
            "device_local",
            device_id="device-offline",
        )
        object_id = obj["ref"]["object_id"]

        response = client.get(f"/vault/objects/{object_id}/content")
        assert response.status_code == 202
        body = response.json()
        assert body["reason"] == "device_offline"
        assert body["object_ref"]["object_id"] == object_id


class TestShareAsProjectCopy:
    def test_share_creates_independent_project_copy(
        self, client: TestClient
    ) -> None:
        registered = _register(
            client, "vault-share@example.com", "correct-horse-12"
        )
        account_id = registered["account"]["id"]
        obj = _create_vault_object(client, account_id, b"Draft", "server_replica")
        object_id = obj["ref"]["object_id"]

        response = client.post(
            f"/vault/objects/{object_id}/share",
            json={
                "source_object_id": object_id,
                "target_project_id": "project-shared-1",
                "grant_purpose": "peer_review",
            },
        )
        assert response.status_code == 201
        body = response.json()
        assert body["project_ref"]["domain"] == "shared_project"
        assert body["project_ref"]["owner_id"] == "project-shared-1"
        assert body["project_ref"]["object_id"] != object_id
        assert body["purpose"] == "peer_review"
