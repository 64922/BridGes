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


def test_failed_amap_browser_probe_keeps_the_previous_pair() -> None:
    old_key = "amap-js-existing-secret"
    old_code = "amap-code-existing-secret"
    candidate_key = "amap-js-rejected-secret"
    candidate_code = "amap-code-rejected-secret"
    credentials = InMemoryCredentialStore(namespace="runtime")
    credentials.save(
        AMAP_BROWSER_MAP_CREDENTIAL_ID,
        SecretStr(json.dumps({"api_key": old_key, "security_js_code": old_code})),
    )

    def invalid_loader(request: httpx.Request) -> httpx.Response:
        assert request.url.params["key"] == candidate_key
        return httpx.Response(200, text="INVALID_USER_KEY")

    provider_client = httpx.Client(transport=httpx.MockTransport(invalid_loader))
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
    candidate_key = "amap-js-public-secret"
    candidate_code = "amap-js-security-secret"
    credentials = InMemoryCredentialStore(namespace="runtime")

    def amap_loader(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/maps"
        assert request.url.params["key"] == candidate_key
        assert "jscode" not in request.url.params
        return httpx.Response(200, text="window.AMap = {}; /* loader ready */")

    provider_client = httpx.Client(transport=httpx.MockTransport(amap_loader))
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
    provider_client.close()


def test_saved_amap_credentials_are_loaded_as_runtime_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    assert app.state.settings.amap_web_service_key == SecretStr("amap-route-stored")
    assert app.state.settings.amap_js_api_key == SecretStr("amap-js-stored")
    assert app.state.settings.amap_security_js_code == SecretStr("amap-code-stored")


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
