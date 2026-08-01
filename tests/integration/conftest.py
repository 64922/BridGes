"""Shared fixtures for integration tests.

T007 introduces reusable isolation fixtures so that storage, index and other
future data channels can reuse the same two-user / two-project / two-tenant
scenario without re-implementing registration and setup logic.
"""

from __future__ import annotations

import base64
from typing import Any, Protocol, cast

import pytest
from fastapi.testclient import TestClient

from science_companion.api.main import create_app


class _RegisteredUser(Protocol):
    def __call__(self, client: TestClient, email: str, password: str) -> dict[str, Any]:
        ...


class _CreateProject(Protocol):
    def __call__(
        self, client: TestClient, name: str, description: str | None = None
    ) -> str:
        ...


class _CreateVaultObject(Protocol):
    def __call__(
        self,
        client: TestClient,
        account_id: str,
        content: bytes,
        content_authority: str = ...,
    ) -> dict[str, Any]:
        ...


class _SubmitWorkOrder(Protocol):
    def __call__(
        self, client: TestClient, project_id: str, objective: str | None = None
    ) -> dict[str, Any]:
        ...


class _CreateInstitution(Protocol):
    def __call__(self, client: TestClient, name: str) -> dict[str, Any]:
        ...


class _InviteInstitutionMember(Protocol):
    def __call__(
        self,
        client: TestClient,
        institution_id: str,
        recipient_account_id: str,
        role: str = ...,
    ) -> str:
        ...


class _CreateInstitutionProject(Protocol):
    def __call__(
        self, client: TestClient, institution_id: str, name: str
    ) -> dict[str, Any]:
        ...


@pytest.fixture
def client() -> TestClient:
    """Fresh API client with an isolated app instance.

    Qwen 适配器强制 stub 与 settings 缓存清理由根级 ``tests/conftest.py``
    的确定性环境 fixture 在每个测试前统一处理。
    """
    return TestClient(create_app())


@pytest.fixture
def registered_user() -> _RegisteredUser:
    """Factory fixture that registers and returns a user dict plus a client."""

    def _make(client: TestClient, email: str, password: str) -> dict[str, Any]:
        response = client.post(
            "/auth/register",
            json={"email": email, "password": password, "agreed_to_terms": True},
        )
        assert response.status_code == 201, response.text
        return cast(dict[str, Any], response.json())

    return _make


@pytest.fixture
def create_project_for_user() -> _CreateProject:
    """Factory fixture that creates a project for the current authenticated user."""

    def _make(
        client: TestClient, name: str, description: str | None = None
    ) -> str:
        payload: dict[str, Any] = {"name": name}
        if description is not None:
            payload["description"] = description
        response = client.post("/projects", json=payload)
        assert response.status_code == 201, response.text
        return cast(str, response.json()["id"])

    return _make


@pytest.fixture
def submit_work_order_for_project() -> _SubmitWorkOrder:
    """Factory fixture that submits a WorkOrder for the current user's project."""

    def _make(
        client: TestClient, project_id: str, objective: str | None = None
    ) -> dict[str, Any]:
        response = client.post(
            f"/projects/{project_id}/work-orders",
            json={
                "workflow_name": "generic_science_task",
                "workflow_version": "1",
                "project_id": project_id,
                "objective": objective or "测试目标",
                "success_criteria": "测试成功标准",
                "risk_statement": "测试风险声明",
            },
        )
        assert response.status_code == 201, response.text
        return cast(dict[str, Any], response.json())

    return _make


@pytest.fixture
def create_vault_object_for_user() -> _CreateVaultObject:
    """Factory fixture that creates a vault object for the current user."""

    def _make(
        client: TestClient,
        account_id: str,
        content: bytes,
        content_authority: str = "server_replica",
    ) -> dict[str, Any]:
        response = client.post(
            "/vault/objects",
            json={
                "owner_account_id": account_id,
                "content": base64.b64encode(content).decode("ascii"),
                "content_authority": content_authority,
                "purpose": "test",
            },
        )
        assert response.status_code == 201, response.text
        return cast(dict[str, Any], response.json())

    return _make


@pytest.fixture
def create_institution() -> _CreateInstitution:
    """Factory fixture that creates an institution for the current authenticated user."""

    def _make(client: TestClient, name: str) -> dict[str, Any]:
        response = client.post("/institutions", json={"name": name})
        assert response.status_code == 201, response.text
        return cast(dict[str, Any], response.json())

    return _make


@pytest.fixture
def invite_institution_member() -> _InviteInstitutionMember:
    """Factory fixture that invites an account to an institution and returns the token secret."""

    def _make(
        client: TestClient,
        institution_id: str,
        recipient_account_id: str,
        role: str = "member",
    ) -> str:
        response = client.post(
            f"/institutions/{institution_id}/invites",
            json={
                "recipient_account_id": recipient_account_id,
                "role": role,
            },
        )
        assert response.status_code == 201, response.text
        return cast(str, response.json()["token_secret"])

    return _make


@pytest.fixture
def create_institution_project() -> _CreateInstitutionProject:
    """Factory fixture that creates an institution-owned project after disclosure."""

    def _make(
        client: TestClient, institution_id: str, name: str
    ) -> dict[str, Any]:
        # Fetch the required disclosure first.
        response = client.get(f"/institutions/{institution_id}/project-disclosure")
        assert response.status_code == 200, response.text

        response = client.post(
            f"/institutions/{institution_id}/projects",
            json={"name": name, "disclosure_acknowledged": True},
        )
        assert response.status_code == 201, response.text
        return cast(dict[str, Any], response.json())

    return _make


@pytest.fixture
def two_user_isolation_fixture(
    client: TestClient,
    registered_user: _RegisteredUser,
    create_project_for_user: _CreateProject,
    create_vault_object_for_user: _CreateVaultObject,
    submit_work_order_for_project: _SubmitWorkOrder,
) -> dict[str, Any]:
    """T007 reusable fixture: two authenticated users with projects and objects.

    Returns a dict with keys:
      - alice: {client, account, project_id, object_id, run_id}
      - bob:   {client, account, project_id, object_id, run_id}

    Both users create a project with the same name and a vault object with the
    same content hash, proving that scope isolation is by identity, not by name
    or hash.
    """
    alice_client = TestClient(client.app)
    bob_client = TestClient(client.app)

    alice = registered_user(
        alice_client, "alice-isolation@example.com", "correct-horse-12"
    )
    bob = registered_user(bob_client, "bob-isolation@example.com", "correct-horse-12")

    alice_account = cast(dict[str, Any], alice["account"])
    bob_account = cast(dict[str, Any], bob["account"])

    alice_project = create_project_for_user(alice_client, "同名隔离项目")
    bob_project = create_project_for_user(bob_client, "同名隔离项目")

    alice_object = create_vault_object_for_user(
        alice_client, cast(str, alice_account["id"]), b"same-content-hash-007"
    )
    bob_object = create_vault_object_for_user(
        bob_client, cast(str, bob_account["id"]), b"same-content-hash-007"
    )

    alice_run = submit_work_order_for_project(alice_client, alice_project)
    bob_run = submit_work_order_for_project(bob_client, bob_project)

    return {
        "alice": {
            "client": alice_client,
            "account": alice_account,
            "project_id": alice_project,
            "object_id": cast(str, alice_object["ref"]["object_id"]),
            "run_id": cast(str, alice_run["run_id"]),
        },
        "bob": {
            "client": bob_client,
            "account": bob_account,
            "project_id": bob_project,
            "object_id": cast(str, bob_object["ref"]["object_id"]),
            "run_id": cast(str, bob_run["run_id"]),
        },
    }
