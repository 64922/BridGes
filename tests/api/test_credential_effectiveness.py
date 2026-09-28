"""「保存即生效」与"只声称已被证明的事"的契约测试（工单 01 任务 3／5）。

三组证据：

1. **后台执行器（worker）按运行重读凭据**：设置页保存的新密钥让 worker 在不重启
   进程的前提下，用**新值**构造下一次真实调用所用的客户端——若只在启动时解析
   一次，就会"提示已保存，后台任务却继续拿旧密钥失败"；
2. **地图代理回写结论**：底图渲染与平台／白名单限制只能在浏览器的真实请求路径
   上被证实或证伪，代理把该结论回写到设置页卡片状态；
3. **换密钥 + 换主模型可在同一操作内完成**：旧密钥已失效、新密钥只覆盖另一批
   模型时，两张卡不再互相指向。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from bridges.ai.fixed_models import CHAT_MODEL_ID
from bridges.api.main import create_app
from bridges.config import get_settings
from bridges.credentials.ids import (
    AMAP_BROWSER_MAP_CREDENTIAL_ID,
    GLOBAL_QWEN_CREDENTIAL_ID,
)
from bridges.credentials.store import (
    EncryptedVolumeCredentialStore,
    InMemoryCredentialStore,
)
from bridges.runtime.executor import BackgroundExecutor

_OLD_KEY = "sk-worker-old-secret"
_NEW_KEY = "sk-worker-new-secret"
_CANDIDATE_MODEL_ID = "qwen-migration-candidate"
_REVOKED_KEY = "sk-qwen-revoked-secret"
_FRESH_KEY = "sk-qwen-fresh-secret"


def _register(client: TestClient) -> None:
    response = client.post(
        "/auth/register",
        json={
            "username": "EffectivenessUser",
            "qq_email": "556677@qq.com",
            "password": "correct-horse-29",
        },
    )
    assert response.status_code == 201, response.text


# --- 1. worker 侧：保存即生效（无需重启进程） ------------------------------


def _worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> BackgroundExecutor:
    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "worker-credential-test-secret")
    # 这里要证明的是"跨进程共享凭据卷被重读"，因此用真实文件载体（加密凭据卷，
    # 落在本用例自己的临时数据目录里）而不是 test 环境的内存替身——test 环境按
    # 设计不读写任何凭据存储（``build_credential_store``）。
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "desktop")
    monkeypatch.setenv("BRIDGES_CREDENTIAL_BACKEND", "encrypted-volume")
    get_settings.cache_clear()
    return BackgroundExecutor(get_settings())


def test_worker_uses_a_credential_saved_after_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BRIDGES_QWEN_API_KEY", raising=False)
    executor = _worker(tmp_path, monkeypatch)
    store = EncryptedVolumeCredentialStore(tmp_path, namespace="runtime")
    store.save(GLOBAL_QWEN_CREDENTIAL_ID, SecretStr(_OLD_KEY))
    # 启动快照里是旧密钥：这正是"设置页保存后 worker 用不到新值"的来源。
    assert executor.refresh_credentials() is True
    assert executor._effective_settings().qwen_api_key == SecretStr(_OLD_KEY)  # noqa: SLF001

    # 设置页（另一个进程）把新密钥写进共享真相源。
    store.save(GLOBAL_QWEN_CREDENTIAL_ID, SecretStr(_NEW_KEY))

    assert executor.refresh_credentials() is True
    assert executor._effective_settings().qwen_api_key == SecretStr(_NEW_KEY)  # noqa: SLF001

    # 下一轮真实调用所用的客户端用新密钥构造（而不是启动快照里的旧值）。
    captured: list[str] = []

    class _RecordingClient:
        def __init__(self, *, api_key: SecretStr, **_: Any) -> None:
            captured.append(api_key.get_secret_value())

    with patch("bridges.runtime.executor.QwenApiClient", _RecordingClient):
        assert executor._ensure_image_service() is not None  # noqa: SLF001

    assert captured == [_NEW_KEY]
    # 值未变化时不重复重建（避免每轮都丢弃已建好的服务）。
    assert executor.refresh_credentials() is False


def test_worker_wakes_up_from_a_credential_gate_without_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """启动时缺凭据而待机的服务，在凭据出现后必须自行恢复。"""
    monkeypatch.delenv("BRIDGES_QWEN_API_KEY", raising=False)
    executor = _worker(tmp_path, monkeypatch)
    assert executor._effective_settings().qwen_api_key is None  # noqa: SLF001

    store = EncryptedVolumeCredentialStore(tmp_path, namespace="runtime")
    store.save(GLOBAL_QWEN_CREDENTIAL_ID, SecretStr(_NEW_KEY))

    assert executor.refresh_credentials() is True
    assert executor._effective_settings().qwen_api_key == SecretStr(_NEW_KEY)  # noqa: SLF001


# --- 2. 地图代理：把真实浏览器请求路径的结论回写到卡片 ----------------------


def _map_app(store: InMemoryCredentialStore, provider: httpx.Client) -> TestClient:
    return TestClient(
        create_app(
            runtime_credential_store=store,
            credential_probe_http_client=provider,
        )
    )


def _browser_map_store() -> InMemoryCredentialStore:
    store = InMemoryCredentialStore(namespace="runtime")
    store.save(
        AMAP_BROWSER_MAP_CREDENTIAL_ID,
        SecretStr(
            json.dumps(
                {
                    "api_key": "amap-js-secret",
                    "security_js_code": "amap-code-secret",
                }
            )
        ),
    )
    return store


def test_map_proxy_writes_a_real_request_conclusion_back_to_the_card() -> None:
    """平台不符这类限制只能在浏览器加载路径上观察：代理请求后写回卡片。"""

    def upstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"status": "0", "info": "USERKEY_PLAT_NOMATCH", "infocode": "10009"},
        )

    provider = httpx.Client(transport=httpx.MockTransport(upstream))
    client = _map_app(_browser_map_store(), provider)
    _register(client)

    proxy_response = client.get(
        "/commute/amap-proxy/v3/geocode/geo", params={"address": "南昌"}
    )
    assert proxy_response.status_code == 200, proxy_response.text

    card_response = client.get("/settings/credentials")
    card = card_response.json()["amap"]["browser_map"]
    assert card["error"] is not None
    assert "平台" in card["error"]
    assert card["runtime_evidence"] is not None
    assert "真实地图数据请求" in card["runtime_evidence"]
    # 诊断写中文原因、不回显上游 info，也不含任何凭据正文；上游正文只作为代理
    # 透传返回给调用方（浏览器需要它）。
    assert "USERKEY_PLAT_NOMATCH" not in card_response.text
    assert "amap-js-secret" not in card_response.text
    assert "amap-code-secret" not in card_response.text
    assert json.loads(proxy_response.text)["infocode"] == "10009"
    provider.close()


def test_map_proxy_clears_a_previous_failure_after_a_successful_request() -> None:
    responses = [
        httpx.Response(
            200, json={"status": "0", "info": "INVALID_USER_SCODE", "infocode": "10008"}
        ),
        httpx.Response(200, json={"status": "1", "infocode": "10000", "geocodes": []}),
    ]
    provider = httpx.Client(
        transport=httpx.MockTransport(lambda request: responses.pop(0))
    )
    client = _map_app(_browser_map_store(), provider)
    _register(client)

    client.get("/commute/amap-proxy/v3/geocode/geo")
    failed = client.get("/settings/credentials").json()["amap"]["browser_map"]
    assert failed["error"]
    validated_at = failed["last_validated_at"]

    client.get("/commute/amap-proxy/v3/geocode/geo")
    card = client.get("/settings/credentials").json()["amap"]["browser_map"]
    assert card["error"] is None
    assert "已成功" in card["runtime_evidence"]
    assert "服务端追加安全码" in card["runtime_evidence"]
    # 运行路径结论不冒充"设置页验证"：最近验证时间不变。
    assert card["last_validated_at"] == validated_at
    provider.close()


def test_map_proxy_ignores_non_envelope_bodies() -> None:
    """底图瓦片等非 JSON 内容不是可判定的结论：既不写成功也不写失败。"""
    provider = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"\x89PNG\r\n\x1a\n")
        )
    )
    client = _map_app(_browser_map_store(), provider)
    _register(client)

    assert client.get("/commute/amap-proxy/v3/geocode/geo").status_code == 200

    card = client.get("/settings/credentials").json()["amap"]["browser_map"]
    assert card["error"] is None
    assert card["runtime_evidence"] is None
    provider.close()


# --- 3. 解密钥 ↔ 主模型的死锁 --------------------------------------------


def _bailian_metadata() -> dict[str, Any]:
    """候选模型元数据（能力与 test_model_settings 的有效样本同形）。"""
    return {
        "success": True,
        "output": {
            "total": 1,
            "models": [
                {
                    "model": _CANDIDATE_MODEL_ID,
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


def _chat_response(
    content: str, message: dict[str, Any] | None = None
) -> httpx.Response:
    payload = (
        message if message is not None else {"role": "assistant", "content": content}
    )
    return httpx.Response(
        200,
        json={
            "model": _CANDIDATE_MODEL_ID,
            "choices": [{"index": 0, "message": payload, "finish_reason": "stop"}],
        },
    )


def _bailian_handler(accepted_keys: set[str]) -> Any:
    """百炼替身：只接受给定密钥；模型列表里只有候选模型（看不到旧主模型）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        header = request.headers.get("Authorization") or ""
        if header.removeprefix("Bearer ") not in accepted_keys:
            return httpx.Response(401, json={"message": "denied"})
        if request.url.path.endswith("/api/v1/models"):
            return httpx.Response(200, json=_bailian_metadata())
        body = json.loads(request.content.decode("utf-8"))
        if body.get("response_format") == {"type": "json_object"}:
            return _chat_response('{"status":"ok"}')
        if body.get("tools"):
            return _chat_response(
                "",
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "record_ping",
                                "arguments": '{"value":"ok"}',
                            },
                        }
                    ],
                },
            )
        if isinstance(body["messages"][0].get("content"), list):
            return _chat_response("白色")
        return _chat_response("ok")

    return handler


