"""Integration tests for the sharing API seam.

The seam under test: an authenticated user previews and executes a share,
collaborators see only the minimized project copy, role baseline and object
grants jointly control access, and invite tokens are short-lived, single-use
and bound to project and identity.
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


def _register(
    client: TestClient, username: str, qq_email: str, password: str
) -> dict[str, Any]:
    response = client.post(
        "/auth/register",
        json={"username": username, "qq_email": qq_email, "password": password},
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def _login(client: TestClient, identifier: str, password: str) -> dict[str, Any]:
    response = client.post(
        "/auth/login",
        json={"identifier": identifier, "password": password},
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def _create_vault_object(
    client: TestClient, account_id: str, content: bytes
) -> dict[str, Any]:
    response = client.post(
        "/vault/objects",
        json={
            "owner_account_id": account_id,
            "content": base64.b64encode(content).decode("ascii"),
            "content_authority": "server_replica",
            "purpose": "research_draft",
        },
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def _create_shared_project(client: TestClient, name: str) -> str:
    response = client.post("/sharing/projects", json={"name": name})
    assert response.status_code == 201, response.text
    return cast(str, response.json()["id"])


class TestSharedProjectLifecycle:
    def test_create_and_list_shared_project(self, client: TestClient) -> None:
        _register(client, "shared-owner", "200001@qq.com", "correct-horse-12")
        project_id = _create_shared_project(client, "共享科研项目")

        response = client.get("/sharing/projects")
        assert response.status_code == 200
        projects = response.json()
        assert any(p["id"] == project_id for p in projects)
        assert all(p["object_domain"] == "shared_project" for p in projects)

    def test_non_member_cannot_access_shared_project(
        self, client: TestClient
    ) -> None:
        alice_client = TestClient(client.app)
        _register(alice_client, "alice-shared", "200002@qq.com", "correct-horse-12")
        project_id = _create_shared_project(alice_client, "Alice 共享项目")

        bob_client = TestClient(client.app)
        _register(bob_client, "bob-shared", "200003@qq.com", "correct-horse-12")

        response = bob_client.get(f"/sharing/projects/{project_id}")
        assert response.status_code == 404


class TestSharePreviewAndExecute:
    def test_preview_shows_independence_and_excluded_fields(
        self, client: TestClient
    ) -> None:
        registered = _register(client, "share-preview", "200004@qq.com", "correct-horse-12")
        account_id = registered["account"]["id"]
        project_id = _create_shared_project(client, "预览测试项目")
        obj = _create_vault_object(client, account_id, b"Draft notes")
        object_id = obj["ref"]["object_id"]

        response = client.post(
            "/sharing/preview",
            json={
                "source_object_id": object_id,
                "target_project_id": project_id,
                "grant_purpose": "peer_review",
                "recipient_account_id": "account-bob",
                "recipient_role": "viewer",
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["source_object_id"] == object_id
        assert body["source_owner_id"] == account_id
        assert body["target_project_id"] == project_id
        assert body["independence_note"]
        included = {f["field_name"] for f in body["included_fields"]}
        excluded = {f["field_name"] for f in body["excluded_fields"]}
        assert "content_length" in included
        assert "device_id" in excluded

    def test_execute_share_creates_copy_and_grant(self, client: TestClient) -> None:
        alice_client = TestClient(client.app)
        alice = _register(alice_client, "alice-share", "200005@qq.com", "correct-horse-12")
        alice_id = alice["account"]["id"]

        bob_client = TestClient(client.app)
        bob = _register(bob_client, "bob-share", "200006@qq.com", "correct-horse-12")
        bob_id = bob["account"]["id"]

        project_id = _create_shared_project(alice_client, "协作写作项目")
        obj = _create_vault_object(alice_client, alice_id, b"Research draft")
        object_id = obj["ref"]["object_id"]

        response = alice_client.post(
            "/sharing/execute",
            json={
                "source_object_id": object_id,
                "target_project_id": project_id,
                "grant_purpose": "peer_review",
                "recipient_account_id": bob_id,
                "recipient_role": "viewer",
                "permissions": ["view"],
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["target_project_id"] == project_id
        assert body["source_object_id"] == object_id
        assert body["project_object_domain"] == "shared_project"
        assert body["project_object_id"] != object_id
        assert body["grant_id"]


class TestSharedObjectAccess:
    def test_collaborator_with_grant_can_read_copy(
        self, client: TestClient
    ) -> None:
        alice_client = TestClient(client.app)
        alice = _register(alice_client, "alice-copy", "200007@qq.com", "correct-horse-12")
        alice_id = alice["account"]["id"]

        bob_client = TestClient(client.app)
        bob = _register(bob_client, "bob-copy", "200008@qq.com", "correct-horse-12")
        bob_id = bob["account"]["id"]

        project_id = _create_shared_project(alice_client, "副本访问项目")
        content = b"Shared research draft"
        obj = _create_vault_object(alice_client, alice_id, content)
        object_id = obj["ref"]["object_id"]

        execute_response = alice_client.post(
            "/sharing/execute",
            json={
                "source_object_id": object_id,
                "target_project_id": project_id,
                "grant_purpose": "peer_review",
                "recipient_account_id": bob_id,
                "recipient_role": "viewer",
                "permissions": ["view"],
            },
        )
        copy_object_id = execute_response.json()["project_object_id"]

        # Bob reads the shared object metadata and content.
        meta_response = bob_client.get(
            f"/sharing/projects/{project_id}/objects/{copy_object_id}"
        )
        assert meta_response.status_code == 200
        assert meta_response.json()["ref"]["domain"] == "shared_project"
        assert meta_response.json()["ref"]["owner_id"] == project_id

        content_response = bob_client.get(
            f"/sharing/projects/{project_id}/objects/{copy_object_id}/content"
        )
        assert content_response.status_code == 200
        assert content_response.content == content

    def test_collaborator_without_grant_cannot_read_copy(
        self, client: TestClient
    ) -> None:
        alice_client = TestClient(client.app)
        alice = _register(alice_client, "alice-revoke", "200009@qq.com", "correct-horse-12")
        alice_id = alice["account"]["id"]

        bob_client = TestClient(client.app)
        bob = _register(bob_client, "bob-revoke", "200010@qq.com", "correct-horse-12")
        bob_id = bob["account"]["id"]

        project_id = _create_shared_project(alice_client, "撤销授权项目")
        obj = _create_vault_object(alice_client, alice_id, b"Sensitive")
        object_id = obj["ref"]["object_id"]

        execute_response = alice_client.post(
            "/sharing/execute",
            json={
                "source_object_id": object_id,
                "target_project_id": project_id,
                "grant_purpose": "peer_review",
                "recipient_account_id": bob_id,
                "permissions": ["view"],
            },
        )
        grant_id = execute_response.json()["grant_id"]
        copy_object_id = execute_response.json()["project_object_id"]

        # Alice revokes the grant.
        revoke_response = alice_client.post(f"/sharing/grants/{grant_id}/revoke")
        assert revoke_response.status_code == 200
        assert revoke_response.json()["status"] == "revoked"

        # Bob can no longer read the content.
        content_response = bob_client.get(
            f"/sharing/projects/{project_id}/objects/{copy_object_id}/content"
        )
        assert content_response.status_code == 404

    def test_source_object_is_not_mounted_in_shared_project(
        self, client: TestClient
    ) -> None:
        alice_client = TestClient(client.app)
        alice = _register(alice_client, "alice-source", "200011@qq.com", "correct-horse-12")
        alice_id = alice["account"]["id"]

        bob_client = TestClient(client.app)
        bob = _register(bob_client, "bob-source", "200012@qq.com", "correct-horse-12")
        bob_id = bob["account"]["id"]

        project_id = _create_shared_project(alice_client, "不挂载源对象项目")
        obj = _create_vault_object(alice_client, alice_id, b"Personal notes")
        object_id = obj["ref"]["object_id"]

        execute_response = alice_client.post(
            "/sharing/execute",
            json={
                "source_object_id": object_id,
                "target_project_id": project_id,
                "grant_purpose": "peer_review",
                "recipient_account_id": bob_id,
                "permissions": ["view"],
            },
        )
        copy_object_id = execute_response.json()["project_object_id"]

        # The source object id remains in Alice's personal vault and is not the
        # same as the project copy object id.
        assert copy_object_id != object_id
        source_in_vault = alice_client.get(f"/vault/objects/{object_id}")
        assert source_in_vault.status_code == 200
        assert source_in_vault.json()["ref"]["domain"] == "personal_vault"


class TestInviteTokens:
    def test_invite_token_bound_to_project_and_identity(
        self, client: TestClient
    ) -> None:
        alice_client = TestClient(client.app)
        _register(alice_client, "alice-invite", "200013@qq.com", "correct-horse-12")
        project_id = _create_shared_project(alice_client, "邀请测试项目")

        bob_client = TestClient(client.app)
        bob = _register(bob_client, "bob-invite", "200014@qq.com", "correct-horse-12")
        bob_id = bob["account"]["id"]

        response = alice_client.post(
            f"/sharing/projects/{project_id}/invites",
            json={
                "project_id": project_id,
                "recipient_account_id": bob_id,
                "role": "editor",
                "ttl_seconds": 300,
            },
        )
        assert response.status_code == 201, response.text
        token = response.json()
        assert token["project_id"] == project_id
        assert token["expected_recipient_id"] == bob_id
        assert token["single_use"] is True
        assert token["status"] == "pending"

        # Bob accepts the invite.
        accept_response = bob_client.post(
            "/sharing/invites/accept",
            json={"token_secret": token["token_secret"]},
        )
        assert accept_response.status_code == 200
        assert accept_response.json()["account_id"] == bob_id
        assert accept_response.json()["role"] == "editor"

        # The same token cannot be accepted again.
        second_accept = bob_client.post(
            "/sharing/invites/accept",
            json={"token_secret": token["token_secret"]},
        )
        assert second_accept.status_code == 404

    def test_invite_wrong_recipient_is_rejected(self, client: TestClient) -> None:
        alice_client = TestClient(client.app)
        _register(alice_client, "alice-invite-wrong", "200015@qq.com", "correct-horse-12")
        project_id = _create_shared_project(alice_client, "绑定身份测试项目")

        bob_client = TestClient(client.app)
        bob = _register(bob_client, "bob-invite-wrong", "200016@qq.com", "correct-horse-12")
        bob_id = bob["account"]["id"]

        eve_client = TestClient(client.app)
        _register(eve_client, "eve-invite-wrong", "200017@qq.com", "correct-horse-12")

        token_response = alice_client.post(
            f"/sharing/projects/{project_id}/invites",
            json={
                "project_id": project_id,
                "recipient_account_id": bob_id,
                "role": "viewer",
            },
        )
        token = token_response.json()

        wrong_accept = eve_client.post(
            "/sharing/invites/accept",
            json={"token_secret": token["token_secret"]},
        )
        assert wrong_accept.status_code == 404


class TestRoleBaselineEnforcement:
    def test_viewer_cannot_receive_edit_grant(self, client: TestClient) -> None:
        alice_client = TestClient(client.app)
        alice = _register(alice_client, "alice-role", "200018@qq.com", "correct-horse-12")
        alice_id = alice["account"]["id"]

        bob_client = TestClient(client.app)
        bob = _register(bob_client, "bob-role", "200019@qq.com", "correct-horse-12")
        bob_id = bob["account"]["id"]

        project_id = _create_shared_project(alice_client, "角色基线项目")
        obj = _create_vault_object(alice_client, alice_id, b"Draft")
        object_id = obj["ref"]["object_id"]

        response = alice_client.post(
            "/sharing/execute",
            json={
                "source_object_id": object_id,
                "target_project_id": project_id,
                "grant_purpose": "peer_review",
                "recipient_account_id": bob_id,
                "recipient_role": "viewer",
                "permissions": ["edit"],
            },
        )
        assert response.status_code == 404
