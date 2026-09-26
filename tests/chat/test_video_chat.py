"""文生视频聊天集成测试（Issue 32，GQ-04 迁移；Issue 21 退役写入口）。

Issue 21 起视频生成是退役能力：聊天载荷与任务/资产写入口全部稳定返回
410，本文件只保留仍然成立的「不暗中执行」合同——未选模块的自然语言
视频请求不会启动视频任务，人味化载荷仍优先 410。退役契约（写入口 410
＋历史只读面）由 ``tests/retirement/test_legacy_generation_exit.py`` 固定。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.api.auth import SESSION_COOKIE_NAME
from bridges.ai import CapabilityRegistry, ModelGateway
from bridges.ai.adapters import AdapterResult
from bridges.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    CapabilityStatus,
    RetryPolicy,
)

VIDEO_MODEL = "wan2.7-t2v-2026-06-12"
_VIDEO_BYTES = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
_RESULT_URL = "http://video.local/result.mp4"


def _chat_capability() -> CapabilityRecord:
    return CapabilityRecord(
        name="qwen_text_chat",
        version="1",
        kind=CapabilityKind.MODEL,
        vendor="qwen",
        region="cn-beijing",
        model_id="qwen3.7-plus-2026-05-26",
        input_schema_version="chat-messages-v1",
        output_schema_version="chat-completion-v1",
        status=CapabilityStatus.VERIFIED,
        retry_policy=RetryPolicy(max_attempts=3, backoff_seconds=0.01),
    )


def _wan_capability() -> CapabilityRecord:
    return CapabilityRecord(
        name="qwen_wan",
        version="1",
        kind=CapabilityKind.MODEL,
        vendor="wan",
        region="cn-beijing",
        model_id=VIDEO_MODEL,
        input_schema_version="video-prompt-v1",
        output_schema_version="video-task-v1",
        status=CapabilityStatus.VERIFIED,
        retry_policy=RetryPolicy(max_attempts=1, backoff_seconds=0.01),
    )


class _ProgrammableWanAdapter:
    """可编程 Wan 适配器：按脚本推进 submit/poll/fetch/cancel。"""

    def __init__(self, script: list[dict[str, Any]] | None = None) -> None:
        self.script = list(script or [])
        self.calls: list[dict[str, Any]] = []

    def call(
        self,
        capability: CapabilityRecord,
        run_context: Any,
        payload: dict[str, Any],
    ) -> AdapterResult:
        self.calls.append(payload)
        step = self.script.pop(0) if self.script else {"output": {}}
        if step.get("error") is not None:
            raise step["error"]
        return AdapterResult(
            actual_model_id=VIDEO_MODEL,
            output=dict(step.get("output") or {}),
        )


def _wan_gateway() -> ModelGateway:
    registry = CapabilityRegistry()
    registry.register(_wan_capability())
    gateway = ModelGateway(registry)
    adapter = _ProgrammableWanAdapter(
        [
            {"output": {"cloud_task_id": "cloud-1"}},
            {"output": {"cloud_status": "SUCCEEDED", "result_url": _RESULT_URL}},
            {"output": {"video_bytes": _VIDEO_BYTES, "media_type": "video/mp4"}},
        ]
    )
    gateway.register_adapter(
        "qwen_wan",
        "1",
        adapter,
    )
    gateway.video_adapter = adapter  # type: ignore[attr-defined]
    return gateway


@pytest.fixture
def sqlite_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """构建挂载 sqlite bridges.db 的应用（真实聊天服务 + 视频服务）。"""
    from bridges.api.main import create_app
    from bridges.config import get_settings

    monkeypatch.setenv(
        "BRIDGES_DATABASE_URL", f"sqlite:///{tmp_path / 'bridges.db'}"
    )
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
            "username": f"video_user_{tag}",
            "qq_email": f"12345679{tag}@qq.com",
            "password": "Passw0rd123!",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["account"]


def _swap_wan_gateway(sqlite_app: Any) -> None:
    gateway = _wan_gateway()
    sqlite_app.state.video_service._gateway = gateway
    return gateway.video_adapter


def _create_conversation(client: TestClient) -> str:
    session_token = client.cookies.get(SESSION_COOKIE_NAME)
    assert session_token is not None
    subject = client.app.state.identity_service.resolve_session(session_token).subject
    conversation = client.app.state.chat_service.create_conversation(subject.account_id)
    return conversation.conversation_id


def _parse_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for block in text.split("\n\n"):
        lines = [line for line in block.split("\n") if line]
        event_name = None
        data: list[str] = []
        for line in lines:
            if line.startswith("event:"):
                event_name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data.append(line[len("data:"):].strip())
        if event_name and data:
            events.append((event_name, json.loads("\n".join(data))))
    return events


def _video_payload(prompt: str = "一条静谧的河") -> dict[str, Any]:
    return {"prompt": prompt}


def test_unselected_natural_language_video_request_stays_ordinary(
    sqlite_app: Any, client: TestClient
) -> None:
    _register(client, tag="2")
    adapter = _swap_wan_gateway(sqlite_app)
    conversation_id = _create_conversation(client)

    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={
            "content": "生成一段 10 秒的竖屏短视频：雨后的街道，镜头慢慢推进到一盏路灯。"
        },
    )
    assert response.status_code == 200, response.text
    created = response.json()
    route = created["assistant_message"]["route"]
    assert route["main_capability"] == "ordinary_chat"
    assert created["assistant_message"]["video"] is None

    sqlite_app.state.generation_executor.run_tick()
    messages = client.get(f"/chat/conversations/{conversation_id}").json()["messages"]
    assistant = next(message for message in messages if message["role"] == "assistant")
    assert assistant["video"] is None
    assert assistant["route"] == route
    assert assistant["status"] == "done"
    assert not [call for call in adapter.calls if call.get("kind") == "submit"]


def test_video_payload_conflicts_with_skill(sqlite_app: Any, client: TestClient) -> None:
    _register(client)
    conversation_id = _create_conversation(client)
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={
            "content": "文章人味化：改写",
            "video": _video_payload(),
            "skill_id": "bridges-humanizer",
            "skill_input": {
                "skill_id": "bridges-humanizer",
                "contract": {
                    "path": "rewrite",
                    "genre": "popular_science",
                    "source_text": "光合作用是把光能转化为化学能的过程。",
                    "audience": "普通读者",
                    "channel": "公众号",
                    "length_target": "200 字",
                },
            },
        },
    )
    # V2 issue 04：人味化入口退役——410 优先于载荷互斥校验。
    assert response.status_code == 410
    assert response.json()["detail"]["error"] == "humanizer_capability_retired"


