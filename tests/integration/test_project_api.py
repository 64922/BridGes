"""Integration tests for the project API seam.

The seam under test: an authenticated user can create, list, select, rename,
and archive scientific project spaces through the API; deep links re-authenticate
and restore the same owned project projection; cross-account access is rejected.
"""

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


def test_create_project_returns_owned_projection(client: TestClient) -> None:
    _register(client, "project-create@example.com", "correct-horse-12")

    response = client.post(
        "/projects",
        json={"name": "贝尔不等式科普", "description": "面向大学生的量子纠缠解释"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "贝尔不等式科普"
    assert body["object_domain"] == "personal_vault"
    assert body["role"] == "owner"
    assert body["status"] == "active"
    assert body["version"] == 1
    assert body["account_id"]


def test_list_projects_requires_authentication(client: TestClient) -> None:
    response = client.get("/projects")
    assert response.status_code == 401


def test_project_lifecycle_through_api(client: TestClient) -> None:
    _register(client, "project-lifecycle@example.com", "correct-horse-12")

    create_response = client.post("/projects", json={"name": "生命周期项目"})
    assert create_response.status_code == 201
    project_id = create_response.json()["id"]

    list_response = client.get("/projects")
    assert list_response.status_code == 200
    active = list_response.json()["active"]
    assert any(p["ref"]["object_id"] == project_id for p in active)

    get_response = client.get(f"/projects/{project_id}")
    assert get_response.status_code == 200
    assert get_response.json()["id"] == project_id

    rename_response = client.patch(
        f"/projects/{project_id}",
        json={"name": "已重命名项目"},
    )
    assert rename_response.status_code == 200
    assert rename_response.json()["name"] == "已重命名项目"
    assert rename_response.json()["version"] == 2

    archive_response = client.post(f"/projects/{project_id}/archive")
    assert archive_response.status_code == 200
    assert archive_response.json()["status"] == "archived"

    list_after_archive = client.get("/projects")
    assert list_after_archive.status_code == 200
    body = list_after_archive.json()
    assert not any(p["ref"]["object_id"] == project_id for p in body["active"])
    assert any(p["ref"]["object_id"] == project_id for p in body["archived"])


def test_cross_account_project_access_is_rejected(client: TestClient) -> None:
    # T007: account switching revokes prior sessions and clears site data, so
    # cross-account tests must use separate browser sessions (TestClient instances)
    # to keep both subjects authenticated at the same time.
    alice_client = TestClient(client.app)
    _register(alice_client, "alice-project@example.com", "correct-horse-12")

    bob_client = TestClient(client.app)
    _register(bob_client, "bob-project@example.com", "correct-horse-12")
    create_response = bob_client.post("/projects", json={"name": "Bob 私有项目"})
    bob_project_id = create_response.json()["id"]

    # Alice's session cannot access Bob's project deep link.
    response = alice_client.get(f"/projects/{bob_project_id}")
    assert response.status_code == 404


def test_unauthenticated_deep_link_is_rejected(client: TestClient) -> None:
    response = client.get("/projects/some-project-id")
    assert response.status_code == 401


def test_update_unknown_project_returns_not_found(client: TestClient) -> None:
    _register(client, "project-update-404@example.com", "correct-horse-12")
    response = client.patch("/projects/missing-id", json={"name": "新名称"})
    assert response.status_code == 404
