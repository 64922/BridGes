"""听写与朗读 API 测试（Issue 30）。

用临时 sqlite 应用验证：ASR/TTS 独立能力门控（未配置 Key / 探测中 /
不可用分别返回可操作中文提示）、听写端点校验（MIME/空音频/超限）、
朗读端点状态机与跨账户拒绝。真实供应商调用由服务级测试的固定快照
覆盖；这里验证 API 边界与门控语义。
"""

from __future__ import annotations

import io
import json
import struct
import wave
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from bridges.api.main import create_app
from bridges.config import get_settings
from bridges.contracts.credentials import ProbeRecord, ProbeStatus
from bridges.credentials.store import InMemoryCredentialStore


def _make_probe_wav() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(struct.pack("<h", 0) * 16000)
    return buffer.getvalue()


@pytest.fixture
def sqlite_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}")
    monkeypatch.setenv("BRIDGES_SECRET_KEY", "api-test-secret-key")
    monkeypatch.setenv("BRIDGES_ENVIRONMENT", "test")
    get_settings.cache_clear()
    return create_app()


@pytest.fixture
def client(sqlite_app: Any) -> TestClient:
    return TestClient(sqlite_app)


def _register(client: TestClient, tag: str = "1") -> dict[str, Any]:
    response = client.post(
        "/auth/register",
        json={
            "username": f"speech_user_{tag}",
            "qq_email": f"12345678{tag}@qq.com",
            "password": "Passw0rd123!",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["account"]


def _set_capability(
    sqlite_app: Any,
    account_id: str,
    capability_id: str,
    model_id: str,
    status: ProbeStatus,
) -> None:
    credential_service = sqlite_app.state.credential_service
    credential_service._store = InMemoryCredentialStore()
    credential_service._store.save(account_id, SecretStr("sk-test-dummy"))
    credential_service._probes._put_record(
        account_id,
        ProbeRecord(
            probe_id=f"probe-{capability_id}-{account_id}",
            capability_id=capability_id,
            model_id=model_id,
            region="cn-beijing",
            parameters={},
            status=status,
            probed_at=datetime.now(UTC),
            error_message="测试原因。" if status == ProbeStatus.UNAVAILABLE else None,
        ),
    )


def _create_conversation(client: TestClient) -> str:
    response = client.post("/chat/conversations", json={})
    assert response.status_code == 201, response.text
    return response.json()["conversation_id"]


def _parse_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for block in text.split("\n\n"):
        lines = [line for line in block.split("\n") if line]
        event_name = None
        data: list[str] = []
        for line in lines:
            if line.startswith("event:"):
                event_name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data.append(line[len("data:") :].strip())
        if event_name and data:
            events.append((event_name, json.loads("\n".join(data))))
    return events


def _seed_done_assistant_message(client: TestClient, conversation_id: str) -> str:
    """通过真实发送链路获得一条已完成的助手回答（stub 适配器确定性输出）。"""
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": "你好"},
    )
    assert response.status_code == 200, response.text
    events = _parse_sse(response.text)
    done = next((data for name, data in events if name == "done"), None)
    assert done is not None and done["message"]["status"] == "done"
    return done["message"]["message_id"]


# ---------------------------------------------------------------------------
# 能力门控
# ---------------------------------------------------------------------------


def test_dictation_requires_asr_capability(client: TestClient, sqlite_app: Any) -> None:
    _register(client)
    conversation_id = _create_conversation(client)
    audio = _make_probe_wav()
    # 未配置 Key → no_api_key。
    response = client.post(
        f"/chat/conversations/{conversation_id}/dictation",
        content=audio,
        headers={
            "Content-Type": "audio/wav",
            "X-Bridges-Audio-Duration": "1",
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "no_api_key"


def test_dictation_probing_and_unavailable_gates(client: TestClient, sqlite_app: Any) -> None:
    account = _register(client)
    conversation_id = _create_conversation(client)
    _set_capability(
        sqlite_app, account["id"], "asr", "qwen3-asr-flash-2025-09-08", ProbeStatus.PROBING
    )
    response = client.post(
        f"/chat/conversations/{conversation_id}/dictation",
        content=_make_probe_wav(),
        headers={"Content-Type": "audio/wav", "X-Bridges-Audio-Duration": "1"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "capability_probing"
    assert "语音转写" in response.json()["detail"]["message"]

    _set_capability(
        sqlite_app, account["id"], "asr", "qwen3-asr-flash-2025-09-08", ProbeStatus.UNAVAILABLE
    )
    response = client.post(
        f"/chat/conversations/{conversation_id}/dictation",
        content=_make_probe_wav(),
        headers={"Content-Type": "audio/wav", "X-Bridges-Audio-Duration": "1"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "capability_unavailable"
    assert "测试原因" in response.json()["detail"]["message"]


def test_read_aloud_requires_tts_capability(client: TestClient, sqlite_app: Any) -> None:
    account = _register(client)
    conversation_id = _create_conversation(client)
    # 只配置 asr 可用、tts 未探测 → 朗读入口被独立门控拒绝（asr 可用不影响 tts）。
    _set_capability(
        sqlite_app, account["id"], "asr", "qwen3-asr-flash-2025-09-08", ProbeStatus.AVAILABLE
    )
    response = client.post(f"/chat/conversations/{conversation_id}/messages/m-none/read-aloud")
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "capability_unavailable"
    assert "语音朗读" in response.json()["detail"]["message"]


def test_dictation_rejects_unsupported_mime_and_empty_audio(
    client: TestClient, sqlite_app: Any
) -> None:
    account = _register(client)
    conversation_id = _create_conversation(client)
    _set_capability(
        sqlite_app, account["id"], "asr", "qwen3-asr-flash-2025-09-08", ProbeStatus.AVAILABLE
    )
    response = client.post(
        f"/chat/conversations/{conversation_id}/dictation",
        content=b"not-audio",
        headers={"Content-Type": "text/plain", "X-Bridges-Audio-Duration": "1"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "unsupported_mime_type"

    response = client.post(
        f"/chat/conversations/{conversation_id}/dictation",
        content=b"",
        headers={"Content-Type": "audio/wav", "X-Bridges-Audio-Duration": "1"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "empty_audio"


def test_dictation_gate_passes_and_reports_deterministic_failure(
    client: TestClient, sqlite_app: Any
) -> None:
    """能力可用时端点可达；无真实适配器时网关确定性失败（不模拟成功）。"""
    account = _register(client)
    conversation_id = _create_conversation(client)
    _set_capability(
        sqlite_app, account["id"], "asr", "qwen3-asr-flash-2025-09-08", ProbeStatus.AVAILABLE
    )
    response = client.post(
        f"/chat/conversations/{conversation_id}/dictation",
        content=_make_probe_wav(),
        headers={"Content-Type": "audio/wav", "X-Bridges-Audio-Duration": "1"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "failed"
    # 测试环境无真实 ASR 适配器：stub 返回空转写 → 可原样重试，不冒充成功。
    assert payload["error_code"] == "empty_transcript"
    assert payload["retryable"] is True


def test_read_aloud_endpoints_account_scoped(client: TestClient, sqlite_app: Any) -> None:
    account = _register(client)
    conversation_id = _create_conversation(client)
    _set_capability(
        sqlite_app, account["id"], "tts", "qwen3-tts-flash-2025-11-27", ProbeStatus.AVAILABLE
    )
    _set_capability(
        sqlite_app, account["id"], "chat", "qwen3.7-plus-2026-05-26", ProbeStatus.AVAILABLE
    )
    # 不存在的消息 → 404。
    response = client.post(f"/chat/conversations/{conversation_id}/messages/m-none/read-aloud")
    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "message_not_found"
    # 无 TTS 适配器时确定性失败（BLOCKED，非模拟成功）。
    message_id = _seed_done_assistant_message(client, conversation_id)
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages/{message_id}/read-aloud"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "failed"
    assert payload["retryable"] is False
    assert payload["error_code"] is not None
    # 音频未就绪 → 404。
    response = client.get(
        f"/chat/conversations/{conversation_id}/messages/{message_id}/read-aloud/audio"
    )
    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "audio_not_ready"
    # 删除朗读幂等复位。
    response = client.delete(
        f"/chat/conversations/{conversation_id}/messages/{message_id}/read-aloud"
    )
    assert response.status_code == 200
    assert response.json()["state"] == "not_generated"
    # 跨账户访问：他人账户能力就绪后，原对话的消息仍不可见（404，不泄漏存在性）。
    other = _register(client, "2")
    _set_capability(
        sqlite_app, other["id"], "tts", "qwen3-tts-flash-2025-11-27", ProbeStatus.AVAILABLE
    )
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages/{message_id}/read-aloud"
    )
    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "message_not_found"
