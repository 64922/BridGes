"""V2 Issue 09 契约测试：主模型验证与原子激活（``/settings/models``）。

覆盖验收标准（``.scratch/bridges-v2/issues/09-qwen-model-configuration.md``）：
- 精确元数据核对文本/图片/工具调用/结构化输出与上下文额度，再以真实调用
  探测；任一不通过都不保存；
- 界面可显示实际模型 ID、能力、上下文长度与具体失败原因；没有预设列表；
- 验证通过的配置原子激活（重启后仍生效），失败保留原配置；
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
from bridges.credentials.store import InMemoryCredentialStore
from bridges.persistence import SqliteStateStore

_CANDIDATE_MODEL_ID = "qwen-probe-candidate"
_QWEN_KEY = "sk-qwen-settings-secret"


def _metadata_payload(
    model_id: str = _CANDIDATE_MODEL_ID,
    *,
    capabilities: tuple[str, ...] = ("TG", "VU"),
    modalities: tuple[str, ...] = ("Text", "Image"),
    features: tuple[str, ...] = ("function-calling", "structured-outputs"),
    context_window: int | None = 131_072,
    max_input_tokens: int | None = 130_048,
) -> dict[str, Any]:
    model_info: dict[str, Any] = {}
    if context_window is not None:
        model_info["context_window"] = context_window
    if max_input_tokens is not None:
        model_info["max_input_tokens"] = max_input_tokens
    entry: dict[str, Any] = {
        "model": model_id,
        "inference_metadata": {"request_modality": list(modalities)},
    }
    if capabilities is not None:
        entry["capabilities"] = list(capabilities)
    if features is not None:
        entry["features"] = list(features)
    entry["model_info"] = model_info
    return {"success": True, "output": {"total": 1, "models": [entry]}}


def _provider_client(
    *,
    metadata: dict[str, Any] | None = None,
    metadata_status: int = 200,
    probe_failures: tuple[str, ...] = (),
    recorded_models: list[str] | None = None,
) -> httpx.Client:
    """百炼替身：``/api/v1/models`` 给元数据，``/chat/completions`` 给探测结果。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/v1/models"):
            if metadata_status != 200:
                return httpx.Response(metadata_status, json={"message": "denied"})
            return httpx.Response(200, json=metadata or _metadata_payload())
        body = json.loads(request.content.decode("utf-8"))
        if recorded_models is not None:
            recorded_models.append(str(body.get("model")))
        if body.get("response_format") == {"type": "json_object"}:
            content = "not-json" if "structured_output" in probe_failures else '{"status":"ok"}'
            return _chat_response(content)
        if body.get("tools"):
            message: dict[str, Any] = {"role": "assistant", "content": ""}
            if "tool_calling" not in probe_failures:
                message["tool_calls"] = [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "record_ping", "arguments": '{"value":"ok"}'},
                    }
                ]
            return _chat_response("", message)
        if isinstance(body["messages"][0].get("content"), list):
            return _chat_response("" if "image" in probe_failures else "白色")
        return _chat_response("" if "text" in probe_failures else "ok")

    return httpx.Client(transport=httpx.MockTransport(handler))


def _chat_response(content: str, message: dict[str, Any] | None = None) -> httpx.Response:
    payload = message if message is not None else {"role": "assistant", "content": content}
    return httpx.Response(
        200,
        json={
            "model": _CANDIDATE_MODEL_ID,
            "choices": [{"index": 0, "message": payload, "finish_reason": "stop"}],
        },
    )


def _register(client: TestClient, tag: str = "1") -> None:
    response = client.post(
        "/auth/register",
        json={
            "username": f"ModelSettingsUser{tag}",
            "qq_email": f"4455{tag}6@qq.com",
            "password": "correct-horse-25",
        },
    )
    assert response.status_code == 201, response.text


def _build_app(
    monkeypatch: pytest.MonkeyPatch,
    *,
    qwen_key: str | None = _QWEN_KEY,
    provider_client: httpx.Client | None = None,
    state_store: SqliteStateStore | None = None,
) -> Any:
    if qwen_key is None:
        monkeypatch.delenv("BRIDGES_QWEN_API_KEY", raising=False)
    else:
        monkeypatch.setenv("BRIDGES_QWEN_API_KEY", qwen_key)
    get_settings.cache_clear()
    return create_app(
        state_store,
        runtime_credential_store=InMemoryCredentialStore(namespace="runtime"),
        credential_probe_http_client=provider_client or _provider_client(),
    )


