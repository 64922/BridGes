"""外部探测的真实响应形状契约测试（工单 01 任务 6）。

覆盖 Qwen、Tavily、高德 Web 服务、高德浏览器地图四类探测的**有效／无效两侧**，
并钉住两类"原生缺陷"：

- 旧的高德浏览器地图探测在正文累计超过 256 KiB 时直接判失败（真实加载器约
  968 KB，于是有效 Key 也存不进去）；
- 旧实现用加载器正文里的四个 ``INVALID_USER_*`` 错误串作判定（实测出现 0 次，
  是死代码；若只放开体积上限，就会变成"任意字符串都通过"的假导入）。

信封形状取自官方文档与真实响应（本文件只引用字段名与取值，不复制第三方正文）：
百炼元数据 ``{"success": true, "output": {"total": N, "models": [...]}}``、
Tavily ``{"results": [...]}``、高德 ``{"status", "info", "infocode", ...}``。
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from bridges.ai.fixed_models import CHAT_MODEL_ID
from bridges.api.main import create_app
from bridges.credentials.ids import (
    AMAP_WEB_SERVICE_CREDENTIAL_ID,
    SETTINGS_TAVILY_CREDENTIAL_ID,
)
from bridges.credentials.store import InMemoryCredentialStore

_QWEN_KEY = "sk-qwen-candidate-shape-secret"
_TAVILY_KEY = "tvly-candidate-shape-secret"
_AMAP_KEY = "amap-web-service-candidate-shape-secret"


def _register(client: TestClient) -> None:
    response = client.post(
        "/auth/register",
        json={
            "username": "ProbeShapeUser",
            "qq_email": "445566@qq.com",
            "password": "correct-horse-27",
        },
    )
    assert response.status_code == 201, response.text


def _bailian_metadata(model_id: str = CHAT_MODEL_ID) -> dict[str, Any]:
    """真实百炼模型元数据信封（能力/特性/上下文额度都在 output.models 里）。"""
    return {
        "success": True,
        "output": {
            "total": 1,
            "models": [
                {
                    "model": model_id,
                    "capabilities": ["TG", "VU"],
                    "features": ["function-calling", "structured-outputs"],
                    "inference_metadata": {"request_modality": ["Text", "Image"]},
                    "model_info": {
                        "context_window": 131_072,
                        "max_input_tokens": 130_048,
                    },
                }
            ],
        },
    }


def _qwen_provider(authorized: bool) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if not authorized or request.headers.get("Authorization") != f"Bearer {_QWEN_KEY}":
            return httpx.Response(401, json={"message": "invalid credentials"})
        if request.url.path.endswith("/api/v1/models"):
            return httpx.Response(200, json=_bailian_metadata())
        return httpx.Response(
            200,
            json={
                "model": CHAT_MODEL_ID,
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "ok"}}
                ],
            },
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


def _app(provider: httpx.Client, store: InMemoryCredentialStore) -> TestClient:
    return TestClient(
        create_app(
            runtime_credential_store=store,
            credential_probe_http_client=provider,
        )
    )


@pytest.mark.parametrize("authorized", [True, False])
def test_qwen_probe_must_pass_valid_and_fail_invalid(authorized: bool) -> None:
    store = InMemoryCredentialStore(namespace="runtime")
    provider = _qwen_provider(authorized)
    client = _app(provider, store)
    _register(client)

    response = client.put("/settings/credentials/qwen", json={"api_key": _QWEN_KEY})

    if authorized:
        assert response.status_code == 200, response.text
        assert store.get("global-qwen-api-key") is not None
    else:
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["reason"] == "key_rejected"
        assert store.get("global-qwen-api-key") is None
    assert _QWEN_KEY not in response.text
    provider.close()


def _tavily_provider(
    *, status_code: int, payload: dict[str, Any] | None = None, transport_error: bool = False
) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/search"
        if transport_error:
            raise httpx.ConnectError("no route to host", request=request)
        return httpx.Response(status_code, json=payload or {"results": []})

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    ("status_code", "transport_error", "expected_status", "expected_reason"),
    [
        (200, False, 200, None),
        (401, False, 422, "search_credential_invalid"),
        (429, False, 422, "search_rate_limited"),
        (0, True, 503, "search_upstream_unreachable"),
    ],
)
def test_tavily_probe_sides_and_reasons(
    status_code: int,
    transport_error: bool,
    expected_status: int,
    expected_reason: str | None,
) -> None:
    store = InMemoryCredentialStore(namespace="runtime")
    provider = _tavily_provider(
        status_code=status_code, transport_error=transport_error
    )
    client = _app(provider, store)
    _register(client)

    response = client.put(
        "/settings/credentials/tavily", json={"api_key": _TAVILY_KEY}
    )

    assert response.status_code == expected_status, response.text
    if expected_reason is None:
        assert store.get(SETTINGS_TAVILY_CREDENTIAL_ID) is not None
        assert _TAVILY_KEY not in response.text
        assert response.json()["effective_source"] == "credential_store"
    else:
        assert response.json()["detail"]["reason"] == expected_reason
        assert store.get(SETTINGS_TAVILY_CREDENTIAL_ID) is None
        assert _TAVILY_KEY not in response.text
    provider.close()


def _amap_provider(payload: dict[str, Any], *, status_code: int = 200) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v3/geocode/geo"
        return httpx.Response(status_code, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    ("payload", "expected_status", "expected_reason"),
    [
        (
            {"status": "1", "info": "OK", "infocode": "10000", "geocodes": []},
            200,
            None,
        ),
        # 把浏览器地图（JS API）Key 填到 Web 服务卡：官方平台不符码。
        (
            {"status": "0", "info": "USERKEY_PLAT_NOMATCH", "infocode": "10009"},
            422,
            "amap_platform_mismatch",
        ),
        # 平台对了但没勾选地理编码服务。
        (
            {"status": "0", "info": "SERVICE_NOT_AVAILABLE", "infocode": "10002"},
            422,
            "amap_service_not_enabled",
        ),
        # Key 未绑定合法域名。
        (
            {"status": "0", "info": "INVALID_USER_DOMAIN", "infocode": "10006"},
            422,
            "amap_domain_restricted",
        ),
        # Key 被删除。
        (
            {"status": "0", "info": "USER_KEY_RECYCLED", "infocode": "10013"},
            422,
            "amap_key_recycled",
        ),
    ],
)
def test_amap_web_service_probe_sides_and_reasons(
    payload: dict[str, Any], expected_status: int, expected_reason: str | None
) -> None:
    store = InMemoryCredentialStore(namespace="runtime")
    provider = _amap_provider(payload)
    client = _app(provider, store)
    _register(client)

    response = client.put(
        "/settings/credentials/amap/web-service", json={"api_key": _AMAP_KEY}
    )

    assert response.status_code == expected_status, response.text
    if expected_reason is None:
        assert store.get(AMAP_WEB_SERVICE_CREDENTIAL_ID) is not None
    else:
        assert response.json()["detail"]["reason"] == expected_reason
        assert store.get(AMAP_WEB_SERVICE_CREDENTIAL_ID) is None
    assert _AMAP_KEY not in response.text
    provider.close()


def test_amap_web_service_probe_reports_unreachable_upstream_as_such() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    store = InMemoryCredentialStore(namespace="runtime")
    provider = httpx.Client(transport=httpx.MockTransport(boom))
    client = _app(provider, store)
    _register(client)

    response = client.put(
        "/settings/credentials/amap/web-service", json={"api_key": _AMAP_KEY}
    )

    assert response.status_code == 503, response.text
    detail = response.json()["detail"]
    assert detail["error"] == "credential_probe_unavailable"
    assert detail["reason"] == "amap_upstream_unreachable"
    assert store.get(AMAP_WEB_SERVICE_CREDENTIAL_ID) is None
    provider.close()
