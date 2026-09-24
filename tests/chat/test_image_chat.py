"""图片生成与编辑聊天集成测试（Issue 31，GQ-04 迁移）。

图片载荷走真实消息流程：send 携带 image → SSE started → image(queued)
→ done（消息投影携带任务快照）；后台执行器处理轮完成后消息投影收敛
为 succeeded（正文更新为完成摘要）；SKILL 与图片载荷互斥拒绝；图片
消息不走消息级重试（任务卡内重试）。GQ-04 起全新账户无任何 Key/探测
记录即可提交图片任务——测试不再注入逐账户假 Key/探测快照，由全局
网关注入的可编程适配器驱动。
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

IMAGE_MODEL = "qwen-image-2.0-pro-2026-06-22"
_IMAGE_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
_RESULT_URL = "http://img.local/result.png"


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


def _image_capability() -> CapabilityRecord:
    return CapabilityRecord(
        name="qwen_image",
        version="1",
        kind=CapabilityKind.MODEL,
        vendor="qwen",
        region="cn-beijing",
        model_id=IMAGE_MODEL,
        input_schema_version="image-prompt-v1",
        output_schema_version="image-task-v1",
        status=CapabilityStatus.VERIFIED,
        retry_policy=RetryPolicy(max_attempts=1, backoff_seconds=0.01),
    )


class _ProgrammableImageAdapter:
    """可编程图片适配器：按脚本推进 submit/poll/fetch。"""

    def __init__(self, script: list[dict[str, Any]] | None = None) -> None:
        self.script = list(script or [])

    def call(
        self,
        capability: CapabilityRecord,
        run_context: Any,
        payload: dict[str, Any],
    ) -> AdapterResult:
        step = self.script.pop(0) if self.script else {"output": {}}
        if step.get("error") is not None:
            raise step["error"]
        return AdapterResult(
            actual_model_id=IMAGE_MODEL,
            output=dict(step.get("output") or {}),
        )


def _image_gateway() -> ModelGateway:
    registry = CapabilityRegistry()
    registry.register(_image_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter(
        "qwen_image",
        "1",
        _ProgrammableImageAdapter(
            [
                {"output": {"cloud_task_id": "cloud-1"}},
                {"output": {"cloud_status": "SUCCEEDED", "result_url": _RESULT_URL}},
                {"output": {"image_bytes": _IMAGE_BYTES, "media_type": "image/png"}},
            ]
        ),
    )
    return gateway


@pytest.fixture
def sqlite_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """构建挂载 sqlite bridges.db 的应用（真实聊天服务 + 图片服务）。"""
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
            "username": f"image_user_{tag}",
            "qq_email": f"12345678{tag}@qq.com",
            "password": "Passw0rd123!",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["account"]


def _swap_image_gateway(sqlite_app: Any) -> None:
    gateway = _image_gateway()
    sqlite_app.state.image_service._gateway = gateway


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


def _image_payload(prompt: str = "一座桥的素描") -> dict[str, Any]:
    return {"kind": "generate", "prompt": prompt}


def test_image_message_flows_through_real_stream_and_worker(
    sqlite_app: Any, client: TestClient
) -> None:
    """全新账户（无 Key、无探测记录）直接提交图片任务并走通全链路（GQ-04）。

    测试环境无真实供应商：图片适配器由全局网关注入可编程替身；账户侧
    不写任何假 Key/探测快照，证明入口不再被账户凭据门禁拦截。
    """
    _register(client)
    _swap_image_gateway(sqlite_app)
    conversation_id = _create_conversation(client)

    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": "生成一张桥的素描", "image": _image_payload()},
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
    assert "image" in names
    assert "done" in names
    image_event = next(data for name, data in events if name == "image")
    assert image_event["task"]["status"] == "queued"
    assert image_event["task"]["kind"] == "generate"
    done_event = next(data for name, data in events if name == "done")
    message = done_event["message"]
    assert message["image"]["status"] == "queued"
    assert message["content"] == "已提交图片生成请求，正在处理…"

    # 后台执行器处理轮：完成后消息投影收敛为 succeeded。
    sqlite_app.state.image_service.process_pending()
    sqlite_app.state.image_service.process_pending()
    sqlite_app.state.image_service.process_pending()

    messages = client.get(
        f"/chat/conversations/{conversation_id}"
    ).json()["messages"]
    assistant = next(m for m in messages if m["role"] == "assistant")
    assert assistant["image"]["status"] == "succeeded"
    assert assistant["image"]["model_id"] == IMAGE_MODEL
    assert assistant["image"]["asset_id"] is not None
    assert assistant["content"] == "图片生成完成。"

    # 资产可查询：版本链 + 自动替代文本。
    asset_id = assistant["image"]["asset_id"]
    asset_response = client.get(
        f"/chat/conversations/{conversation_id}/image-assets/{asset_id}"
    )
    assert asset_response.status_code == 200
    asset = asset_response.json()
    assert asset["version_count"] == 1
    # 测试 gateway 无视觉能力：替代文本确定性降级为提示词摘要。
    assert asset["alt_text_source"] == "fallback"
    assert "一座桥的素描" in asset["alt_text"]

    # 版本图片字节：私有缓存头 + 内容一致。
    version_id = asset["current_version_id"]
    image_response = client.get(
        f"/chat/conversations/{conversation_id}/image-assets/{asset_id}"
        f"/versions/{version_id}/image"
    )
    assert image_response.status_code == 200
    assert image_response.content == _IMAGE_BYTES
    assert "no-store" in image_response.headers["cache-control"]
    # 下载参数附加附件头。
    download_response = client.get(
        f"/chat/conversations/{conversation_id}/image-assets/{asset_id}"
        f"/versions/{version_id}/image?download=1"
    )
    assert "attachment" in download_response.headers["content-disposition"]

    # 替代文本修改。
    alt_response = client.put(
        f"/chat/conversations/{conversation_id}/image-assets/{asset_id}/alt-text",
        json={"alt_text": "深蓝色背景的跨江大桥素描"},
    )
    assert alt_response.status_code == 200
    assert alt_response.json()["alt_text_source"] == "manual"

    # 取消已成功任务：幂等返回当前投影。
    task_id = assistant["image"]["task_id"]
    cancel_response = client.post(
        f"/chat/conversations/{conversation_id}/image-tasks/{task_id}/cancel"
    )
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "succeeded"

    # 删除资产：影响说明 + 消息引用标记 + 已删除查询 404。
    delete_response = client.delete(
        f"/chat/conversations/{conversation_id}/image-assets/{asset_id}"
    )
    assert delete_response.status_code == 200
    deletion = delete_response.json()
    assert deletion["removed_versions"] == 1
    assert deletion["updated_messages"] == 1
    gone = client.get(
        f"/chat/conversations/{conversation_id}/image-assets/{asset_id}"
    )
    assert gone.status_code == 404
    refreshed = client.get(
        f"/chat/conversations/{conversation_id}"
    ).json()["messages"]
    assistant_after = next(m for m in refreshed if m["role"] == "assistant")
    assert assistant_after["image"]["deleted"] is True


def test_unselected_natural_language_image_request_stays_ordinary(
    sqlite_app: Any, client: TestClient
) -> None:
    """图片请求正文不会在未选择模块时启动图片任务。"""
    _register(client, "2")
    _swap_image_gateway(sqlite_app)
    conversation_id = _create_conversation(client)

    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": "生成一张小猫的图"},
    )
    assert response.status_code == 200, response.text
    created = response.json()
    user = created["user_message"]
    assert user["route"]["main_capability"] == "ordinary_chat"
    assert created["assistant_message"]["active_run"] is not None

    sqlite_app.state.generation_executor.run_tick()
    with client.stream(
        "GET",
        f"/chat/conversations/{conversation_id}/messages/"
        f"{created['assistant_message']['message_id']}/events",
        params={"cursor": 0},
    ) as stream:
        events = _parse_sse("\n".join(stream.iter_lines()))
    names = [name for name, _ in events]
    assert "done" in names
    assert "image" not in names
    messages = client.get(f"/chat/conversations/{conversation_id}").json()["messages"]
    assistant = next(message for message in messages if message["role"] == "assistant")
    assert assistant["status"] == "done"
    assert assistant["image"] is None


def test_natural_language_ambiguous_edit_asks_once_without_model_call(
    sqlite_app: Any, client: TestClient
) -> None:
    _register(client, "3")
    _swap_image_gateway(sqlite_app)
    conversation_id = _create_conversation(client)

    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": "把这张图的背景换成实验室"},
    )
    assert response.status_code == 200, response.text
    created = response.json()
    assert created["user_message"]["route"]["main_capability"] == "ordinary_chat"

    sqlite_app.state.generation_executor.run_tick()
    messages = client.get(
        f"/chat/conversations/{conversation_id}"
    ).json()["messages"]
    assistant = next(message for message in messages if message["role"] == "assistant")
    assert assistant["status"] == "done"
    assert assistant["image"] is None


def test_image_payload_conflicts_with_skill(sqlite_app: Any, client: TestClient) -> None:
    _register(client)
    conversation_id = _create_conversation(client)
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={
            "content": "文章人味化：改写",
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
            "image": _image_payload(),
        },
    )
    # V2 issue 04：人味化入口退役——410 优先于载荷互斥校验。
    assert response.status_code == 410
    assert response.json()["detail"]["error"] == "humanizer_capability_retired"


def test_image_edit_without_source_rejected(sqlite_app: Any, client: TestClient) -> None:
    _register(client)
    conversation_id = _create_conversation(client)
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={
            "content": "把背景改为夜空",
            "image": {"kind": "edit", "prompt": "把背景改为夜空"},
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "invalid_image_request"


def test_image_message_retry_rejected_via_card(sqlite_app: Any, client: TestClient) -> None:
    _register(client)
    _swap_image_gateway(sqlite_app)
    conversation_id = _create_conversation(client)
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": "生成一张桥的素描", "image": _image_payload()},
    )
    assert response.status_code == 200
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
    message_id = done_event["message"]["message_id"]

    retry_response = client.post(
        f"/chat/conversations/{conversation_id}/messages/{message_id}/retry"
    )
    assert retry_response.status_code == 409
    assert retry_response.json()["detail"]["error"] == "image_task_retry_via_card"


def test_task_endpoints_are_account_scoped(
    sqlite_app: Any, client: TestClient
) -> None:
    _register(client)
    _swap_image_gateway(sqlite_app)
    conversation_id = _create_conversation(client)
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": "生成一张桥的素描", "image": _image_payload()},
    )
    assert response.status_code == 200
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
    image_event = next(data for name, data in events if name == "image")
    task_id = image_event["task"]["task_id"]

    # 第二个账户无法读取/取消/重试第一个账户的任务。
    _register(client, tag="2")
    other_conversation = _create_conversation(client)
    for method, path in (
        ("get", f"/chat/conversations/{other_conversation}/image-tasks/{task_id}"),
        (
            "post",
            f"/chat/conversations/{other_conversation}/image-tasks/{task_id}/cancel",
        ),
        (
            "post",
            f"/chat/conversations/{other_conversation}/image-tasks/{task_id}/retry",
        ),
    ):
        scoped = getattr(client, method)
        assert scoped(path).status_code == 404
