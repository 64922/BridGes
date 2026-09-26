"""听写 API 测试（Issue 30，GQ-03 迁移；Issue 21 退役朗读）。

用临时 sqlite 应用验证：新账户无任何账户 Key/探测记录即可提交听写
（GQ-03：入口由全局运行凭据驱动，不再返回 no_api_key 或
capability_probing 门禁错误）、听写端点校验（MIME/空音频/超限）。
真实供应商调用由服务级测试的固定快照覆盖；这里验证 API 边界与 GQ-03
后语义。回答朗读已按 ADR-0030 退役，其 410 契约与历史只读面由
``tests/retirement/test_legacy_generation_exit.py`` 固定。
"""

from __future__ import annotations

import io
import json
import struct
import wave
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.api.auth import SESSION_COOKIE_NAME
from bridges.api.main import create_app
from bridges.config import get_settings


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


def _create_conversation(client: TestClient) -> str:
    """建会话走聊天服务：首轮原子创建后空会话创建入口恒为 409。"""
    session_token = client.cookies.get(SESSION_COOKIE_NAME)
    assert session_token is not None
    subject = client.app.state.identity_service.resolve_session(session_token).subject
    return client.app.state.chat_service.create_conversation(
        subject.account_id
    ).conversation_id


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


def _seed_done_assistant_message(
    sqlite_app: Any, client: TestClient, conversation_id: str
) -> str:
    """通过真实发送链路获得一条已完成的助手回答（stub 适配器确定性输出）。

    Issue 02：发送创建运行后由后台执行器领取执行（test 环境同步驱动），
    订阅持久化事件拿到 done 终态。
    """
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": "你好"},
    )
    assert response.status_code == 200, response.text
    created = response.json()
    sqlite_app.state.generation_executor.run_tick()
    with client.stream(
        "GET",
        f"/chat/conversations/{conversation_id}/messages/"
        f"{created['assistant_message']['message_id']}/events",
        params={"cursor": 0},
    ) as stream:
        events = _parse_sse("\n".join(stream.iter_lines()))
    done = next((data for name, data in events if name == "done"), None)
    assert done is not None and done["message"]["status"] == "done"
    return done["message"]["message_id"]


# ---------------------------------------------------------------------------
# GQ-03：无账户凭据门禁
# ---------------------------------------------------------------------------


def test_new_account_dictation_without_any_key_or_probe(
    client: TestClient, sqlite_app: Any
) -> None:
    """全新账户（无 Key、无探测记录）可直接提交听写，不再被预检拦截。

    测试环境无真实 ASR 适配器：请求进入全局网关确定性失败（空转写），
    但绝不再返回 no_api_key / capability_probing。
    """
    _register(client)
    conversation_id = _create_conversation(client)
    response = client.post(
        f"/chat/conversations/{conversation_id}/dictation",
        content=_make_probe_wav(),
        headers={"Content-Type": "audio/wav", "X-Bridges-Audio-Duration": "1"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "failed"
    assert payload["error_code"] == "empty_transcript"
    assert payload["retryable"] is True
    assert payload["error_code"] not in {"no_api_key", "capability_probing"}


def test_dictation_rejects_unsupported_mime_and_empty_audio(
    client: TestClient, sqlite_app: Any
) -> None:
    _register(client)
    conversation_id = _create_conversation(client)
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


def test_dictation_reports_deterministic_failure(
    client: TestClient, sqlite_app: Any
) -> None:
    """无真实适配器时网关确定性失败（不模拟成功），新账户直接可达。"""
    _register(client)
    conversation_id = _create_conversation(client)
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

