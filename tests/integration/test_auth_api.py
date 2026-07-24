"""Integration tests for the authentication API seam.

The seam under test: registration, login, logout, session introspection, and
recovery behave as a secure, cookie-based auth system; unauthenticated requests
to protected routes are rejected with uniform errors.
"""

import pytest
from fastapi.testclient import TestClient

from science_companion.api.main import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def _register(client: TestClient, email: str, password: str) -> None:
    response = client.post(
        "/auth/register",
        json={"email": email, "password": password, "agreed_to_terms": True},
    )
    assert response.status_code == 201


def test_register_returns_account_and_sets_session_cookie(client: TestClient) -> None:
    response = client.post(
        "/auth/register",
        json={"email": "user@example.com", "password": "correct-horse-12", "agreed_to_terms": True},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["account"]["email"] == "user@example.com"
    assert "science_companion_session" in response.cookies


def test_login_with_valid_credentials_sets_session_cookie(client: TestClient) -> None:
    _register(client, "user@example.com", "correct-horse-12")

    response = client.post(
        "/auth/login",
        json={"email": "user@example.com", "password": "correct-horse-12"},
    )
    assert response.status_code == 200
    assert response.json()["account"]["email"] == "user@example.com"
    assert "science_companion_session" in response.cookies


def test_login_with_invalid_credentials_returns_uniform_error(client: TestClient) -> None:
    _register(client, "user@example.com", "correct-horse-12")

    response = client.post(
        "/auth/login",
        json={"email": "user@example.com", "password": "wrong-password-12"},
    )
    assert response.status_code == 401
    assert "邮箱或密码不正确" in response.json()["detail"]["message"]


def test_login_for_unknown_email_returns_same_error(client: TestClient) -> None:
    response = client.post(
        "/auth/login",
        json={"email": "missing@example.com", "password": "correct-horse-12"},
    )
    assert response.status_code == 401
    assert "邮箱或密码不正确" in response.json()["detail"]["message"]


def test_protected_route_rejects_unauthenticated_request(client: TestClient) -> None:
    response = client.get("/me")
    assert response.status_code == 401


def test_session_endpoint_returns_current_subject(client: TestClient) -> None:
    _register(client, "user@example.com", "correct-horse-12")

    response = client.get("/auth/session")
    assert response.status_code == 200
    body = response.json()
    assert body["account"]["email"] == "user@example.com"
    assert body["subject"]["account_id"] == body["account"]["id"]


def test_logout_revokes_session_and_clears_cookie(client: TestClient) -> None:
    _register(client, "user@example.com", "correct-horse-12")

    response = client.post("/auth/logout")
    assert response.status_code == 204
    # After logout the session endpoint must reject the same cookie.
    response = client.get("/auth/session")
    assert response.status_code == 401


def test_recovery_revokes_existing_session(client: TestClient) -> None:
    _register(client, "user@example.com", "correct-horse-12")
    original_cookies = dict(client.cookies)

    # Simulate receiving a recovery token out of band.
    service = client.app.state.identity_service
    token = service.test_create_recovery_token("user@example.com")

    response = client.post(
        "/auth/recover/reset",
        json={"token": token, "new_password": "new-stable-password-12"},
    )
    assert response.status_code == 200

    # The old session cookie must no longer work.
    client.cookies = original_cookies
    response = client.get("/auth/session")
    assert response.status_code == 401


def test_recovery_request_is_silent_for_unknown_email(client: TestClient) -> None:
    response = client.post(
        "/auth/recover",
        json={"email": "missing@example.com"},
    )
    assert response.status_code == 202
    assert response.json()["status"] == "accepted"


def test_register_rejects_duplicate_email(client: TestClient) -> None:
    _register(client, "user@example.com", "correct-horse-12")
    response = client.post(
        "/auth/register",
        json={"email": "user@example.com", "password": "correct-horse-12", "agreed_to_terms": True},
    )
    assert response.status_code == 409
