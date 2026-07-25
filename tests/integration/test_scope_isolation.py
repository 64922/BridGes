"""T007 integration tests for multi-user basic scope isolation.

The seam under test: two accounts, two projects and two tenants can run the
basic project-task lifecycle concurrently; every cross-scope access path
(API direct request, deep link, cache key, task reference, vault object
reference) is rejected; background tasks fail closed when scope fields are
missing; account switching cleans up prior state.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from science_companion.api.main import create_app
from science_companion.contracts.identity import AuthMethod, SubjectContext
from science_companion.scope import ScopeEnforcer


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def _register(client: TestClient, email: str, password: str) -> dict[str, Any]:
    response = client.post(
        "/auth/register",
        json={"email": email, "password": password, "agreed_to_terms": True},
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def _login(client: TestClient, email: str, password: str) -> dict[str, Any]:
    response = client.post(
        "/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


class TestTwoUserScenarioIsRepeatable:
    def test_two_user_fixture_is_independent(
        self, two_user_isolation_fixture: dict[str, Any]
    ) -> None:
        alice = two_user_isolation_fixture["alice"]
        bob = two_user_isolation_fixture["bob"]

        assert alice["account"]["id"] != bob["account"]["id"]
        assert alice["project_id"] != bob["project_id"]
        assert alice["object_id"] != bob["object_id"]
        assert alice["run_id"] != bob["run_id"]

        # Both users see exactly one project and one vault object.
        alice_projects = alice["client"].get("/projects").json()["active"]
        bob_projects = bob["client"].get("/projects").json()["active"]
        assert len(alice_projects) == 1
        assert len(bob_projects) == 1

        alice_objects = alice["client"].get("/vault/objects").json()
        bob_objects = bob["client"].get("/vault/objects").json()
        assert len(alice_objects) == 1
        assert len(bob_objects) == 1


class TestCrossAccountAccessIsRejected:
    def test_alice_cannot_read_bob_project_deep_link(
        self, two_user_isolation_fixture: dict[str, Any]
    ) -> None:
        alice = two_user_isolation_fixture["alice"]
        bob = two_user_isolation_fixture["bob"]

        response = alice["client"].get(f"/projects/{bob['project_id']}")
        assert response.status_code == 404

    def test_alice_cannot_list_bob_projects(
        self, two_user_isolation_fixture: dict[str, Any]
    ) -> None:
        alice = two_user_isolation_fixture["alice"]
        bob_project_id = two_user_isolation_fixture["bob"]["project_id"]

        response = alice["client"].get("/projects")
        assert response.status_code == 200
        active = response.json()["active"]
        assert not any(p["ref"]["object_id"] == bob_project_id for p in active)

    def test_alice_cannot_read_bob_run_deep_link(
        self, two_user_isolation_fixture: dict[str, Any]
    ) -> None:
        alice = two_user_isolation_fixture["alice"]
        bob = two_user_isolation_fixture["bob"]

        response = alice["client"].get(
            f"/projects/{bob['project_id']}/runs/{bob['run_id']}"
        )
        assert response.status_code == 404

    def test_alice_cannot_read_bob_vault_object(
        self, two_user_isolation_fixture: dict[str, Any]
    ) -> None:
        alice = two_user_isolation_fixture["alice"]
        bob = two_user_isolation_fixture["bob"]

        response = alice["client"].get(f"/vault/objects/{bob['object_id']}")
        assert response.status_code == 404

    def test_alice_cannot_read_bob_vault_content(
        self, two_user_isolation_fixture: dict[str, Any]
    ) -> None:
        alice = two_user_isolation_fixture["alice"]
        bob = two_user_isolation_fixture["bob"]

        response = alice["client"].get(f"/vault/objects/{bob['object_id']}/content")
        assert response.status_code == 404

    def test_alice_cannot_read_bob_cloud_projection(
        self, two_user_isolation_fixture: dict[str, Any]
    ) -> None:
        alice = two_user_isolation_fixture["alice"]
        bob = two_user_isolation_fixture["bob"]

        response = alice["client"].get(
            f"/vault/objects/{bob['object_id']}/projection"
        )
        assert response.status_code == 404

    def test_cross_account_cache_keys_do_not_collide(
        self, two_user_isolation_fixture: dict[str, Any]
    ) -> None:
        """Scope-aware cache keys must include the account so private results do
        not leak across users, even with identical project names and content.
        """
        alice = two_user_isolation_fixture["alice"]
        bob = two_user_isolation_fixture["bob"]
        enforcer = ScopeEnforcer(deployment_cell="test")

        alice_subject = SubjectContext(
            account_id=alice["account"]["id"],
            session_id="session-alice",
            auth_method=AuthMethod.PASSWORD,
        )
        bob_subject = SubjectContext(
            account_id=bob["account"]["id"],
            session_id="session-bob",
            auth_method=AuthMethod.PASSWORD,
        )
        alice_scope = enforcer.compile_scope(
            subject=alice_subject,
            project_id=alice["project_id"],
        )
        bob_scope = enforcer.compile_scope(
            subject=bob_subject,
            project_id=bob["project_id"],
        )

        alice_key = enforcer.build_cache_key(alice_scope)
        bob_key = enforcer.build_cache_key(bob_scope)

        assert alice_key != bob_key
        assert alice["account"]["id"] in alice_key
        assert bob["account"]["id"] in bob_key


class TestBackgroundTaskScopeValidation:
    def test_task_missing_subject_is_rejected(self, client: TestClient) -> None:
        _register(client, "task-missing-subject@example.com", "correct-horse-12")
        response = client.post(
            "/scope/validate-task",
            json={
                "task_id": "task-1",
                "task_type": "ingestion",
                "object_domain": "personal_vault",
                "authorization_version": "authz-1.0",
                "key_epoch": "epoch-0",
                "purpose": "test",
            },
        )
        # Missing required subject is caught by schema validation (422).
        assert response.status_code == 422

    def test_task_missing_object_domain_is_rejected(self, client: TestClient) -> None:
        registered = _register(
            client, "task-missing-domain@example.com", "correct-horse-12"
        )
        account_id = registered["account"]["id"]
        response = client.post(
            "/scope/validate-task",
            json={
                "task_id": "task-2",
                "task_type": "ingestion",
                "subject": {
                    "account_id": account_id,
                    "session_id": "session-2",
                    "auth_method": "password",
                },
                "authorization_version": "authz-1.0",
                "key_epoch": "epoch-0",
                "purpose": "test",
            },
        )
        assert response.status_code == 422

    def test_task_missing_authorization_version_is_rejected(
        self, client: TestClient
    ) -> None:
        registered = _register(
            client, "task-missing-authz@example.com", "correct-horse-12"
        )
        account_id = registered["account"]["id"]
        response = client.post(
            "/scope/validate-task",
            json={
                "task_id": "task-3",
                "task_type": "ingestion",
                "subject": {
                    "account_id": account_id,
                    "session_id": "session-3",
                    "auth_method": "password",
                },
                "object_domain": "personal_vault",
                "key_epoch": "epoch-0",
                "purpose": "test",
            },
        )
        assert response.status_code == 422

    def test_task_missing_key_epoch_is_rejected(self, client: TestClient) -> None:
        registered = _register(
            client, "task-missing-epoch@example.com", "correct-horse-12"
        )
        account_id = registered["account"]["id"]
        response = client.post(
            "/scope/validate-task",
            json={
                "task_id": "task-4",
                "task_type": "ingestion",
                "subject": {
                    "account_id": account_id,
                    "session_id": "session-4",
                    "auth_method": "password",
                },
                "object_domain": "personal_vault",
                "authorization_version": "authz-1.0",
                "purpose": "test",
            },
        )
        assert response.status_code == 422

    def test_valid_task_returns_scope_envelope(self, client: TestClient) -> None:
        registered = _register(
            client, "task-valid@example.com", "correct-horse-12"
        )
        account_id = registered["account"]["id"]
        response = client.post(
            "/scope/validate-task",
            json={
                "task_id": "task-5",
                "task_type": "ingestion",
                "subject": {
                    "account_id": account_id,
                    "session_id": "session-5",
                    "auth_method": "password",
                },
                "object_domain": "personal_vault",
                "authorization_version": "authz-1.0",
                "key_epoch": "epoch-0",
                "purpose": "test",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["valid"] is True
        assert body["scope_envelope"]["account_id"] == account_id

    def test_task_subject_mismatch_is_rejected(self, client: TestClient) -> None:
        _register(client, "task-owner@example.com", "correct-horse-12")
        response = client.post(
            "/scope/validate-task",
            json={
                "task_id": "task-6",
                "task_type": "ingestion",
                "subject": {
                    "account_id": "other-account",
                    "session_id": "session-6",
                    "auth_method": "password",
                },
                "object_domain": "personal_vault",
                "authorization_version": "authz-1.0",
                "key_epoch": "epoch-0",
                "purpose": "test",
            },
        )
        assert response.status_code == 403


class TestAccountSwitchCleanup:
    def test_login_revokes_prior_session_and_clears_cache(
        self, client: TestClient
    ) -> None:
        alice = _register(client, "alice-switch@example.com", "correct-horse-12")
        alice_session = client.get("/auth/session")
        assert alice_session.status_code == 200

        response = client.post(
            "/auth/login",
            json={"email": "alice-switch@example.com", "password": "correct-horse-12"},
        )
        assert response.status_code == 200
        # T007: login must clear cached state from the prior session.
        assert "Clear-Site-Data" in response.headers

    def test_account_switch_does_not_leak_prior_projects(
        self, client: TestClient
    ) -> None:
        alice_client = TestClient(client.app)
        _register(alice_client, "alice-no-leak@example.com", "correct-horse-12")
        alice_project = alice_client.post(
            "/projects", json={"name": "Alice 不泄漏项目"}
        ).json()["id"]

        # Switch to Bob using a separate browser session; cross-account deep
        # links must not expose Alice's project.
        bob_client = TestClient(client.app)
        _register(bob_client, "bob-no-leak@example.com", "correct-horse-12")

        # Alice's project must not appear in Bob's list.
        bob_projects = bob_client.get("/projects").json()["active"]
        assert not any(p["ref"]["object_id"] == alice_project for p in bob_projects)

        # Bob's client must not be able to deep-link Alice's project.
        assert bob_client.get(f"/projects/{alice_project}").status_code == 404


class TestScopeContextEndpoint:
    def test_scope_context_returns_rls_summary(self, client: TestClient) -> None:
        registered = _register(
            client, "scope-context@example.com", "correct-horse-12"
        )
        account_id = registered["account"]["id"]

        response = client.get("/scope/context")
        assert response.status_code == 200
        body = response.json()
        assert body["scope_envelope"]["account_id"] == account_id
        assert body["rls_context"]["subject_account_id"] == account_id
        assert body["rls_context"]["authorization_version"]
        assert body["rls_context"]["key_epoch"]
