"""Issue 25: profile-center API seam tests.

The seam under test: the desktop profile center creates records manually,
withdraws, freezes, unfreezes and deletes them, and the avatar endpoint
removes the uploaded image — all strictly account-scoped.
"""

from __future__ import annotations

import base64
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from bridges.api.main import create_app

_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def _register(
    client: TestClient, username: str = "Alice", qq_email: str = "111111@qq.com"
) -> dict[str, Any]:
    response = client.post(
        "/auth/register",
        json={"username": username, "qq_email": qq_email, "password": "correct-horse-25"},
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json()["account"])


def _manual_assertion_payload(
    dimension: str = "stage_goal", value: str = "三个月内完成科学项目框架"
) -> dict[str, Any]:
    return {
        "dimension": dimension,
        "value_or_rule": value,
        "applicable_scenes": ["quick_check"],
        "sensitivity_class": "preference",
        "authorization_scope": "general",
        "source_note": "用户手动记录",
    }


class TestManualAssertionAPI:
    def test_manual_create_requires_authentication(self, client: TestClient) -> None:
        response = client.post(
            "/profiles/assertions/manual", json=_manual_assertion_payload()
        )
        assert response.status_code == 401

    def test_manual_create_promotes_record_with_provenance(
        self, client: TestClient
    ) -> None:
        _register(client)
        response = client.post(
            "/profiles/assertions/manual", json=_manual_assertion_payload()
        )
        assert response.status_code == 201, response.text
        assertion = response.json()
        assert assertion["status"] == "active"
        assert assertion["canonical_dimension"] == "stage_goal"
        assert assertion["version"] == 1
        assert assertion["last_used_at"] is None

        observations = client.get("/profiles/observations")
        assert observations.status_code == 200
        assert len(observations.json()) == 1

    def test_manual_create_rejects_unregistered_dimension(
        self, client: TestClient
    ) -> None:
        _register(client)
        payload = _manual_assertion_payload(dimension="not_a_dimension")
        response = client.post("/profiles/assertions/manual", json=payload)
        assert response.status_code == 422


class TestProfileCenterLifecycleAPI:
    def test_withdraw_unfreeze_delete_round_trip(self, client: TestClient) -> None:
        _register(client)
        created = client.post(
            "/profiles/assertions/manual", json=_manual_assertion_payload()
        ).json()
        assertion_id = created["assertion_id"]

        withdrawn = client.post(
            f"/profiles/assertions/{assertion_id}/withdraw",
            json={"reason": "不再适用"},
        )
        assert withdrawn.status_code == 200
        assert withdrawn.json()["status"] == "withdrawn"

        restored = client.post(
            f"/profiles/assertions/{assertion_id}/unfreeze",
            json={"reason": "恢复使用"},
        )
        assert restored.status_code == 200
        assert restored.json()["status"] == "active"

        frozen = client.post(
            f"/profiles/assertions/{assertion_id}/freeze",
            json={"reason": "暂时冻结"},
        )
        assert frozen.status_code == 200
        assert frozen.json()["status"] == "frozen"

        unfrozen = client.post(
            f"/profiles/assertions/{assertion_id}/unfreeze",
            json={"reason": "恢复使用"},
        )
        assert unfrozen.status_code == 200
        assert unfrozen.json()["status"] == "active"

        deleted = client.post(
            f"/profiles/assertions/{assertion_id}/delete",
            json={"reason": "清理记录"},
        )
        assert deleted.status_code == 200
        assert deleted.json()["status"] == "deleted"

    def test_lifecycle_operations_are_account_scoped(self, client: TestClient) -> None:
        alice = _register(client)
        created = client.post(
            "/profiles/assertions/manual", json=_manual_assertion_payload()
        ).json()
        assertion_id = created["assertion_id"]

        # Registering Bob switches the session cookie; Bob must not be able to
        # see or mutate Alice's record.
        bob = _register(
            client, username="Bob", qq_email="222222@qq.com"
        )
        assert bob["id"] != alice["id"]
        assert client.get("/profiles/assertions").json() == []
        unknown = client.post(
            f"/profiles/assertions/{assertion_id}/withdraw",
            json={"reason": "越权尝试"},
        )
        assert unknown.status_code in {404, 422}


class TestAvatarRemoveAPI:
    def test_remove_avatar_falls_back_to_static_choice(self, client: TestClient) -> None:
        _register(client)
        upload = client.put(
            "/auth/profile/avatar",
            content=_PNG_1X1,
            headers={"content-type": "image/png"},
        )
        assert upload.status_code == 200
        assert upload.json()["avatar_choice"] == "uploaded"

        removed = client.delete("/auth/profile/avatar")
        assert removed.status_code == 200
        assert removed.json()["avatar_choice"] == "initials"
        assert removed.json()["has_uploaded_avatar"] is False

        avatar = client.get("/auth/profile/avatar")
        assert avatar.status_code == 404

    def test_remove_avatar_without_upload_is_404(self, client: TestClient) -> None:
        _register(client)
        removed = client.delete("/auth/profile/avatar")
        assert removed.status_code == 404

    def test_remove_avatar_requires_authentication(self, client: TestClient) -> None:
        removed = client.delete("/auth/profile/avatar")
        assert removed.status_code == 401


class TestAssertionHistoryAPI:
    def test_history_lists_versions_with_actors(self, client: TestClient) -> None:
        _register(client)
        created = client.post(
            "/profiles/assertions/manual", json=_manual_assertion_payload()
        ).json()
        assertion_id = created["assertion_id"]
        client.post(
            f"/profiles/assertions/{assertion_id}/modify",
            json={
                "value_or_rule": "两个月内完成科学项目框架",
                "applicable_scenes": ["quick_check"],
                "reason": "调整目标周期",
            },
        )

        history = client.get(f"/profiles/assertions/{assertion_id}/history")
        assert history.status_code == 200
        versions = history.json()["versions"]
        assert len(versions) == 1
        assert versions[0]["version"] == 1
        assert versions[0]["value_or_rule"] == "三个月内完成科学项目框架"
        assert versions[0]["change_reason"] == "调整目标周期"
        assert history.json()["current_version"] == 2

    def test_history_is_account_scoped(self, client: TestClient) -> None:
        _register(client)
        created = client.post(
            "/profiles/assertions/manual", json=_manual_assertion_payload()
        ).json()
        assertion_id = created["assertion_id"]
        _register(client, username="Bob", qq_email="222222@qq.com")

        history = client.get(f"/profiles/assertions/{assertion_id}/history")
        assert history.status_code in {404, 422}
