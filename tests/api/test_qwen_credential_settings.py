"""V2 Issue 09 契约测试：设置页更换全局 Qwen 凭据（``/settings/credentials/qwen``）。

覆盖验收标准：
- 候选密钥先经最小只读探测（精确元数据查询 + 一次真实调用），验证失败不覆盖
  旧凭据；
- 密钥看不到当前主模型时给出操作顺序提示（先换密钥或先改模型 ID）；
- 成功替换后就地轮换运行期密钥，写入与交互式首启同一凭据库项（下次启动生效）；
- 响应、日志与错误信息不泄漏明文密钥。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from bridges.ai.fixed_models import CHAT_MODEL_ID
from bridges.api.main import create_app
from bridges.config import get_settings
from bridges.credentials.ids import GLOBAL_QWEN_CREDENTIAL_ID
from bridges.credentials.store import InMemoryCredentialStore

_OLD_KEY = "sk-qwen-old-secret"
_NEW_KEY = "sk-qwen-new-secret"


def _metadata_payload(model_id: str | None = CHAT_MODEL_ID) -> dict[str, Any]:
    models = []
    if model_id is not None:
        models.append(
            {
                "model": model_id,
                "capabilities": ["TG", "VU"],
                "features": ["function-calling", "structured-outputs"],
                "inference_metadata": {"request_modality": ["Text", "Image"]},
                "model_info": {"context_window": 131_072, "max_input_tokens": 130_048},
            }
        )
    return {"success": True, "output": {"total": len(models), "models": models}}


def _provider_client(
    *, candidate_key: str, metadata_model: str | None = CHAT_MODEL_ID
) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("Authorization") != f"Bearer {candidate_key}":
            return httpx.Response(401, json={"message": f"invalid {candidate_key}"})
        if request.url.path.endswith("/api/v1/models"):
            return httpx.Response(200, json=_metadata_payload(metadata_model))
        return httpx.Response(
            200,
            json={
                "model": CHAT_MODEL_ID,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}],
            },
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


def _register(client: TestClient) -> None:
    response = client.post(
        "/auth/register",
        json={
            "username": "QwenCredentialUser",
            "qq_email": "778899@qq.com",
            "password": "correct-horse-25",
        },
    )
    assert response.status_code == 201, response.text


def _app(monkeypatch: pytest.MonkeyPatch, provider: httpx.Client) -> Any:
    monkeypatch.setenv("BRIDGES_QWEN_API_KEY", _OLD_KEY)
    get_settings.cache_clear()
    credentials = InMemoryCredentialStore(namespace="runtime")
    credentials.save(GLOBAL_QWEN_CREDENTIAL_ID, SecretStr(_OLD_KEY))
    return create_app(
        runtime_credential_store=credentials,
        credential_probe_http_client=provider,
    )


def test_successful_replacement_persists_and_rotates_the_runtime_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _provider_client(candidate_key=_NEW_KEY)
    app = _app(monkeypatch, provider)
    client = TestClient(app)
    _register(client)
    old_client_key = app.state.qwen_client._api_key.get_secret_value()  # noqa: SLF001

    response = client.put("/settings/credentials/qwen", json={"api_key": _NEW_KEY})

    assert response.status_code == 200, response.text
    assert response.json()["configured"] is True
    assert response.json()["last_validated_at"] is not None
    assert _NEW_KEY not in response.text
    assert old_client_key == _OLD_KEY
    assert (
        app.state.runtime_credential_store.get(GLOBAL_QWEN_CREDENTIAL_ID)
        == SecretStr(_NEW_KEY)
    )
    assert app.state.settings.qwen_api_key == SecretStr(_NEW_KEY)
    assert app.state.qwen_client._api_key == SecretStr(_NEW_KEY)  # noqa: SLF001
    assert client.get("/settings/credentials").json()["qwen"]["configured"] is True
    provider.close()


def test_rejected_candidate_keeps_the_previous_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _provider_client(candidate_key=_NEW_KEY)
    app = _app(monkeypatch, provider)
    client = TestClient(app)
    _register(client)

    response = client.put("/settings/credentials/qwen", json={"api_key": "sk-rejected"})

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["error"] == "credential_invalid"
    assert detail["reason"] == "key_rejected"
    assert "sk-rejected" not in response.text
    assert (
        app.state.runtime_credential_store.get(GLOBAL_QWEN_CREDENTIAL_ID)
        == SecretStr(_OLD_KEY)
    )
    assert app.state.settings.qwen_api_key == SecretStr(_OLD_KEY)
    status = client.get("/settings/credentials").json()["qwen"]
    assert status["configured"] is True
    assert status["error"] is not None
    provider.close()


def test_key_without_the_active_model_explains_the_operation_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _provider_client(candidate_key=_NEW_KEY, metadata_model=None)
    app = _app(monkeypatch, provider)
    client = TestClient(app)
    _register(client)

    response = client.put("/settings/credentials/qwen", json={"api_key": _NEW_KEY})

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["reason"] == "model_not_matching_key"
    assert CHAT_MODEL_ID in detail["message"]
    assert "主模型 ID" in detail["message"]
    assert (
        app.state.runtime_credential_store.get(GLOBAL_QWEN_CREDENTIAL_ID)
        == SecretStr(_OLD_KEY)
    )
    provider.close()


def test_empty_candidate_is_rejected_without_touching_the_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _provider_client(candidate_key=_NEW_KEY)
    app = _app(monkeypatch, provider)
    client = TestClient(app)
    _register(client)

    response = client.put("/settings/credentials/qwen", json={"api_key": "   "})

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "credential_invalid"
    assert (
        app.state.runtime_credential_store.get(GLOBAL_QWEN_CREDENTIAL_ID)
        == SecretStr(_OLD_KEY)
    )
    provider.close()


def test_replacement_is_rejected_without_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _provider_client(candidate_key=_NEW_KEY)
    app = _app(monkeypatch, provider)
    client = TestClient(app)

    response = client.put("/settings/credentials/qwen", json={"api_key": _NEW_KEY})

    assert response.status_code == 401
    provider.close()


def test_probe_request_bodies_never_contain_the_candidate_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """密钥只出现在鉴权头；请求正文、响应与错误信息都不带明文。"""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/api/v1/models"):
            return httpx.Response(200, json=_metadata_payload())
        return httpx.Response(
            200,
            json={
                "model": CHAT_MODEL_ID,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}],
            },
        )

    provider = httpx.Client(transport=httpx.MockTransport(handler))
    app = _app(monkeypatch, provider)
    client = TestClient(app)
    _register(client)

    response = client.put("/settings/credentials/qwen", json={"api_key": _NEW_KEY})

    assert seen
    for request in seen:
        assert request.headers.get("Authorization") == f"Bearer {_NEW_KEY}"
        assert _NEW_KEY not in request.content.decode("utf-8")
    assert response.status_code == 200
    assert _NEW_KEY not in response.text
    assert _NEW_KEY not in client.get("/settings/credentials").text
    assert _NEW_KEY not in client.get("/settings/models").text
    provider.close()
