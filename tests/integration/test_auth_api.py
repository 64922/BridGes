"""Integration tests for the authentication API seam.

The seam under test: registration, login, logout, session introspection, and
recovery behave as a secure, cookie-based auth system; the session token only
travels through the secure HttpOnly cookie and never appears in JSON bodies;
unauthenticated requests to protected routes are rejected with uniform errors.
"""

import pytest
from fastapi.testclient import TestClient

from bridges.api.main import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def _register(
    client: TestClient,
    username: str = "桥桥",
    qq_email: str = "123456@qq.com",
    password: str = "correct-horse-12",
) -> dict:
    response = client.post(
        "/auth/register",
        json={"username": username, "qq_email": qq_email, "password": password},
    )
    assert response.status_code == 201, response.text
    return response.json()["account"]


def _set_cookie_header(response) -> str:
    header = response.headers.get("set-cookie")
    assert header is not None
    return header


def test_register_returns_account_and_sets_session_cookie(client: TestClient) -> None:
    response = client.post(
        "/auth/register",
        json={"username": "桥桥", "qq_email": "123456@qq.com", "password": "correct-horse-12"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["account"]["username"] == "桥桥"
    assert body["account"]["qq_email"] == "123456@qq.com"
    assert "bridges_session" in response.cookies


def test_session_cookie_contract(client: TestClient) -> None:
    response = client.post(
        "/auth/register",
        json={"username": "桥桥", "qq_email": "123456@qq.com", "password": "correct-horse-12"},
    )
    header = _set_cookie_header(response)
    assert "HttpOnly" in header
    assert "SameSite=lax" in header
    assert "Path=/" in header
    assert "Max-Age=28800" in header  # mirrors the 8h session TTL
    # Plain HTTP (development) must not mark the cookie Secure.
    assert "Secure" not in header


def test_session_cookie_is_secure_over_https() -> None:
    secure_client = TestClient(create_app(), base_url="https://testserver")
    response = secure_client.post(
        "/auth/register",
        json={"username": "桥桥", "qq_email": "123456@qq.com", "password": "correct-horse-12"},
    )
    header = _set_cookie_header(response)
    assert "Secure" in header
    assert "HttpOnly" in header


def test_session_token_never_appears_in_json(client: TestClient) -> None:
    for path, payload in (
        (
            "/auth/register",
            {"username": "桥桥", "qq_email": "123456@qq.com", "password": "correct-horse-12"},
        ),
        ("/auth/login", {"identifier": "桥桥", "password": "correct-horse-12"}),
    ):
        response = client.post(path, json=payload)
        assert response.status_code in (200, 201)
        body = response.json()
        assert "session_token" not in body
        assert "session_token" not in body.get("session", {})
        # The raw cookie value must not leak into the response body either.
        token = response.cookies.get("bridges_session")
        assert token
        assert token not in response.text


def test_login_with_username_sets_session_cookie(client: TestClient) -> None:
    account = _register(client, username="BridgeUser")

    response = client.post(
        "/auth/login",
        json={"identifier": "bridgeuser", "password": "correct-horse-12"},
    )
    assert response.status_code == 200
    assert response.json()["account"]["id"] == account["id"]
    assert "bridges_session" in response.cookies


def test_login_with_qq_email_sets_session_cookie(client: TestClient) -> None:
    account = _register(client)

    response = client.post(
        "/auth/login",
        json={"identifier": "123456@qq.com", "password": "correct-horse-12"},
    )
    assert response.status_code == 200
    assert response.json()["account"]["id"] == account["id"]


def test_login_with_invalid_credentials_returns_uniform_error(client: TestClient) -> None:
    _register(client)

    response = client.post(
        "/auth/login",
        json={"identifier": "桥桥", "password": "wrong-password-12"},
    )
    assert response.status_code == 401
    assert "用户名或密码不正确" in response.json()["detail"]["message"]


@pytest.mark.parametrize("identifier", ["missing-user", "999999@qq.com"])
def test_login_for_unknown_identifier_returns_same_error(
    client: TestClient, identifier: str
) -> None:
    response = client.post(
        "/auth/login",
        json={"identifier": identifier, "password": "correct-horse-12"},
    )
    assert response.status_code == 401
    assert "用户名或密码不正确" in response.json()["detail"]["message"]


@pytest.mark.parametrize(
    "qq_email",
    ["abc@qq.com", "123456@foxmail.com", "123456@qq.com.evil.cn", "not-an-email"],
)
def test_register_rejects_non_qq_mailbox(client: TestClient, qq_email: str) -> None:
    response = client.post(
        "/auth/register",
        json={"username": "桥桥", "qq_email": qq_email, "password": "correct-horse-12"},
    )
    assert response.status_code == 400
    assert "QQ 邮箱" in response.json()["detail"]["message"]


def test_register_rejects_duplicate_username_case_insensitive(client: TestClient) -> None:
    _register(client, username="BridgeUser")
    response = client.post(
        "/auth/register",
        json={
            "username": "bridgeuser",
            "qq_email": "654321@qq.com",
            "password": "correct-horse-12",
        },
    )
    assert response.status_code == 409
    # Uniform message: must not reveal which identifier is taken.
    assert response.json()["detail"]["message"] == "无法完成注册。"


def test_register_rejects_duplicate_qq_email(client: TestClient) -> None:
    _register(client)
    response = client.post(
        "/auth/register",
        json={
            "username": "另一个用户",
            "qq_email": "123456@qq.com",
            "password": "correct-horse-12",
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["message"] == "无法完成注册。"


def test_protected_route_rejects_unauthenticated_request(client: TestClient) -> None:
    response = client.get("/me")
    assert response.status_code == 401


def test_session_endpoint_returns_current_subject(client: TestClient) -> None:
    account = _register(client)

    response = client.get("/auth/session")
    assert response.status_code == 200
    body = response.json()
    assert body["account"]["username"] == account["username"]
    assert body["subject"]["account_id"] == account["id"]


def test_logout_revokes_session_and_clears_cookie(client: TestClient) -> None:
    _register(client)

    response = client.post("/auth/logout")
    assert response.status_code == 204
    # After logout the session endpoint must reject the same cookie.
    response = client.get("/auth/session")
    assert response.status_code == 401


def test_recovery_revokes_existing_session(client: TestClient) -> None:
    _register(client)
    original_cookies = dict(client.cookies)

    # Simulate receiving a recovery token out of band.
    service = client.app.state.identity_service  # type: ignore[attr-defined]
    token = service.test_create_recovery_token("123456@qq.com")

    response = client.post(
        "/auth/recover/reset",
        json={"token": token, "new_password": "new-stable-password-12"},
    )
    assert response.status_code == 200
    assert "session_token" not in response.text

    # The old session cookie must no longer work.
    client.cookies = original_cookies
    response = client.get("/auth/session")
    assert response.status_code == 401


def test_recovery_request_is_silent_for_unknown_qq_email(client: TestClient) -> None:
    response = client.post(
        "/auth/recover",
        json={"qq_email": "999999@qq.com"},
    )
    assert response.status_code == 202
    assert response.json()["status"] == "accepted"