def _candidate(body: dict[str, Any]) -> dict[str, Any]:
    return body["detail"]


@pytest.fixture
def memory_state() -> SqliteStateStore:
    store = SqliteStateStore(":memory:")
    yield store
    store.close()


def test_model_settings_require_an_authenticated_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TestClient(_build_app(monkeypatch))

    assert client.get("/settings/models").status_code == 401
    assert client.put("/settings/models", json={"model_id": "x"}).status_code == 401


def test_factory_snapshot_reports_capabilities_and_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TestClient(_build_app(monkeypatch))
    _register(client)

    response = client.get("/settings/models")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["model_id"] == CHAT_MODEL_ID
    assert body["source"] == "factory"
    assert body["revision"] == 0
    assert body["capabilities"] == {
        "text": True,
        "image": True,
        "tool_calling": True,
        "structured_output": True,
    }
    assert body["context_window"]
    assert body["credential_configured"] is True
    assert body["last_validation"] is None
    assert response.headers["cache-control"] == "no-store"
    assert _QWEN_KEY not in response.text


def test_unconfigured_credential_explains_the_operation_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TestClient(_build_app(monkeypatch, qwen_key=None))
    _register(client)

    status = client.get("/settings/models").json()
    assert status["credential_configured"] is False

    response = client.put("/settings/models", json={"model_id": _CANDIDATE_MODEL_ID})

    assert response.status_code == 422, response.text
    detail = _candidate(response.json())
    assert detail["error"] == "credential_not_configured"
    assert "Qwen 凭据" in detail["message"] and "主模型 ID" in detail["message"]
    assert client.get("/settings/models").json()["model_id"] == CHAT_MODEL_ID


def test_empty_model_id_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(_build_app(monkeypatch))
    _register(client)

    response = client.put("/settings/models", json={"model_id": "   "})

    assert response.status_code == 422
    assert _candidate(response.json())["error"] == "model_id_invalid"


def test_unknown_model_id_keeps_the_previous_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _provider_client(metadata={"success": True, "output": {"models": []}})
    client = TestClient(_build_app(monkeypatch, provider_client=provider))
    _register(client)

    response = client.put("/settings/models", json={"model_id": _CANDIDATE_MODEL_ID})

    assert response.status_code == 422, response.text
    detail = _candidate(response.json())
    assert detail["error"] == "model_not_found"
    assert _CANDIDATE_MODEL_ID in detail["message"]
    assert client.get("/settings/models").json()["model_id"] == CHAT_MODEL_ID
    assert client.get("/settings/models").json()["last_validation"]["passed"] is False
    provider.close()


def test_incomplete_metadata_is_reported_and_not_saved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _provider_client(metadata=_metadata_payload(features=None))
    client = TestClient(_build_app(monkeypatch, provider_client=provider))
    _register(client)

    response = client.put("/settings/models", json={"model_id": _CANDIDATE_MODEL_ID})

    assert response.status_code == 422, response.text
    detail = _candidate(response.json())
    assert detail["error"] == "model_metadata_incomplete"
    assert "无法核对" in detail["message"]
    assert client.get("/settings/models").json()["model_id"] == CHAT_MODEL_ID
    provider.close()


def test_capability_gap_in_metadata_blocks_saving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _provider_client(
        metadata=_metadata_payload(capabilities=("TG",), modalities=("Text",))
    )
    client = TestClient(_build_app(monkeypatch, provider_client=provider))
    _register(client)

    response = client.put("/settings/models", json={"model_id": _CANDIDATE_MODEL_ID})

    assert response.status_code == 422, response.text
    detail = _candidate(response.json())
    assert detail["error"] == "model_capability_missing"
    assert "图片" in detail["message"]
    checks = {(check["capability"], check["source"], check["ok"]) for check in detail["checks"]}
    assert ("image", "metadata", False) in checks
    assert ("text", "metadata", True) in checks
    assert client.get("/settings/models").json()["model_id"] == CHAT_MODEL_ID
    provider.close()