def test_key_and_model_can_be_replaced_in_one_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BRIDGES_QWEN_API_KEY", raising=False)
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "migration-test-secret-key")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    store = InMemoryCredentialStore(namespace="runtime")
    store.save(GLOBAL_QWEN_CREDENTIAL_ID, SecretStr(_REVOKED_KEY))
    provider = httpx.Client(
        transport=httpx.MockTransport(_bailian_handler({_REVOKED_KEY, _FRESH_KEY}))
    )
    app = create_app(
        runtime_credential_store=store,
        credential_probe_http_client=provider,
    )
    client = TestClient(app)
    _register(client)
    assert app.state.settings.qwen_api_key == SecretStr(_REVOKED_KEY)

    # 只换密钥：新旧两把密钥都看不到当前主模型 → 死锁的字面症状。此时必须给出
    # 指向「Qwen 主模型」卡的同一次迁移操作，而不是"先去凭据卡换密钥"。
    credential_card = client.put(
        "/settings/credentials/qwen", json={"api_key": _FRESH_KEY}
    )
    assert credential_card.status_code == 422, credential_card.text
    detail = credential_card.json()["detail"]
    assert detail["reason"] == "model_not_matching_key"
    assert "同时填入" in detail["message"]
    assert "换密钥 + 换主模型" in detail["message"]
    assert store.get(GLOBAL_QWEN_CREDENTIAL_ID) == SecretStr(_REVOKED_KEY)

    # 同一操作里同时提交候选密钥与候选模型：两者一起验证、一起生效。
    response = client.put(
        "/settings/models",
        json={"model_id": _CANDIDATE_MODEL_ID, "api_key": _FRESH_KEY},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["model_id"] == _CANDIDATE_MODEL_ID
    assert body["source"] == "settings"
    assert body["credential_configured"] is True
    assert "同时更换" in body["last_validation"]["message"]
    assert store.get(GLOBAL_QWEN_CREDENTIAL_ID) == SecretStr(_FRESH_KEY)
    assert app.state.settings.qwen_api_key == SecretStr(_FRESH_KEY)
    assert app.state.qwen_client._api_key == SecretStr(_FRESH_KEY)  # noqa: SLF001
    assert _FRESH_KEY not in response.text
    provider.close()


def test_model_card_saves_but_discloses_a_shadowing_environment_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """环境变量优先：密钥存下了但不会生效，文案必须说明而不是谎称已更换。"""
    monkeypatch.setenv("BRIDGES_QWEN_API_KEY", _REVOKED_KEY)
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    store = InMemoryCredentialStore(namespace="runtime")
    provider = httpx.Client(
        transport=httpx.MockTransport(_bailian_handler({_REVOKED_KEY, _FRESH_KEY}))
    )
    app = create_app(
        runtime_credential_store=store,
        credential_probe_http_client=provider,
    )
    client = TestClient(app)
    _register(client)

    response = client.put(
        "/settings/models",
        json={"model_id": _CANDIDATE_MODEL_ID, "api_key": _FRESH_KEY},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["model_id"] == _CANDIDATE_MODEL_ID
    message = body["last_validation"]["message"]
    assert "同时更换" not in message
    assert "BRIDGES_QWEN_API_KEY" in message
    assert "不会生效" in message
    # 真正生效的仍是环境变量提供的旧密钥（重启后也如此）。
    assert app.state.settings.qwen_api_key == SecretStr(_REVOKED_KEY)
    assert store.get(GLOBAL_QWEN_CREDENTIAL_ID) == SecretStr(_FRESH_KEY)
    provider.close()


def test_model_card_without_any_key_offers_both_migration_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BRIDGES_QWEN_API_KEY", raising=False)
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    client = TestClient(create_app(runtime_credential_store=InMemoryCredentialStore()))
    _register(client)

    response = client.put("/settings/models", json={"model_id": CHAT_MODEL_ID})

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["error"] == "credential_not_configured"
    # 指引要同时给出"本卡一起填"与"先去凭据卡"两条路径，缺一即回到死锁。
    assert "本卡" in detail["message"]
    assert "Qwen 凭据" in detail["message"]
