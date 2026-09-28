from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from bridges.api.main import create_app
from bridges.config import get_settings
from bridges.credentials.ids import (
    AMAP_BROWSER_MAP_CREDENTIAL_ID,
    AMAP_WEB_SERVICE_CREDENTIAL_ID,
    SETTINGS_TAVILY_CREDENTIAL_ID,
)
from bridges.credentials.store import InMemoryCredentialStore


def _register(client: TestClient) -> None:
    response = client.post(
        "/auth/register",
        json={
            "username": "CredentialUser",
            "qq_email": "112233@qq.com",
            "password": "correct-horse-25",
        },
    )
    assert response.status_code == 201, response.text


def test_credential_settings_require_an_authenticated_account() -> None:
    client = TestClient(create_app())

    response = client.get("/settings/credentials")

    assert response.status_code == 401


def test_credential_status_shows_configuration_without_returning_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secrets = {
        "BRIDGES_TAVILY_API_KEY": "tvly-status-secret",
        "BRIDGES_AMAP_WEB_SERVICE_KEY": "amap-route-status-secret",
        "BRIDGES_AMAP_JS_API_KEY": "amap-js-status-secret",
        "BRIDGES_AMAP_SECURITY_JS_CODE": "amap-browser-security-secret",
    }
    for name, value in secrets.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    client = TestClient(create_app())
    _register(client)

    response = client.get("/settings/credentials")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tavily"]["configured"] is True
    assert body["amap"]["web_service"]["configured"] is True
    assert body["amap"]["browser_map"]["configured"] is True
    for value in secrets.values():
        assert value not in response.text


def test_failed_tavily_replacement_keeps_the_previous_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_key = "tvly-existing-secret"
    candidate_key = "tvly-rejected-secret"
    monkeypatch.setenv("BRIDGES_TAVILY_API_KEY", old_key)
    get_settings.cache_clear()
    credentials = InMemoryCredentialStore(namespace="runtime")
    credentials.save("global-tavily-api-key", SecretStr(old_key))

    def provider_response(request: httpx.Request) -> httpx.Response:
        if request.headers.get("Authorization") == f"Bearer {candidate_key}":
            return httpx.Response(401, json={"error": f"invalid {candidate_key}"})
        return httpx.Response(200, json={"results": []})

    provider_client = httpx.Client(transport=httpx.MockTransport(provider_response))
    app = create_app(
        runtime_credential_store=credentials,
        credential_probe_http_client=provider_client,
    )
    client = TestClient(app)
    _register(client)

    response = client.put(
        "/settings/credentials/tavily",
        json={"api_key": candidate_key},
    )

    assert response.status_code == 422, response.text
    assert candidate_key not in response.text
    assert old_key not in response.text
    assert credentials.get("global-tavily-api-key") == SecretStr(old_key)

    status_response = client.get("/settings/credentials")
    assert status_response.status_code == 200, status_response.text
    assert status_response.json()["tavily"]["configured"] is True
    assert candidate_key not in status_response.text
    assert old_key not in status_response.text

    provider_client.close()


