"""Integration tests for the authentication API seam.

The seam under test: registration, login, logout, session introspection, and
recovery behave as a secure, cookie-based auth system; the session token only
travels through the secure HttpOnly cookie and never appears in JSON bodies;
unauthenticated requests to protected routes are rejected with uniform errors.
"""

import base64

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


def test_device_cookie_contract(client: TestClient) -> None:
    """设备 Cookie 与会话 Cookie 同合同：HttpOnly/SameSite/Path/Max-Age=30 天。"""
    response = client.post(
        "/auth/register",
        json={"username": "桥桥", "qq_email": "123456@qq.com", "password": "correct-horse-12"},
    )
    header = response.headers.get("set-cookie")
    assert header is not None
    assert "bridges_device=" in header
    assert "HttpOnly" in header
    assert "SameSite=lax" in header
    assert "Path=/" in header
    assert "Max-Age=2592000" in header  # 30 天
    # 明文 HTTP（开发环境）不得标记 Secure。
    assert "Secure" not in header


def test_stale_device_operation_is_rejected_with_conflict() -> None:
    """并发切换竞态守卫：旧操作的 operation_id 不得改写更新的切换结果。"""
    app = create_app()
    alice = TestClient(app)
    bob_creator = TestClient(app)
    _register(alice, username="Alice", qq_email="111111@qq.com")
    _register(bob_creator, username="Bob", qq_email="222222@qq.com")
    added = alice.post(
        "/auth/device/accounts/add",
        json={"identifier": "Bob", "password": "correct-horse-12"},
    )
    assert added.status_code == 200
    accounts = alice.get("/auth/device/accounts").json()["accounts"]
    alice_session_id = next(
        item["session_id"] for item in accounts if item["username"] == "Alice"
    )

    # 第一次切换成功（operation 1000 已被记录）。
    first = alice.post(
        "/auth/device/switch",
        json={"session_id": alice_session_id},
        headers={"X-Bridges-Account-Operation": "1000"},
    )
    assert first.status_code == 200

    # 旧操作编号（<= 已记录值）重放必须被拒绝，且当前会话不被动摇。
    stale = alice.post(
        "/auth/device/switch",
        json={"session_id": alice_session_id},
        headers={"X-Bridges-Account-Operation": "999"},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["error"] == "device_operation_stale"
    # 会话仍为第一次切换后的账户。
    assert alice.get("/auth/session").json()["account"]["username"] == "Alice"


def test_session_cookie_honors_explicit_secure_config_over_plain_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BRIDGES_SESSION_COOKIE_SECURE=true 时即使走明文 HTTP 也强制 Secure。

    覆盖反向代理 TLS 终止导致 request.url.scheme 为 http 的生产场景。
    """
    from bridges.config import get_settings

    monkeypatch.setenv("BRIDGES_SESSION_COOKIE_SECURE", "true")
    get_settings.cache_clear()
    client = TestClient(create_app())
    response = client.post(
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


def test_key_settings_contract_is_removed(client: TestClient) -> None:
    """GQ-06：账户级百炼密钥用户面退出公开合同，旧 URL 一律 404。"""
    _register(client)
    for method, url in [
        ("GET", "/auth/key-settings"),
        ("PUT", "/auth/key-settings"),
        ("DELETE", "/auth/key-settings"),
        ("POST", "/auth/key-settings/probes"),
        ("POST", "/auth/key-settings/probes/chat/retry"),
    ]:
        response = client.request(method, url)
        assert response.status_code == 404, (method, url, response.text)
    # 测试环境假 Key 探测钩子同样不再存在。
    assert client.post("/_test/capabilities").status_code == 404


def test_reauthenticate_contract_remains_for_sensitive_settings(
    client: TestClient,
) -> None:
    """SMTP/数据导出等敏感设置仍要求近期密码确认（GQ-06 保留再认证合同）。"""
    _register(client)
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


def test_device_accounts_add_and_switch_without_revoking_the_previous_session() -> None:
    app = create_app()
    alice = TestClient(app)
    bob_creator = TestClient(app)
    alice_account = _register(alice, username="Alice", qq_email="111111@qq.com")
    _register(bob_creator, username="Bob", qq_email="222222@qq.com")

    initial = alice.get("/auth/device/accounts")
    assert initial.status_code == 200
    assert [item["username"] for item in initial.json()["accounts"]] == ["Alice"]
    alice_session_id = initial.json()["current_session_id"]

    added = alice.post(
        "/auth/device/accounts/add",
        json={"identifier": "Bob", "password": "correct-horse-12"},
    )
    assert added.status_code == 200
    assert added.json()["current_account"]["username"] == "Bob"
    assert alice.get("/auth/session").json()["account"]["username"] == "Bob"

    accounts = alice.get("/auth/device/accounts").json()["accounts"]
    assert {item["username"] for item in accounts} == {"Alice", "Bob"}
    alice_handle = next(item["session_id"] for item in accounts if item["username"] == "Alice")
    assert alice_handle == alice_session_id

    switched = alice.post("/auth/device/switch", json={"session_id": alice_handle})
    assert switched.status_code == 200
    assert switched.json()["current_account"]["id"] == alice_account["id"]
    assert alice.get("/auth/session").json()["account"]["username"] == "Alice"

    guessed = alice.post(
        "/auth/device/switch",
        json={"session_id": alice_account["id"]},
    )
    assert guessed.status_code == 403
    assert guessed.json()["detail"]["error"] == "device_account_unavailable"


def test_device_avatar_endpoint_is_scoped_to_registered_device_session() -> None:
    app = create_app()
    alice = TestClient(app)
    bob_creator = TestClient(app)
    _register(alice, username="Alice", qq_email="111111@qq.com")
    _register(bob_creator, username="Bob", qq_email="222222@qq.com")
    added = alice.post(
        "/auth/device/accounts/add",
        json={"identifier": "Bob", "password": "correct-horse-12"},
    )
    bob_session_id = added.json()["current_session_id"]
    alice_session_id = next(
        item["session_id"]
        for item in alice.get("/auth/device/accounts").json()["accounts"]
        if item["username"] == "Alice"
    )

    # Bob 先上传头像。
    bob_creator.put(
        "/auth/profile/avatar",
        content=_PNG_1X1,
        headers={"content-type": "image/png"},
    )
    # 当前账户（Alice）经设备作用域端点读取 Bob 会话头像。
    avatar = alice.get(f"/auth/device/accounts/{bob_session_id}/avatar")
    assert avatar.status_code == 200
    assert avatar.content == _PNG_1X1
    assert avatar.headers["content-type"] == "image/png"
    assert avatar.headers["cache-control"] == "private, no-store"

    # 未注册在本设备的会话（用 Bob 自己设备的 cookie 也不可读 Alice 会话头像）。
    foreign = bob_creator.get(f"/auth/device/accounts/{alice_session_id}/avatar")
    assert foreign.status_code == 403
    # 未注册的会话 ID 同样拒绝。
    guessed = alice.get("/auth/device/accounts/session-does-not-exist/avatar")
    assert guessed.status_code == 403


def test_expired_device_account_requires_password_and_does_not_leak_session_state() -> None:
    app = create_app()
    alice = TestClient(app)
    bob_creator = TestClient(app)
    _register(alice, username="Alice", qq_email="111111@qq.com")
    _register(bob_creator, username="Bob", qq_email="222222@qq.com")
    added = alice.post(
        "/auth/device/accounts/add",
        json={"identifier": "Bob", "password": "correct-horse-12"},
    )
    bob_session_id = added.json()["current_session_id"]
    alice_session_id = next(
        item["session_id"]
        for item in alice.get("/auth/device/accounts").json()["accounts"]
        if item["username"] == "Alice"
    )
    assert (
        alice.post("/auth/device/switch", json={"session_id": alice_session_id}).status_code
        == 200
    )
    service = app.state.identity_service
    service.revoke_session(bob_session_id)

    stale = alice.post("/auth/device/switch", json={"session_id": bob_session_id})
    assert stale.status_code == 403
    assert stale.json()["detail"]["error"] == "device_account_unavailable"
    assert "仍然有效" not in stale.json()["detail"]["message"]

    wrong = alice.post(
        "/auth/device/reauthenticate",
        json={"session_id": bob_session_id, "password": "wrong-password-12"},
    )
    assert wrong.status_code == 401
    assert wrong.json()["detail"]["error"] == "device_reauthentication_failed"

    restored = alice.post(
        "/auth/device/reauthenticate",
        json={"session_id": bob_session_id, "password": "correct-horse-12"},
    )
    assert restored.status_code == 200
    assert restored.json()["current_account"]["username"] == "Bob"
    assert alice.get("/auth/session").json()["account"]["username"] == "Bob"


def test_device_logout_current_falls_back_and_logout_all_clears_every_local_session() -> None:
    app = create_app()
    alice = TestClient(app)
    bob_creator = TestClient(app)
    _register(alice, username="Alice", qq_email="111111@qq.com")
    _register(bob_creator, username="Bob", qq_email="222222@qq.com")
    added = alice.post(
        "/auth/device/accounts/add",
        json={"identifier": "Bob", "password": "correct-horse-12"},
    )
    bob_session_id = added.json()["current_session_id"]

    logged_out = alice.post("/auth/device/logout")
    assert logged_out.status_code == 200
    assert logged_out.json()["current_account"]["username"] == "Alice"
    assert alice.get("/auth/session").json()["account"]["username"] == "Alice"
    assert app.state.identity_service.get_session(bob_session_id).revoked_at is not None

    all_out = alice.post("/auth/device/logout-all")
    assert all_out.status_code == 204
    assert alice.get("/auth/session").status_code == 401


def test_device_logout_all_revokes_current_session_with_invalid_device_cookie() -> None:
    app = create_app()
    client = TestClient(app)
    _register(client, username="Alice", qq_email="111111@qq.com")
    session_id = client.get("/auth/session").json()["session"]["id"]
    client.cookies.set("bridges_device", "forged-device-token")

    response = client.post("/auth/device/logout-all")

    assert response.status_code == 204
    assert app.state.identity_service.get_session(session_id).revoked_at is not None
