"""Issue 39 AC2：CSRF 来源校验回归测试。

中间件对改变状态的请求（POST/PUT/PATCH/DELETE）校验 ``Origin`` /
``Referer`` 来源：跨站页面（浏览器必带与页面同源的 Origin）必须被拒绝；
同源、环回开发代理与显式配置的来源放行；双头缺失的非浏览器客户端放行。
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from bridges.api.main import create_app
from bridges.config import get_settings


def _register(client: TestClient) -> dict[str, Any]:
    response = client.post(
        "/auth/register",
        json={
            "username": "alice-csrf",
            "qq_email": "100101@qq.com",
            "password": "correct-horse-12",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_state_changing_request_with_cross_site_origin_rejected() -> None:
    """跨站脚本/表单请求（Origin 与受信来源不一致）必须 403 拒绝。"""
    client = TestClient(create_app())
    response = client.post(
        "/auth/register",
        json={"username": "evil", "qq_email": "100102@qq.com", "password": "correct-horse-12"},
        headers={"Origin": "https://evil.example.com"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "csrf_origin_rejected"


def test_cross_site_origin_rejected_even_with_valid_session() -> None:
    """已登录会话下跨站请求同样被拒绝（Cookie 自动携带场景的核心攻击）。"""
    client = TestClient(create_app())
    _register(client)
    response = client.post(
        "/auth/device/logout-all",
        headers={"Origin": "https://attacker.example.com"},
    )
    assert response.status_code == 403
    # 会话未被破坏：来源校验拒绝在认证之前，不产生任何副作用
    assert response.json()["detail"]["error"] == "csrf_origin_rejected"
    session = client.get("/auth/session")
    assert session.status_code == 200


def test_matching_origin_allowed() -> None:
    """来源与请求 Host 一致时放行（同源请求）。"""
    client = TestClient(create_app())
    response = client.post(
        "/auth/register",
        json={
            "username": "alice-csrf2",
            "qq_email": "100103@qq.com",
            "password": "correct-horse-12",
        },
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 201, response.text


def test_loopback_dev_proxy_origin_allowed() -> None:
    """本地开发经 Next.js 代理：浏览器来源 localhost:3000，API Host 127.0.0.1:8000。"""
    client = TestClient(create_app(), base_url="http://127.0.0.1:8000")
    response = client.post(
        "/auth/register",
        json={
            "username": "alice-csrf3",
            "qq_email": "100104@qq.com",
            "password": "correct-horse-12",
        },
        headers={"Origin": "http://localhost:3000"},
    )
    assert response.status_code == 201, response.text


def test_non_browser_client_without_origin_allowed() -> None:
    """无 Origin/Referer 的非浏览器客户端放行（curl/TestClient/脚本）。"""
    client = TestClient(create_app())
    _register(client)
    response = client.post("/auth/device/logout-all")
    assert response.status_code == 204


def test_mismatched_referer_rejected() -> None:
    """仅带 Referer 且来源不匹配时同样拒绝。"""
    client = TestClient(create_app())
    response = client.post(
        "/auth/register",
        json={
            "username": "alice-csrf4",
            "qq_email": "100105@qq.com",
            "password": "correct-horse-12",
        },
        headers={"Referer": "https://evil.example.com/login"},
    )
    assert response.status_code == 403


def test_matching_referer_allowed() -> None:
    """Referer 与受信来源一致时放行。"""
    client = TestClient(create_app())
    response = client.post(
        "/auth/register",
        json={
            "username": "alice-csrf5",
            "qq_email": "100106@qq.com",
            "password": "correct-horse-12",
        },
        headers={"Referer": "http://testserver/login"},
    )
    assert response.status_code == 201, response.text


def test_forwarded_origin_allowed() -> None:
    """受信反代转发 X-Forwarded-Proto/Host 时按转发来源放行。"""
    client = TestClient(create_app())
    response = client.post(
        "/auth/register",
        json={
            "username": "alice-csrf6",
            "qq_email": "100107@qq.com",
            "password": "correct-horse-12",
        },
        headers={
            "Origin": "https://bridges.example.com",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "bridges.example.com",
        },
    )
    assert response.status_code == 201, response.text


def test_forwarded_origin_mismatch_still_rejected() -> None:
    """转发来源与 Origin 不一致时仍拒绝（防代理头伪造配合跨站来源）。"""
    client = TestClient(create_app())
    response = client.post(
        "/auth/register",
        json={
            "username": "alice-csrf7",
            "qq_email": "100108@qq.com",
            "password": "correct-horse-12",
        },
        headers={
            "Origin": "https://evil.example.com",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "bridges.example.com",
        },
    )
    assert response.status_code == 403


def test_read_methods_never_rejected() -> None:
    """只读请求（GET）不因跨站来源被拒绝（浏览器图片/链接预取不受影响）。"""
    client = TestClient(create_app())
    response = client.get(
        "/health",
        headers={"Origin": "https://evil.example.com"},
    )
    assert response.status_code == 200


def test_explicit_allowed_origins_configuration(
    monkeypatch: Any,
) -> None:
    """显式配置 BRIDGES_ALLOWED_ORIGINS 后只放行清单内来源。"""
    monkeypatch.setenv("BRIDGES_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
    get_settings.cache_clear()
    try:
        client = TestClient(create_app())
        # 清单内的来源放行（即使 Host 是 testserver，不匹配环回推导）
        response = client.post(
            "/auth/register",
            json={
                "username": "alice-csrf8",
                "qq_email": "100109@qq.com",
                "password": "correct-horse-12",
            },
            headers={"Origin": "http://localhost:3000"},
        )
        assert response.status_code == 201, response.text
        # 清单外的环回来源也拒绝（显式配置优先于环回兜底）
        response = client.post(
            "/auth/register",
            json={
                "username": "alice-csrf9",
                "qq_email": "100110@qq.com",
                "password": "correct-horse-12",
            },
            headers={"Origin": "http://localhost:9999"},
        )
        assert response.status_code == 403
    finally:
        monkeypatch.delenv("BRIDGES_ALLOWED_ORIGINS", raising=False)
        get_settings.cache_clear()
