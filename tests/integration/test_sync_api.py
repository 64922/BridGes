"""API seam tests for control-first synchronization."""

import base64
import json
from datetime import UTC, datetime
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from fastapi.testclient import TestClient

from bridges.contracts.sync import SyncOperation


def _register(client: TestClient) -> dict[str, Any]:
    response = client.post(
        "/auth/register",
        json={
            "email": "sync-api@example.com",
            "password": "correct-horse-12",
            "agreed_to_terms": True,
        },
    )
    assert response.status_code == 201
    return response.json()


def _pair(client: TestClient) -> dict[str, Any]:
    response = client.post("/vault/devices/pair", json={"device_name": "sync laptop"})
    assert response.status_code == 201
    return response.json()


def _operation(account_id: str, device_id: str, epoch: str, operation_id: str) -> dict[str, Any]:
    return {
        "operation_id": operation_id,
        "account_id": account_id,
        "device_id": device_id,
        "object_ref": {
            "domain": "personal_vault",
            "owner_id": account_id,
            "object_id": "sync-object",
            "version": 0,
        },
        "operation_type": "upsert",
        "base_version": 0,
        "payload": {"title": "offline edit"},
        "authorization_version": "authz-1",
        "key_epoch": epoch,
        "created_at": datetime.now(UTC).isoformat(),
    }


def _sign_operation(client: TestClient, operation: dict[str, Any]) -> dict[str, Any]:
    """模拟配对设备使用系统密钥链中的私钥签名。"""
    parsed = SyncOperation.model_validate(operation)
    unsigned = parsed.model_dump(
        mode="json",
        exclude={"signature", "status", "quarantine_reason"},
    )
    canonical = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    private_pem = client.app.state.device_keychain.get_private_key(
        operation["account_id"], operation["device_id"]
    )
    assert private_pem is not None
    private_key = serialization.load_pem_private_key(private_pem.encode("ascii"), password=None)
    signed = dict(operation)
    signed["signature"] = base64.b64encode(
        private_key.sign(canonical, padding.PKCS1v15(), hashes.SHA256())
    ).decode("ascii")
    return signed


def test_sync_api_accepts_an_operation_after_control_pull(client: TestClient) -> None:
    registered = _register(client)
    paired = _pair(client)
    certificate = paired["certificate"]

    control = client.get(f"/sync/control?device_id={certificate['device_id']}")
    assert control.status_code == 200
    assert control.json()["key_epoch"] == certificate["key_epoch"]

    response = client.post(
        "/sync/exchange",
        json={
            "device_id": certificate["device_id"],
            "key_epoch": certificate["key_epoch"],
            "authorization_version": "authz-1",
            "operations": [
                _sign_operation(
                    client,
                    _operation(
                        registered["account"]["id"],
                        certificate["device_id"],
                        certificate["key_epoch"],
                        "sync-op-1",
                    ),
                )
            ],
        },
    )

    assert response.status_code == 200
    assert [item["operation_id"] for item in response.json()["accepted_operations"]] == [
        "sync-op-1"
    ]


def test_revoked_device_cannot_submit_its_old_outbox(client: TestClient) -> None:
    registered = _register(client)
    paired = _pair(client)
    certificate = paired["certificate"]
    device_id = certificate["device_id"]

    revoked = client.post(
        f"/vault/devices/{device_id}/revoke",
        json={"device_id": device_id, "reason": "lost"},
    )
    assert revoked.status_code == 200

    response = client.post(
        "/sync/exchange",
        json={
            "device_id": device_id,
            "key_epoch": certificate["key_epoch"],
            "authorization_version": "authz-1",
            "operations": [
                _operation(
                    registered["account"]["id"],
                    device_id,
                    certificate["key_epoch"],
                    "stale-op",
                )
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["control"]["device_status"] == "revoked"
    assert response.json()["accepted_operations"] == []
    assert response.json()["quarantined_operations"][0]["status"] == "quarantined"
