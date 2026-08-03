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
    body = initial.json()
    assert body["status"] == "unconfigured"
    assert body["configured"] is False
    assert body["key_tail"] is None
    # 固定能力矩阵逐项呈现真实"未探测"状态，无 Stub 成功。
    assert [c["capability_id"] for c in body["capabilities"]] == [
        "chat",
        "embedding",
        "asr",
        "tts",
        "image",
        "video",
    ]
    assert all(c["status"] == "not_probed" for c in body["capabilities"])
    assert all(c["can_retry"] is False for c in body["capabilities"])
    assert body["message"] == "尚未配置百炼密钥。"

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


# ---------------------------------------------------------------------------
# Issue 10：账户级百炼 Key 与固定能力探测 API
# ---------------------------------------------------------------------------


class _FakeProbeRunner:
    """探测替身：可配置逐能力成功/失败；只允许测试环境使用。"""

    def __init__(self, failures: dict[str, str] | None = None) -> None:
        self.failures = failures or {}
        self.calls: list[str] = []

    def probe(self, binding, client) -> object:
        self.calls.append(binding.capability_id)
        if binding.capability_id in self.failures:
            from bridges.credentials.probes import ProbeError

            raise ProbeError(
                self.failures[binding.capability_id], code="fake_failure"
            )
        from bridges.credentials.probes import ProbeOutcome

        return ProbeOutcome(success=True, message="探测成功。")


@pytest.fixture
def credential_client() -> TestClient:
    """同步探测 + 替身 runner 的客户端，保证测试确定性。"""
    app = create_app()
    service = app.state.credential_service  # type: ignore[attr-defined]
    service._sync_probes = True
    service._probes._runner = _FakeProbeRunner()
    return TestClient(app)


def _reauth(client: TestClient) -> None:
    response = client.post(
        "/auth/reauthenticate", json={"password": "correct-horse-12"}
    )
    assert response.status_code == 204


def test_key_save_probes_all_capabilities_and_never_leaks_key(
    credential_client: TestClient,
) -> None:
    _register(credential_client)
    _reauth(credential_client)

    response = credential_client.put(
        "/auth/key-settings",
        json={"key": "sk-probe-1234567890abcdef"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "configured"
    assert body["configured"] is True
    assert body["key_tail"] == "…cdef"
    # 完整 Key 绝不进入 API 响应。
    assert "sk-probe" not in response.text
    assert all(c["status"] == "available" for c in body["capabilities"])

    # 审计事件存在且不含 Key 正文。
    events = credential_client.app.state.observability_service.list_audit_events(
        action="key_save"
    )
    assert len(events) == 1
    assert "sk-probe" not in events[0].model_dump_json()
    assert events[0].details == {"operation": "save"}


def test_key_save_and_delete_require_recent_password(
    credential_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register(credential_client)
    _reauth(credential_client)

    service = credential_client.app.state.identity_service  # type: ignore[attr-defined]
    initial_now = service._now()
    monkeypatch.setattr(service, "_now", lambda: initial_now + timedelta(minutes=6))

    denied = credential_client.put(
        "/auth/key-settings", json={"key": "sk-probe-1234567890abcdef"}
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["error"] == "reauth_required"

    denied_delete = credential_client.delete("/auth/key-settings")
    assert denied_delete.status_code == 403

    _reauth(credential_client)
    saved = credential_client.put(
        "/auth/key-settings", json={"key": "sk-probe-1234567890abcdef"}
    )
    assert saved.status_code == 200

    # 再认证后跳过 6 分钟（>5 分钟 TTL），删除必须再次确认密码。
    monkeypatch.setattr(service, "_now", lambda: initial_now + timedelta(minutes=12))
    assert credential_client.delete("/auth/key-settings").status_code == 403
    _reauth(credential_client)

    deleted = credential_client.delete("/auth/key-settings")
    assert deleted.status_code == 200
    body = deleted.json()
    assert body["status"] == "unconfigured"
    assert body["configured"] is False
    assert all(c["status"] == "not_probed" for c in body["capabilities"])


def test_key_isolation_between_accounts(credential_client: TestClient) -> None:
    alice = credential_client
    _register(alice, username="Alice", qq_email="111111@qq.com")
    _reauth(alice)
    bob = TestClient(credential_client.app)
    _register(bob, username="Bob", qq_email="222222@qq.com")
    _reauth(bob)

    saved = alice.put(
        "/auth/key-settings", json={"key": "sk-alice-key-0000"}
    )
    assert saved.status_code == 200
    assert saved.json()["key_tail"] == "…0000"

    # Bob 看不到 Alice 的 Key，自己的页面保持未配置。
    bob_view = bob.get("/auth/key-settings")
    assert bob_view.status_code == 200
    assert bob_view.json()["configured"] is False
    assert "sk-alice" not in bob_view.text
    # 猜测 account_id 无法读取 Alice 的配置。
    guessed = bob.get("/auth/key-settings?account_id=alice")
    assert guessed.json()["configured"] is False
    assert alice.get("/auth/key-settings").json()["configured"] is True


def test_partial_failure_only_disables_that_capability(
    credential_client: TestClient,
) -> None:
    app = credential_client.app
    runner = _FakeProbeRunner(failures={"asr": "语音转写探测失败。"})
    app.state.credential_service._probes._runner = runner  # type: ignore[attr-defined]
    _register(credential_client)
    _reauth(credential_client)

    response = credential_client.put(
        "/auth/key-settings", json={"key": "sk-probe-1234567890abcdef"}
    )
    assert response.status_code == 200
    by_id = {c["capability_id"]: c for c in response.json()["capabilities"]}
    assert by_id["asr"]["status"] == "unavailable"
    assert by_id["asr"]["message"] == "语音转写探测失败。"
    assert by_id["asr"]["can_retry"] is True
    assert by_id["chat"]["status"] == "available"

    # 单项同模型重试：修复 runner 后只重试该项。
    runner.failures.clear()
    retried = credential_client.post("/auth/key-settings/probes/asr/retry")
    assert retried.status_code == 200
    by_id = {c["capability_id"]: c for c in retried.json()["capabilities"]}
    assert by_id["asr"]["status"] == "available"
    # 重试只探测指定能力，不重复全量。
    assert runner.calls.count("asr") == 2
    assert runner.calls.count("chat") == 1


def test_probe_all_and_unknown_capability_retry(
    credential_client: TestClient,
) -> None:
    app = credential_client.app
    runner = _FakeProbeRunner(failures={"video": "视频生成探测失败。"})
    app.state.credential_service._probes._runner = runner  # type: ignore[attr-defined]
    _register(credential_client)
    _reauth(credential_client)
    credential_client.put(
        "/auth/key-settings", json={"key": "sk-probe-1234567890abcdef"}
    )

    unknown = credential_client.post(
        "/auth/key-settings/probes/unknown/retry"
    )
    assert unknown.status_code == 400

    refreshed = credential_client.post("/auth/key-settings/probes")
    assert refreshed.status_code == 200
    by_id = {c["capability_id"]: c for c in refreshed.json()["capabilities"]}
    assert by_id["video"]["status"] == "unavailable"


def test_save_rejects_short_key(credential_client: TestClient) -> None:
    _register(credential_client)
    _reauth(credential_client)
    response = credential_client.put(
        "/auth/key-settings", json={"key": "short"}
    )
    assert response.status_code == 422
