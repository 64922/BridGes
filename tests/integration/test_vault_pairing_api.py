"""Integration tests for vault device pairing and encrypted local objects.

The seam under test: an authenticated user pairs a device, creates an encrypted
local object, and issues a minimal task capsule through the API. The cloud
projection and capsule must never contain the full plaintext.
"""

from __future__ import annotations

import base64
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from bridges.api.main import create_app


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


def _login(client: TestClient, email: str, password: str) -> dict[str, Any]:
    response = client.post(
        "/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200
    return cast(dict[str, Any], response.json())


class TestDevicePairingAPI:
    def test_pair_device_requires_authentication(self, client: TestClient) -> None:
        response = client.post(
            "/vault/devices/pair",
            json={"device_name": "test"},
        )
        assert response.status_code == 401

    def test_pair_device_creates_certificate_and_runtime(
        self, client: TestClient
    ) -> None:
        registered = _register(client, "pair@example.com", "correct-horse-12")
        account_id = registered["account"]["id"]

        response = client.post(
            "/vault/devices/pair",
            json={"device_name": "My laptop", "device_type": "desktop"},
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["certificate"]["account_id"] == account_id
        assert body["certificate"]["device_name"] == "My laptop"
        assert body["certificate"]["device_type"] == "desktop"
        assert body["certificate"]["status"] == "paired"
        assert body["key_epoch"]["status"] == "paired"
        assert body["runtime"]["account_id"] == account_id
        assert "encrypted_storage" in body["runtime"]["capabilities"]

    def test_list_devices_only_shows_own_devices(
        self, client: TestClient
    ) -> None:
        _register(client, "alice-pair@example.com", "correct-horse-12")
        client.post(
            "/vault/devices/pair",
            json={"device_name": "Alice laptop"},
        )

        client.cookies.clear()
        _login(client, "alice-pair@example.com", "correct-horse-12")
        response = client.get("/vault/devices")
        assert response.status_code == 200
        devices = response.json()
        assert len(devices) == 1
        assert devices[0]["device_name"] == "Alice laptop"

        # Bob sees none of Alice's devices.
        client.cookies.clear()
        _register(client, "bob-pair@example.com", "correct-horse-12")
        _login(client, "bob-pair@example.com", "correct-horse-12")
        response = client.get("/vault/devices")
        assert response.status_code == 200
        assert response.json() == []

    def test_revoke_device_marks_it_revoked(self, client: TestClient) -> None:
        _register(client, "revoke@example.com", "correct-horse-12")
        pair_response = client.post(
            "/vault/devices/pair",
            json={"device_name": "To revoke"},
        )
        device_id = pair_response.json()["certificate"]["device_id"]

        response = client.post(
            f"/vault/devices/{device_id}/revoke",
            json={"device_id": device_id, "reason": "lost"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "revoked"
        assert response.json()["revoked_at"] is not None


class TestEncryptedLocalObjectAPI:
    def test_create_device_local_object_is_encrypted(
        self, client: TestClient
    ) -> None:
        registered = _register(client, "local@example.com", "correct-horse-12")
        account_id = registered["account"]["id"]
        pair_response = client.post(
            "/vault/devices/pair",
            json={"device_name": "Local device"},
        )
        device_id = pair_response.json()["certificate"]["device_id"]
        content = b"Sensitive local notes"

        response = client.post(
            "/vault/objects",
            json={
                "owner_account_id": account_id,
                "content": base64.b64encode(content).decode("ascii"),
                "content_authority": "device_local",
                "device_id": device_id,
                "purpose": "test",
            },
        )
        assert response.status_code == 201, response.text
        obj = response.json()
        assert obj["content_authority"] == "device_local"
        assert obj["device_id"] == device_id

        projection_response = client.get(
            f"/vault/objects/{obj['ref']['object_id']}/projection"
        )
        assert projection_response.status_code == 200
        projection = projection_response.json()
        assert projection["content_length"] == len(content)
        assert projection["content_hash"]
        assert content.decode() not in projection_response.text

        content_response = client.get(
            f"/vault/objects/{obj['ref']['object_id']}/content"
        )
        assert content_response.status_code == 200
        assert content_response.content == content

    def test_device_local_without_pairing_is_rejected(
        self, client: TestClient
    ) -> None:
        registered = _register(client, "unpaired@example.com", "correct-horse-12")
        account_id = registered["account"]["id"]

        response = client.post(
            "/vault/objects",
            json={
                "owner_account_id": account_id,
                "content": base64.b64encode(b"data").decode("ascii"),
                "content_authority": "device_local",
                "device_id": "unpaired-device",
                "purpose": "test",
            },
        )
        assert response.status_code == 422

    def test_task_capsule_excludes_full_content(
        self, client: TestClient
    ) -> None:
        registered = _register(client, "capsule-local@example.com", "correct-horse-12")
        account_id = registered["account"]["id"]
        pair_response = client.post(
            "/vault/devices/pair",
            json={"device_name": "Capsule device"},
        )
        device_id = pair_response.json()["certificate"]["device_id"]
        content = b"Capsule source material"
        obj_response = client.post(
            "/vault/objects",
            json={
                "owner_account_id": account_id,
                "content": base64.b64encode(content).decode("ascii"),
                "content_authority": "device_local",
                "device_id": device_id,
                "purpose": "test",
            },
        )
        obj = obj_response.json()
        object_id = obj["ref"]["object_id"]

        response = client.post(
            f"/vault/objects/{object_id}/capsules",
            json={
                "owner_account_id": account_id,
                "object_id": object_id,
                "run_id": "run-api-local",
                "purpose": "answer_question",
                "ttl_seconds": 300,
            },
        )
        assert response.status_code == 201, response.text
        capsule = response.json()
        assert capsule["run_id"] == "run-api-local"
        assert capsule["purpose"] == "answer_question"
        assert capsule["object_refs"][0]["object_id"] == object_id
        assert capsule["key_epoch"] == obj["projection"]["key_epoch"]
        assert content.decode() not in response.text