def test_successful_tavily_replacement_persists_and_updates_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_key = "tvly-verified-secret"
    monkeypatch.delenv("BRIDGES_TAVILY_API_KEY", raising=False)
    get_settings.cache_clear()
    credentials = InMemoryCredentialStore(namespace="runtime")

    def provider_response(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Authorization") == f"Bearer {candidate_key}"
        return httpx.Response(200, json={"results": []})

    provider_client = httpx.Client(transport=httpx.MockTransport(provider_response))
    app = create_app(
        runtime_credential_store=credentials,
        credential_probe_http_client=provider_client,
    )
    client = TestClient(app)
    _register(client)

    response = client.put(
        "/settings/credentials/tavily",
        json={"api_key": candidate_key},
    )

    assert response.status_code == 200, response.text
    assert response.json()["configured"] is True
    assert candidate_key not in response.text
    assert credentials.get(SETTINGS_TAVILY_CREDENTIAL_ID) == SecretStr(candidate_key)
    assert app.state.settings.tavily_api_key == SecretStr(candidate_key)
    provider_client.close()


def test_saved_tavily_credential_is_loaded_when_api_restarts() -> None:
    stored_key = "tvly-settings-restart-secret"
    credentials = InMemoryCredentialStore(namespace="runtime")
    credentials.save(SETTINGS_TAVILY_CREDENTIAL_ID, SecretStr(stored_key))

    app = create_app(runtime_credential_store=credentials)

    assert app.state.settings.tavily_api_key == SecretStr(stored_key)


def test_failed_amap_web_service_replacement_keeps_previous_credential() -> None:
    old_key = "amap-existing-secret"
    candidate_key = "amap-rejected-secret"
    credentials = InMemoryCredentialStore(namespace="runtime")
    credentials.save(AMAP_WEB_SERVICE_CREDENTIAL_ID, SecretStr(old_key))

    def amap_response(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v3/geocode/geo"
        assert request.url.params["address"] == "华东交通大学南昌校区"
        assert request.url.params["key"] == candidate_key
        return httpx.Response(200, json={"status": "0", "info": candidate_key})

    provider_client = httpx.Client(transport=httpx.MockTransport(amap_response))
    client = TestClient(
        create_app(
            runtime_credential_store=credentials,
            credential_probe_http_client=provider_client,
        )
    )
    _register(client)

    response = client.put(
        "/settings/credentials/amap/web-service",
        json={"api_key": candidate_key},
    )

    assert response.status_code == 422, response.text
    assert candidate_key not in response.text
    assert old_key not in response.text
    assert credentials.get(AMAP_WEB_SERVICE_CREDENTIAL_ID) == SecretStr(old_key)
    provider_client.close()


def test_successful_amap_web_service_replacement_stores_only_after_probe() -> None:
    candidate_key = "amap-valid-route-secret"
    credentials = InMemoryCredentialStore(namespace="runtime")

    def amap_response(request: httpx.Request) -> httpx.Response:
        assert request.url.params["key"] == candidate_key
        return httpx.Response(
            200,
            json={"status": "1", "infocode": "10000", "geocodes": []},
        )

    provider_client = httpx.Client(transport=httpx.MockTransport(amap_response))
    client = TestClient(
        create_app(
            runtime_credential_store=credentials,
            credential_probe_http_client=provider_client,
        )
    )
    _register(client)

    response = client.put(
        "/settings/credentials/amap/web-service",
        json={"api_key": candidate_key},
    )

    assert response.status_code == 200, response.text
    assert candidate_key not in response.text
    assert credentials.get(AMAP_WEB_SERVICE_CREDENTIAL_ID) == SecretStr(candidate_key)
    assert client.app.state.settings.amap_web_service_key == SecretStr(candidate_key)
    provider_client.close()


def test_unparsable_amap_response_is_not_reported_as_an_invalid_value() -> None:
    """探测没跑通（无法解析的上游响应）不冒充"值无效"，旧配对保持不变。

    旧实现把加载器正文里匹配错误串当作失败依据（实测是死代码）；新实现只按
    官方错误码表判定，拿不到可判定的结论时给 503，并保留原凭据。
    """
    old_key = "amap-js-existing-secret"
    old_code = "amap-code-existing-secret"
    candidate_key = "amap-js-unparsable-secret"
    candidate_code = "amap-code-unparsable-secret"
    credentials = InMemoryCredentialStore(namespace="runtime")
    credentials.save(
        AMAP_BROWSER_MAP_CREDENTIAL_ID,
        SecretStr(json.dumps({"api_key": old_key, "security_js_code": old_code})),
    )

    def amap_provider(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/maps":
            return httpx.Response(200, text="INVALID_USER_KEY")  # 正文串不再作判定
        return httpx.Response(200, text="<html>网关错误</html>")

    provider_client = httpx.Client(transport=httpx.MockTransport(amap_provider))
    client = TestClient(
        create_app(
            runtime_credential_store=credentials,
            credential_probe_http_client=provider_client,
        )
    )
    _register(client)

    response = client.put(
        "/settings/credentials/amap/browser-map",
        json={"api_key": candidate_key, "security_js_code": candidate_code},
    )

    assert response.status_code == 503, response.text
    detail = response.json()["detail"]
    assert detail["error"] == "credential_probe_unavailable"
    assert detail["reason"] == "amap_bad_payload"
    for secret in (old_key, old_code, candidate_key, candidate_code):
        assert secret not in response.text
    stored = credentials.get(AMAP_BROWSER_MAP_CREDENTIAL_ID)
    assert stored is not None
    assert json.loads(stored.get_secret_value()) == {
        "api_key": old_key,
        "security_js_code": old_code,
    }
    provider_client.close()


def test_browser_map_key_and_security_code_are_stored_as_one_secret_pair() -> None:
    """浏览器地图卡的真验证：加载器可达 + 成对数据服务请求通过。

    有效性只由成对请求证明（工单 01）：加载器对有效与无效 Key 返回同一份脚本，
    因此"拿到脚本"不能当"Key 有效"；而服务端追加 ``jscode`` 的地理编码请求能
    区分配对是否正确。安全码只出现在服务端发出的请求里，绝不回给浏览器。
    """
    candidate_key = "amap-js-public-secret"
    candidate_code = "amap-js-security-secret"
    credentials = InMemoryCredentialStore(namespace="runtime")
    seen: list[httpx.Request] = []

    def amap_provider(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/maps":
            assert request.url.params["key"] == candidate_key
            assert "jscode" not in request.url.params
            return httpx.Response(200, text="window.AMap = {}; /* loader ready */")
        assert request.url.path == "/v3/geocode/geo"
        assert request.url.params["key"] == candidate_key
        assert request.url.params["jscode"] == candidate_code
        return httpx.Response(
            200,
            json={
                "status": "1",
                "info": "OK",
                "infocode": "10000",
                "count": "1",
                "geocodes": [{"formatted_address": "江西省南昌市"}],
            },
        )

    provider_client = httpx.Client(transport=httpx.MockTransport(amap_provider))
    client = TestClient(
        create_app(
            runtime_credential_store=credentials,
            credential_probe_http_client=provider_client,
        )
    )
    _register(client)

    response = client.put(
        "/settings/credentials/amap/browser-map",
        json={"api_key": candidate_key, "security_js_code": candidate_code},
    )

    assert response.status_code == 200, response.text
    assert candidate_key not in response.text
    assert candidate_code not in response.text
    stored = credentials.get(AMAP_BROWSER_MAP_CREDENTIAL_ID)
    assert stored is not None
    assert json.loads(stored.get_secret_value()) == {
        "api_key": candidate_key,
        "security_js_code": candidate_code,
    }
    assert client.app.state.settings.amap_js_api_key == SecretStr(candidate_key)
    assert client.app.state.settings.amap_security_js_code == SecretStr(candidate_code)
    # 两条路径都必须走过：加载器可达性探测与成对数据服务请求。
    assert [request.url.path for request in seen] == ["/maps", "/v3/geocode/geo"]
    body = response.json()
    assert body["effective_source"] == "credential_store"
    # 成功文案只声称已被证明的事，并明确底图渲染要等真实浏览器请求确认。
    assert "真实数据服务请求" in body["message"]
    assert "浏览器" in body["message"]
    provider_client.close()


def test_browser_map_pairing_failure_reports_the_specific_reason() -> None:
    """安全码与 Key 不匹配时必须落到具体原因，而不是笼统的"验证失败"。"""
    candidate_key = "amap-js-pairing-secret"
    candidate_code = "amap-code-mismatch-secret"
    credentials = InMemoryCredentialStore(namespace="runtime")

    def amap_provider(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/maps":
            return httpx.Response(200, text="window.AMap = {}; /* loader ready */")
        return httpx.Response(
            200,
            json={
                "status": "0",
                "info": "INVALID_USER_SCODE",
                "infocode": "10008",
            },
        )

    provider_client = httpx.Client(transport=httpx.MockTransport(amap_provider))
    client = TestClient(
        create_app(
            runtime_credential_store=credentials,
            credential_probe_http_client=provider_client,
        )
    )
    _register(client)

    response = client.put(
        "/settings/credentials/amap/browser-map",
        json={"api_key": candidate_key, "security_js_code": candidate_code},
    )

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["reason"] == "amap_security_code_mismatch"
    assert "安全码" in detail["message"]
    for secret in (candidate_key, candidate_code):
        assert secret not in response.text
    assert credentials.get(AMAP_BROWSER_MAP_CREDENTIAL_ID) is None
    provider_client.close()


def test_browser_map_loader_body_size_does_not_fail_the_probe() -> None:
    """防回归（工单 01 证据 A）：正文体积不得再被当作失败依据。

    真实加载器正文实测约 968 KB；旧实现在累计超过 256 KiB 时直接判失败，
    于是有效 Key 也存不进去。这里给出同量级的正文，判定必须只看可达性。
    """
    candidate_key = "amap-js-large-loader-secret"
    candidate_code = "amap-code-large-loader-secret"
    credentials = InMemoryCredentialStore(namespace="runtime")

    def amap_provider(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/maps":
            padding = "/*".join(["x" * 1024] * 940)  # ≈ 968 KB，与实测同量级
            return httpx.Response(200, text=f"window.AMap = {{}};{padding}")
        return httpx.Response(200, json={"status": "1", "infocode": "10000"})

    provider_client = httpx.Client(transport=httpx.MockTransport(amap_provider))
    client = TestClient(
        create_app(
            runtime_credential_store=credentials,
            credential_probe_http_client=provider_client,
        )
    )
    _register(client)

    response = client.put(
        "/settings/credentials/amap/browser-map",
        json={"api_key": candidate_key, "security_js_code": candidate_code},
    )

    assert response.status_code == 200, response.text
    assert credentials.get(AMAP_BROWSER_MAP_CREDENTIAL_ID) is not None
    provider_client.close()


def test_saved_amap_credentials_are_loaded_as_runtime_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """装载优先级与报告一致：环境变量优先，凭据库兜底（工单 01）。

    旧实现只按"是否有 ``_FILE`` 引用"决定要不要让凭据库覆盖 Settings，于是
    **内联环境变量**会被凭据库静默覆盖，与 ADR-0024 写明的"文件/环境变量优先"
    不符。这里把两种情形都钉住：
    - Web 服务卡：内联环境变量存在 → 环境变量生效，凭据库里的值被遮蔽；
    - 浏览器地图卡：环境里只有半个配对（缺安全码）→ 退回凭据库里的完整配对。
    """
    for name in (
        "BRIDGES_AMAP_WEB_SERVICE_KEY",
        "BRIDGES_AMAP_JS_API_KEY",
        "BRIDGES_AMAP_SECURITY_JS_CODE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BRIDGES_AMAP_WEB_SERVICE_KEY", "amap-route-environment-old")
    monkeypatch.setenv("BRIDGES_AMAP_JS_API_KEY", "amap-js-environment-partial")
    get_settings.cache_clear()
    credentials = InMemoryCredentialStore(namespace="runtime")
    credentials.save(AMAP_WEB_SERVICE_CREDENTIAL_ID, SecretStr("amap-route-stored"))
    credentials.save(
        AMAP_BROWSER_MAP_CREDENTIAL_ID,
        SecretStr(
            json.dumps(
                {
                    "api_key": "amap-js-stored",
                    "security_js_code": "amap-code-stored",
                }
            )
        ),
    )

    app = create_app(runtime_credential_store=credentials)

    # 环境变量优先：凭据库里后保存的值不生效（这一点必须能被页面报告出来）。
    assert app.state.settings.amap_web_service_key == SecretStr(
        "amap-route-environment-old"
    )
    # 环境里的浏览器地图配对不完整（缺安全码）→ 使用凭据库里的完整配对。
    assert app.state.settings.amap_js_api_key == SecretStr("amap-js-stored")
    assert app.state.settings.amap_security_js_code == SecretStr("amap-code-stored")

    client = TestClient(app)
    _register(client)
    body = client.get("/settings/credentials").json()

    assert body["amap"]["web_service"]["effective_source"] == "environment"
    assert body["amap"]["browser_map"]["effective_source"] == "credential_store"


def test_browser_map_is_not_configured_with_a_key_but_no_security_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRIDGES_AMAP_JS_API_KEY", "amap-js-partial-secret")
    monkeypatch.delenv("BRIDGES_AMAP_SECURITY_JS_CODE", raising=False)
    get_settings.cache_clear()
    client = TestClient(create_app())
    _register(client)

    response = client.get("/settings/credentials")

    assert response.status_code == 200, response.text
    assert response.json()["amap"]["browser_map"]["configured"] is False
    assert "amap-js-partial-secret" not in response.text
