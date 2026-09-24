"""文生视频聊天集成测试（Issue 32，GQ-04 迁移）。

video 载荷走真实消息流程：send 携带 video → SSE started → video(queued)
→ done（消息投影携带任务快照）；后台执行器处理轮完成后消息投影收敛为
succeeded（正文更新为完成摘要）；SKILL 与视频载荷互斥拒绝；视频消息不
走消息级重试（任务卡内重试）；任务/资产/字节端点跨账户 404。GQ-04 起
全新账户无任何 Key/探测记录即可提交视频任务——测试不再注入逐账户假
Key/探测快照，由全局网关注入的可编程适配器驱动。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

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
                event_name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data.append(line[len("data:"):].strip())
        if event_name and data:
            events.append((event_name, json.loads("\n".join(data))))
    return events


def _video_payload(prompt: str = "一条静谧的河") -> dict[str, Any]:
    return {"prompt": prompt}


def test_video_message_flows_through_real_stream_and_worker(
    sqlite_app: Any, client: TestClient
) -> None:
    """全新账户（无 Key、无探测记录）直接提交视频任务并走通全链路（GQ-04）。

    测试环境无真实供应商：视频适配器由全局网关注入可编程替身；账户侧
    不写任何假 Key/探测快照，证明入口不再被账户凭据门禁拦截。
    """
    _register(client)
    _swap_wan_gateway(sqlite_app)
    conversation_id = _create_conversation(client)

    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": "生成一条河的视频", "video": _video_payload()},
    )
    assert response.status_code == 200, response.text
    created = response.json()
    sqlite_app.state.generation_executor.run_tick()
    events: list[tuple[str, dict[str, Any]]] = []
    with client.stream(
        "GET",
        f"/chat/conversations/{conversation_id}/messages/"
        f"{created['assistant_message']['message_id']}/events",
        params={"cursor": 0},
    ) as stream:
        events.extend(_parse_sse("\n".join(stream.iter_lines())))
    names = [name for name, _ in events]
    assert "started" in names
    assert "video" in names
    assert "done" in names
    video_event = next(data for name, data in events if name == "video")
    assert video_event["task"]["status"] == "queued"
    assert video_event["task"]["prompt"] == "一条静谧的河"
    done_event = next(data for name, data in events if name == "done")
    message = done_event["message"]
    assert message["video"]["status"] == "queued"
    assert message["content"] == "已提交视频生成请求，正在处理…"

    # 后台执行器处理轮：提交 → 轮询完成 → 下载，消息投影收敛为 succeeded。
    sqlite_app.state.video_service.process_pending()
    sqlite_app.state.video_service.process_pending()
    sqlite_app.state.video_service.process_pending()

    messages = client.get(
        f"/chat/conversations/{conversation_id}"
    ).json()["messages"]
    assistant = next(m for m in messages if m["role"] == "assistant")
    assert assistant["video"]["status"] == "succeeded"
    assert assistant["video"]["model_id"] == VIDEO_MODEL
    assert assistant["video"]["synthetic_media"] is True
    assert assistant["video"]["asset_id"] is not None
    assert assistant["content"] == "视频生成完成。"

    # 资产可查询：提示/模型/供应商任务标识/说明文字。
    asset_id = assistant["video"]["asset_id"]
    asset_response = client.get(
        f"/chat/conversations/{conversation_id}/video-assets/{asset_id}"
    )
    assert asset_response.status_code == 200
    asset = asset_response.json()
    assert asset["model_id"] == VIDEO_MODEL
    assert asset["synthetic_media"] is True
    assert asset["cloud_task_id"] == "cloud-1"
    assert asset["description_source"] == "prompt"
    assert "一条静谧的河" in asset["description"]

    # 视频字节：私有缓存头 + 内容一致；下载参数附加附件头。
    video_response = client.get(
        f"/chat/conversations/{conversation_id}/video-assets/{asset_id}/video"
    )
    assert video_response.status_code == 200
    assert video_response.content == _VIDEO_BYTES
    assert "no-store" in video_response.headers["cache-control"]
    download_response = client.get(
        f"/chat/conversations/{conversation_id}/video-assets/{asset_id}/video?download=1"
    )
    assert "attachment" in download_response.headers["content-disposition"]

    # 说明文字修改。
    description_response = client.put(
        f"/chat/conversations/{conversation_id}/video-assets/{asset_id}/description",
        json={"description": "晨雾中的河流，画面缓缓推进。"},
    )
    assert description_response.status_code == 200
    assert description_response.json()["description_source"] == "manual"

    # 删除：带影响说明 + 消息投影 deleted 标记；幂等返回零计数。
    delete_response = client.delete(
        f"/chat/conversations/{conversation_id}/video-assets/{asset_id}"
    )
    assert delete_response.status_code == 200
    deletion = delete_response.json()
    assert deletion["removed_objects"] == 1
    assert deletion["updated_messages"] == 1
    again = client.delete(
        f"/chat/conversations/{conversation_id}/video-assets/{asset_id}"
    )
    assert again.json()["removed_objects"] == 0


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
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "conflicting_payload"


def test_video_message_retry_rejected_via_card(sqlite_app: Any, client: TestClient) -> None:
    _register(client)
    _swap_wan_gateway(sqlite_app)
    conversation_id = _create_conversation(client)
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": "生成一条河的视频", "video": _video_payload()},
    )
    assert response.status_code == 200, response.text
    created = response.json()
    sqlite_app.state.generation_executor.run_tick()
    events: list[tuple[str, dict[str, Any]]] = []
    with client.stream(
        "GET",
        f"/chat/conversations/{conversation_id}/messages/"
        f"{created['assistant_message']['message_id']}/events",
        params={"cursor": 0},
    ) as stream:
        events.extend(_parse_sse("\n".join(stream.iter_lines())))
    done_event = next(data for name, data in events if name == "done")
    assistant_message_id = done_event["message"]["message_id"]

    retry_response = client.post(
        f"/chat/conversations/{conversation_id}/messages/{assistant_message_id}/retry"
    )
    assert retry_response.status_code == 409
    assert retry_response.json()["detail"]["error"] == "video_task_retry_via_card"


def test_task_endpoints_are_account_scoped(sqlite_app: Any, client: TestClient) -> None:
    _register(client)
    _swap_wan_gateway(sqlite_app)
    conversation_id = _create_conversation(client)
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": "生成一条河的视频", "video": _video_payload()},
    )
    created = response.json()
    sqlite_app.state.generation_executor.run_tick()
    events: list[tuple[str, dict[str, Any]]] = []
    with client.stream(
        "GET",
        f"/chat/conversations/{conversation_id}/messages/"
        f"{created['assistant_message']['message_id']}/events",
        params={"cursor": 0},
    ) as stream:
        events.extend(_parse_sse("\n".join(stream.iter_lines())))
    video_event = next(data for name, data in events if name == "video")
    task_id = video_event["task"]["task_id"]

    # 完成生成后拿到资产标识（注册第二账户前，会话仍属账户 1）。
    sqlite_app.state.video_service.process_pending()
    sqlite_app.state.video_service.process_pending()
    sqlite_app.state.video_service.process_pending()
    messages = client.get(
        f"/chat/conversations/{conversation_id}"
    ).json()["messages"]
    assistant = next(m for m in messages if m["role"] == "assistant")
    asset_id = assistant["video"]["asset_id"]
    assert asset_id is not None

    _register(client, tag="2")
    other_conversation = _create_conversation(client)
    for method, path in (
        ("get", f"/chat/conversations/{other_conversation}/video-tasks/{task_id}"),
        ("post", f"/chat/conversations/{other_conversation}/video-tasks/{task_id}/cancel"),
        ("post", f"/chat/conversations/{other_conversation}/video-tasks/{task_id}/retry"),
    ):
        assert getattr(client, method)(path).status_code == 404

    # 资产/说明/字节/下载端点同样跨账户 404（不泄漏存在性）。
    for method, path in (
        ("get", f"/chat/conversations/{other_conversation}/video-assets/{asset_id}"),
        (
            "put",
            f"/chat/conversations/{other_conversation}/video-assets/{asset_id}/description",
        ),
        ("get", f"/chat/conversations/{other_conversation}/video-assets/{asset_id}/video"),
        (
            "get",
            f"/chat/conversations/{other_conversation}/video-assets/{asset_id}/video?download=1",
        ),
        ("delete", f"/chat/conversations/{other_conversation}/video-assets/{asset_id}"),
    ):
        if method == "put":
            response = client.put(path, json={"description": "x"})
        else:
            response = getattr(client, method)(path)
        assert response.status_code == 404, path
