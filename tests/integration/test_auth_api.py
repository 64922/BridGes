"""Integration tests for the authentication API seam.

The seam under test: registration, login, logout, session introspection, and
recovery behave as a secure, cookie-based auth system; the session token only
travels through the secure HttpOnly cookie and never appears in JSON bodies;
unauthenticated requests to protected routes are rejected with uniform errors.
"""

import base64
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from bridges.api.main import create_app

_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


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
    assert '"cache"' in response.headers["clear-site-data"]
    assert '"storage"' not in response.headers["clear-site-data"]
    # After logout the session endpoint must reject the same cookie.
    response = client.get("/auth/session")
    assert response.status_code == 401


def test_logout_clears_an_invalid_session_cookie(client: TestClient) -> None:
    client.cookies.set("bridges_session", "expired-or-forged")

    response = client.post("/auth/logout")

    assert response.status_code == 204
    assert "bridges_session=" in response.headers["set-cookie"]
    assert "Max-Age=0" in response.headers["set-cookie"]


def test_session_rejection_clears_an_invalid_cookie_without_logout_race(
    client: TestClient,
) -> None:
    client.cookies.set("bridges_session", "expired-or-forged")

    response = client.get("/auth/session")

    assert response.status_code == 401
    assert "bridges_session=" in response.headers["set-cookie"]
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert response.headers["cache-control"] == "no-store"


def test_profile_update_and_avatar_access_are_scoped_to_current_session() -> None:
    app = create_app()
    alice = TestClient(app)
    bob = TestClient(app)
    alice_account = _register(alice, username="Alice", qq_email="111111@qq.com")
    bob_account = _register(bob, username="Bob", qq_email="222222@qq.com")

    update = alice.patch(
        "/auth/profile",
        json={"username": "Alice-Renamed", "avatar_choice": "knowledge"},
    )
    assert update.status_code == 200
    assert update.json()["id"] == alice_account["id"]
    assert update.json()["qq_email"] == alice_account["qq_email"]

    upload = alice.put(
        "/auth/profile/avatar",
        content=_PNG_1X1,
        headers={"content-type": "image/png"},
    )
    assert upload.status_code == 200
    assert upload.json()["avatar_choice"] == "uploaded"
    assert "C:\\" not in upload.text

    avatar = alice.get("/auth/profile/avatar")
    assert avatar.status_code == 200
    assert avatar.headers["content-type"] == "image/png"
    assert avatar.content == _PNG_1X1
    assert avatar.headers["cache-control"] == "private, no-store"

    # 猜测另一个账户 ID 不会改变授权主体：额外 query 被忽略，Bob 只能改自己。
    guessed_update = bob.patch(
        f"/auth/profile?account_id={alice_account['id']}",
        json={"username": "Bob-Renamed", "avatar_choice": "bridge"},
    )
    assert guessed_update.status_code == 200
    assert guessed_update.json()["id"] == bob_account["id"]
    assert alice.get("/auth/session").json()["account"]["username"] == "Alice-Renamed"

    guessed_avatar = bob.get(
        f"/auth/profile/avatar?account_id={alice_account['id']}"
    )
    assert guessed_avatar.status_code == 404


def test_avatar_upload_rejects_declared_type_mismatch_and_oversize(
    client: TestClient,
) -> None:
    _register(client)

    mismatch = client.put(
        "/auth/profile/avatar",
        content=_PNG_1X1,
        headers={"content-type": "image/jpeg"},
    )
    assert mismatch.status_code == 400
    assert "类型" in mismatch.json()["detail"]["message"]

    oversize = client.put(
        "/auth/profile/avatar",
        content=b"x" * (2 * 1024 * 1024 + 1),
        headers={"content-type": "image/png"},
    )
    assert oversize.status_code == 413
    assert "2 MiB" in oversize.json()["detail"]["message"]


def test_key_settings_report_unconfigured_and_enforce_recent_password(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register(client)
    initial = client.get("/auth/key-settings")
    assert initial.status_code == 200
    assert initial.json() == {
        "status": "unconfigured",
        "configured": False,
        "message": "尚未配置百炼密钥。",
        "next_step": "完成密钥接入后，可在本页录入并验证；现在请勿在聊天中粘贴密钥。",
    }

    service = client.app.state.identity_service  # type: ignore[attr-defined]
    initial_now = service._now()
    monkeypatch.setattr(service, "_now", lambda: initial_now + timedelta(minutes=6))

    protected = client.get("/auth/key-settings")
    assert protected.status_code == 403
    assert protected.json()["detail"]["error"] == "reauth_required"

    wrong = client.post(
        "/auth/reauthenticate",
        json={"password": "wrong-password-12"},
    )
    assert wrong.status_code == 401
    assert "密码不正确" in wrong.json()["detail"]["message"]

    confirmed = client.post(
        "/auth/reauthenticate",
        json={"password": "correct-horse-12"},
    )
    assert confirmed.status_code == 204
    assert client.get("/auth/key-settings").status_code == 200


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