def test_probe_failure_lists_capability_reasons_and_blocks_saving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _provider_client(probe_failures=("image", "tool_calling"))
    client = TestClient(_build_app(monkeypatch, provider_client=provider))
    _register(client)

    response = client.put("/settings/models", json={"model_id": _CANDIDATE_MODEL_ID})

    assert response.status_code == 422, response.text
    detail = _candidate(response.json())
    assert detail["error"] == "model_probe_failed"
    assert "图片" in detail["message"] and "工具调用" in detail["message"]
    probes = {
        check["capability"]: check
        for check in detail["checks"]
        if check["source"] == "probe"
    }
    assert probes["image"]["ok"] is False
    assert "图片探测失败" in probes["image"]["message"]
    assert probes["tool_calling"]["ok"] is False
    assert probes["text"]["ok"] is True
    assert probes["structured_output"]["ok"] is True
    assert client.get("/settings/models").json()["model_id"] == CHAT_MODEL_ID
    provider.close()


def test_verified_model_activates_atomically_and_survives_restart(
    monkeypatch: pytest.MonkeyPatch, memory_state: SqliteStateStore
) -> None:
    recorded: list[str] = []
    provider = _provider_client(recorded_models=recorded)
    app = _build_app(monkeypatch, provider_client=provider, state_store=memory_state)
    client = TestClient(app)
    _register(client)

    response = client.put("/settings/models", json={"model_id": _CANDIDATE_MODEL_ID})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["model_id"] == _CANDIDATE_MODEL_ID
    assert body["source"] == "settings"
    assert body["revision"] == 1
    assert body["context_window"] == 131_072
    assert body["max_input_tokens"] == 130_048
    assert body["validated_at"] is not None
    assert body["capabilities"]["tool_calling"] is True
    assert body["last_validation"]["passed"] is True
    assert _QWEN_KEY not in response.text
    # 探测确实发生在候选模型上（真实调用而非仅元数据）。
    assert recorded and set(recorded) == {_CANDIDATE_MODEL_ID}

    # 同进程读方（后台执行器）立即看到激活结果。
    assert app.state.run_model_config_provider.snapshot().model_id == _CANDIDATE_MODEL_ID

    # 重启（同一数据库）后仍是已激活配置。
    restarted = create_app(
        memory_state,
        runtime_credential_store=InMemoryCredentialStore(namespace="runtime"),
        credential_probe_http_client=_provider_client(),
    )
    restart_client = TestClient(restarted)
    _register(restart_client, tag="2")
    assert restart_client.get("/settings/models").json()["model_id"] == _CANDIDATE_MODEL_ID
    provider.close()


def test_failed_replacement_keeps_the_previously_activated_model(
    monkeypatch: pytest.MonkeyPatch, memory_state: SqliteStateStore
) -> None:
    provider = _provider_client()
    app = _build_app(monkeypatch, provider_client=provider, state_store=memory_state)
    client = TestClient(app)
    _register(client)
    assert (
        client.put("/settings/models", json={"model_id": _CANDIDATE_MODEL_ID}).status_code
        == 200
    )

    rejecting = _provider_client(metadata_status=401)
    app.state.credential_probe_http_client = rejecting
    response = client.put("/settings/models", json={"model_id": "qwen-other-candidate"})

    assert response.status_code == 422, response.text
    assert _candidate(response.json())["error"] == "model_metadata_credential_invalid"
    current = client.get("/settings/models").json()
    assert current["model_id"] == _CANDIDATE_MODEL_ID
    assert current["revision"] == 1
    provider.close()
    rejecting.close()


def test_metadata_and_probe_request_bodies_carry_no_key_material(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/api/v1/models"):
            return httpx.Response(200, json=_metadata_payload())
        return _chat_response("ok")

    provider = httpx.Client(transport=httpx.MockTransport(handler))
    client = TestClient(_build_app(monkeypatch, provider_client=provider))
    _register(client)
    client.put("/settings/models", json={"model_id": _CANDIDATE_MODEL_ID})

    assert seen, "验证必须真实发起请求"
    for request in seen:
        assert request.headers.get("Authorization") == f"Bearer {_QWEN_KEY}"
        assert _QWEN_KEY not in request.content.decode("utf-8")
    provider.close()


def test_credential_status_reports_qwen_without_returning_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TestClient(_build_app(monkeypatch))
    _register(client)

    body = client.get("/settings/credentials").json()

    assert body["qwen"]["configured"] is True
    assert SecretStr(_QWEN_KEY).get_secret_value() not in json.dumps(body)
    assert "qwen" in body
