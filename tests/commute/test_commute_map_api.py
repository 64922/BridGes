"""浏览器地图运行时配置与高德代理（V2 Issue 12）。

``docs/v2/architecture.md`` 第 7 节要求高德浏览器地图的安全密钥**由后端保护**：
这组用例固定 ``GET /commute/map-config`` 只下发 JS API Key（它本身必须出现在
加载器 URL 里）与同源代理路径，以及 ``GET /commute/amap-proxy/{path}`` 在服务端
追加 ``jscode``、只放行数据服务路径、失败时如实降级且不回显密钥。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from bridges.api.main import create_app
from bridges.config import get_settings

JS_KEY = "amap-js-key-plain"
SECURITY_CODE = "amap-security-code-secret"

AMAP_ENV_VARS = (
    "BRIDGES_AMAP_JS_API_KEY",
    "BRIDGES_AMAP_SECURITY_JS_CODE",
    "BRIDGES_AMAP_WEB_SERVICE_KEY",
)


def _register(client: TestClient) -> None:
    response = client.post(
        "/auth/register",
        json={
            "username": "MapUser",
            "qq_email": "445566@qq.com",
            "password": "correct-horse-25",
        },
    )
    assert response.status_code == 201, response.text


@pytest.fixture
def map_client(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[..., TestClient]]:
    """按用例给定的凭据组合构造已登录客户端；用完关闭探测客户端。"""
    created: list[httpx.Client] = []

    def build(
        *,
        js_key: str | None = None,
        security_code: str | None = None,
        probe_handler: Callable[[httpx.Request], httpx.Response] | None = None,
    ) -> TestClient:
        for name in AMAP_ENV_VARS:
            monkeypatch.delenv(name, raising=False)
        if js_key is not None:
            monkeypatch.setenv("BRIDGES_AMAP_JS_API_KEY", js_key)
        if security_code is not None:
            monkeypatch.setenv("BRIDGES_AMAP_SECURITY_JS_CODE", security_code)
        get_settings.cache_clear()
        probe = httpx.Client(transport=httpx.MockTransport(probe_handler))
        created.append(probe)
        client = TestClient(create_app(credential_probe_http_client=probe))
        _register(client)
        return client

    yield build
    for probe in created:
        probe.close()


def test_map_config_requires_an_authenticated_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in AMAP_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    client = TestClient(create_app())

    assert client.get("/commute/map-config").status_code == 401


def test_map_config_sends_js_key_and_proxy_path_but_never_the_security_code(
    map_client: Callable[..., TestClient],
) -> None:
    client = map_client(js_key=JS_KEY, security_code=SECURITY_CODE)

    response = client.get("/commute/map-config")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "configured": True,
        "js_api_key": JS_KEY,
        "service_host_path": "/commute/amap-proxy",
        "security_code_configured": True,
        "notice": None,
    }
    assert SECURITY_CODE not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_map_config_without_credentials_points_at_the_settings_page(
    map_client: Callable[..., TestClient],
) -> None:
    body = map_client().get("/commute/map-config").json()

    assert body["configured"] is False
    assert body["js_api_key"] is None
    assert body["service_host_path"] is None
    assert body["security_code_configured"] is False
    assert "设置页" in body["notice"]


def test_map_config_without_security_code_warns_instead_of_shipping_the_map(
    map_client: Callable[..., TestClient],
) -> None:
    body = map_client(js_key=JS_KEY).get("/commute/map-config").json()

    assert body["configured"] is True
    assert body["js_api_key"] == JS_KEY
    assert body["service_host_path"] is None
    assert body["security_code_configured"] is False
    assert "安全密钥" in body["notice"]


def test_proxy_appends_the_server_side_security_code(
    map_client: Callable[..., TestClient],
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"status": "1", "info": "OK"})

    client = map_client(js_key=JS_KEY, security_code=SECURITY_CODE, probe_handler=handler)

    response = client.get(
        "/commute/amap-proxy/v3/place/text",
        params={"keywords": "华东交通大学图书馆", "jscode": "client-supplied-code"},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "1", "info": "OK"}
    assert len(seen) == 1
    upstream = seen[0]
    assert upstream.url.host == "restapi.amap.com"
    assert upstream.url.path == "/v3/place/text"
    # 服务端密钥覆盖客户端传来的任何 jscode，浏览器侧拿不到它
    assert upstream.url.params["jscode"] == SECURITY_CODE
    assert upstream.url.params["keywords"] == "华东交通大学图书馆"
    assert SECURITY_CODE not in response.text


def test_proxy_refuses_paths_outside_the_data_service(
    map_client: Callable[..., TestClient],
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    client = map_client(js_key=JS_KEY, security_code=SECURITY_CODE, probe_handler=handler)

    response = client.get("/commute/amap-proxy/maps")

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "amap_proxy_path_not_allowed"
    assert seen == [], "越界路径不转发到上游"


def test_proxy_requires_a_configured_security_code(
    map_client: Callable[..., TestClient],
) -> None:
    client = map_client(js_key=JS_KEY)

    response = client.get("/commute/amap-proxy/v3/place/text", params={"keywords": "图书馆"})

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "amap_security_code_missing"


def test_proxy_reports_upstream_failure_without_echoing_the_secret(
    map_client: Callable[..., TestClient],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    client = map_client(js_key=JS_KEY, security_code=SECURITY_CODE, probe_handler=handler)

    response = client.get("/commute/amap-proxy/v5/direction/walking", params={"origin": "1,1"})

    assert response.status_code == 502
    assert response.json()["detail"]["error"] == "amap_proxy_unavailable"
    assert "网络" in response.json()["detail"]["message"]
    assert SECURITY_CODE not in response.text
