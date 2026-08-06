"""Issue 39 AC1：会话 Cookie 属性与失效、登出、重定向循环回归测试。

覆盖：Cookie 属性（HttpOnly/SameSite/路径/生命周期/Secure 跟随传输
环境）、登出清除、撤销与过期会话清除、无效 Cookie 不形成登录重定向
循环（401 响应携带清除 Cookie 的 Set-Cookie 头）、会话固定防护
（重新登录后旧令牌失效）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient

from bridges.api.auth import DEVICE_COOKIE_NAME, SESSION_COOKIE_NAME
from bridges.api.main import create_app

_EMAIL_COUNTER = 0


def _register(client: TestClient, tag: str = "1") -> dict[str, Any]:
    global _EMAIL_COUNTER
    _EMAIL_COUNTER += 1
    response = client.post(
        "/auth/register",
        json={
            "username": f"cookie-user-{tag}",
            "qq_email": f"1002{_EMAIL_COUNTER:04d}@qq.com",
            "password": "correct-horse-12",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _session_token(client: TestClient) -> str:
    cookies = client.cookies
    return str(cookies.get(SESSION_COOKIE_NAME, ""))


def test_session_cookie_attributes_are_secure() -> None:
    """会话 Cookie 必须 HttpOnly + SameSite=lax + 固定路径与生命周期。"""
    client = TestClient(create_app())
    response = client.post(
        "/auth/register",
        json={
            "username": "cookie-user-attr",
            "qq_email": "100210@qq.com",
            "password": "correct-horse-12",
        },
    )
    assert response.status_code == 201
    set_cookie = response.headers.get("set-cookie", "")
    assert f"{SESSION_COOKIE_NAME}=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie
    assert "Path=/" in set_cookie
    assert "Max-Age=28800" in set_cookie  # 8 小时生命周期
    # 测试环境为 HTTP：Secure 标志跟随传输环境，不应强制启用
    assert "Secure" not in set_cookie.replace("Secure-", "")


def test_session_cookie_secure_follows_https_scheme() -> None:
    """HTTPS 传输环境下 Cookie 必须带 Secure 标志。"""
    client = TestClient(create_app(), base_url="https://testserver")
    response = client.post(
        "/auth/register",
        json={
            "username": "cookie-user-secure",
            "qq_email": "100211@qq.com",
            "password": "correct-horse-12",
        },
    )
    assert response.status_code == 201
    assert "Secure" in response.headers.get("set-cookie", "")


def test_login_revokes_previous_session_fixes_fixation() -> None:
    """会话固定防护：重新登录后旧会话令牌立即失效，新令牌可用。"""
    client = TestClient(create_app())
    _register(client, "fix1")
    old_token = _session_token(client)
    assert old_token

    response = client.post(
        "/auth/login",
        json={"identifier": "cookie-user-fix1", "password": "correct-horse-12"},
    )
    assert response.status_code == 200
    new_token = _session_token(client)
    assert new_token and new_token != old_token

    # 旧令牌不能再解析（服务端已撤销）
    client.cookies.set(SESSION_COOKIE_NAME, old_token)
    assert client.get("/auth/session").status_code == 401
    # 新令牌仍然有效
    client.cookies.set(SESSION_COOKIE_NAME, new_token)
    assert client.get("/auth/session").status_code == 200


def test_logout_revokes_session_and_clears_cookie() -> None:
    """登出必须撤销服务端会话并清除浏览器 Cookie。"""
    client = TestClient(create_app())
    _register(client, "logout1")
    token = _session_token(client)

    response = client.post("/auth/logout")
    assert response.status_code == 204
    assert "Set-Cookie" in response.headers
    assert "Max-Age=0" in response.headers["set-cookie"]
    # 服务端会话已撤销：旧令牌 401
    client.cookies.set(SESSION_COOKIE_NAME, token)
    assert client.get("/auth/session").status_code == 401


def test_invalid_cookie_cleared_without_redirect_loop() -> None:
    """无效/伪造 Cookie：401 响应必须携带清除该 Cookie 的 Set-Cookie 头。

    前端据此收敛为登录态，不会与「已带 Cookie 的用户跳过登录页」的
    中间件逻辑形成重定向循环。
    """
    client = TestClient(create_app())
    client.cookies.set(SESSION_COOKIE_NAME, "forged-token-not-registered")
    response = client.get("/auth/session")
    assert response.status_code == 401
    set_cookie = response.headers.get("set-cookie", "")
    assert f"{SESSION_COOKIE_NAME}=" in set_cookie
    assert "Max-Age=0" in set_cookie


def test_expired_session_cleared_without_redirect_loop() -> None:
    """过期会话：401 响应携带清除 Cookie 头，且服务端不再接受该令牌。"""
    client = TestClient(create_app())
    _register(client, "expired1")
    token = _session_token(client)
    service = client.app.state.identity_service
    # 直接过期该会话（等价自然过期，避免等待 8 小时）
    session = next(
        s
        for s in service._sessions.values()
        if s.session.account_id
        == next(
            a for a in service._accounts.values() if a.account.username == "cookie-user-expired1"
        ).account.id
    )
    session.session.expires_at = datetime.now(UTC) - timedelta(seconds=1)

    client.cookies.set(SESSION_COOKIE_NAME, token)
    response = client.get("/auth/session")
    assert response.status_code == 401
    assert "Max-Age=0" in response.headers.get("set-cookie", "")


def test_revoked_session_cleared() -> None:
    """被撤销（设备登出全部）的会话令牌：401 并清除 Cookie。"""
    client = TestClient(create_app())
    _register(client, "revoke1")
    token = _session_token(client)
    client.post("/auth/device/logout-all")
    # 重新注入旧令牌：服务端已撤销
    client.cookies.set(SESSION_COOKIE_NAME, token)
    response = client.get("/auth/session")
    assert response.status_code == 401
    assert "Max-Age=0" in response.headers.get("set-cookie", "")


def test_logout_all_clears_device_cookie_too() -> None:
    """登出全部设备账户必须同时清除会话与设备 Cookie。"""
    client = TestClient(create_app())
    _register(client, "dev1")
    response = client.post("/auth/device/logout-all")
    assert response.status_code == 204
    set_cookie = response.headers.get("set-cookie", "")
    assert f"{SESSION_COOKIE_NAME}=" in set_cookie
    assert f"{DEVICE_COOKIE_NAME}=" in set_cookie
    assert "Max-Age=0" in set_cookie
