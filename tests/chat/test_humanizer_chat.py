"""文章人味化写路径退出测试（V2 issue 04）。

专用文章人味化的入口、继续执行与重试写路径全部关闭：

1. 发送/首轮携带 ``bridges-humanizer`` 载荷 → 410，不创建任何消息。
2. 历史人味化轮次的助手消息重试 → 410，不创建新尝试。
3. 历史遗留的 queued/中断人味化运行被领取时确定性收敛为退役错误，
   绝不调用模型继续二次全文改写。
4. 历史人味化输入与结果保持只读：消息投影继续携带结果卡数据，
   账户导出继续包含 ``messages.skill`` 结果正文。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bridges.ai import ModelGateway
from bridges.ai.adapters import StreamChunk
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.contracts.ai import CapabilityKind, CapabilityRecord
from bridges.contracts.chat import ChatMessageRole, ChatMessageStatus
from bridges.contracts.expression import Genre
from bridges.contracts.humanizer import (
    HumanizerOutputContract,
    HumanizerPath,
    HumanizerResultProjection,
    HumanizerResultStatus,
    HumanizerTaskContract,
)

_SKILL_PAYLOAD = {
    "skill_id": "bridges-humanizer",
    "contract": {
        "path": "rewrite",
        "genre": "popular_science",
        "source_text": "光合作用是植物把光能转化为化学能的过程。",
        "audience": "普通读者",
        "channel": "公众号",
        "length_target": "800 字",
    },
}


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
    )


class _CountingStreamAdapter:
    """计数流式适配器：断言退役收敛路径不产生任何模型调用。"""

    def __init__(self) -> None:
        self.calls = 0

    def call(self, capability: CapabilityRecord, run_context: Any, payload: dict[str, Any]) -> Any:
        self.calls += 1
        from bridges.ai.adapters import AdapterResult

        return AdapterResult(actual_model_id=capability.model_id, output={"content": ""})

    def stream_call(
        self,
        capability: CapabilityRecord,
        run_context: Any,
        payload: dict[str, Any],
    ):
        self.calls += 1
        yield StreamChunk(kind="delta", delta="不应被调用")


@pytest.fixture
def sqlite_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """构建挂载 sqlite bridges.db 的应用（真实聊天服务）。"""
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


def _register(client: TestClient, tag: str = "1") -> str:
    response = client.post(
        "/auth/register",
        json={
            "username": f"humanizer_user_{tag}",
            "qq_email": f"12345670{tag}@qq.com",
            "password": "Passw0rd123!",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["account"]["id"]


def _create_conversation(client: TestClient) -> str:
    """issue 01：会话由首条普通消息原子创建。"""
    response = client.post(
        "/chat/first-turn",
        json={"content": "先聊聊天气", "idempotency_key": "key-humanizer-exit"},
    )
    assert response.status_code == 201, response.text
    return response.json()["conversation"]["conversation_id"]


def _seed_legacy_turn(
    sqlite_app: Any,
    account_id: str,
    conversation_id: str,
    *,
    assistant_status: ChatMessageStatus,
    assistant_skill: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """直写存储层构造「历史遗留」的人味化轮次（退役前的旧数据）。"""
    from bridges.chat.repository import MessageRecord

    repo = sqlite_app.state.chat_service._repo
    now = datetime.now(UTC)
    user_message = MessageRecord(
        message_id="legacy-user-1",
        conversation_id=conversation_id,
        account_id=account_id,
        role=ChatMessageRole.USER,
        attempt_number=1,
        status=ChatMessageStatus.DONE,
        content="文章人味化：改写光合作用科普段落",
        thinking=None,
        error_code=None,
        error_message=None,
        duration_ms=None,
        model_id=None,
        run_lock_id=None,
        created_at=now,
        updated_at=now,
        skill=dict(_SKILL_PAYLOAD),
    )
    assistant_message = MessageRecord(
        message_id="legacy-assistant-1",
        conversation_id=conversation_id,
        account_id=account_id,
        role=ChatMessageRole.ASSISTANT,
        attempt_number=1,
        status=assistant_status,
        content="（旧任务已产出的历史草稿正文）",
        thinking=None,
        error_code=None,
        error_message=None,
        duration_ms=None,
        model_id=None,
        run_lock_id=None,
        created_at=now,
        updated_at=now,
        skill=assistant_skill,
    )
    repo.insert_message(user_message)
    repo.insert_message(assistant_message)
    return user_message.message_id, assistant_message.message_id


def _legacy_result_projection() -> HumanizerResultProjection:
    return HumanizerResultProjection(
        task_id="legacy-task-1",
        skill_id="bridges-humanizer",
        skill_version="1.0.0",
        path=HumanizerPath.REWRITE,
        genre=Genre.POPULAR_SCIENCE,
        contract=HumanizerTaskContract(
            path=HumanizerPath.REWRITE,
            genre=Genre.POPULAR_SCIENCE,
            source_text="光合作用是植物把光能转化为化学能的过程。",
        ),
        status=HumanizerResultStatus.DONE,
        output=HumanizerOutputContract(
            final_text="（历史结果：植物通过光合作用把光能储存为化学能。）",
            edits=[],
            fact_check=[],
            open_questions=[],
        ),
    )


# ---------------------------------------------------------------------------
# 入口退出（AC2）
# ---------------------------------------------------------------------------


def test_send_with_humanizer_payload_is_retired(
    client: TestClient, sqlite_app: Any
) -> None:
    _register(client)
    conversation_id = _create_conversation(client)
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={
            "content": "文章人味化：改写光合作用科普段落",
            "skill_id": _SKILL_PAYLOAD["skill_id"],
            "skill_input": _SKILL_PAYLOAD,
        },
    )
    assert response.status_code == 410, response.text
    body = response.json()["detail"]
    assert body["error"] == "humanizer_capability_retired"
    assert "已退役" in body["message"]
    # 拒绝发生在任何写入之前：会话消息数不变（仍只有首轮两条）。
    detail = client.get(f"/chat/conversations/{conversation_id}").json()
    assert len(detail["messages"]) == 2
    assert all(message["skill"] is None for message in detail["messages"])


def test_first_turn_with_humanizer_payload_is_retired(
    client: TestClient, sqlite_app: Any
) -> None:
    _register(client, "2")
    response = client.post(
        "/chat/first-turn",
        json={
            "content": "文章人味化：改写光合作用科普段落",
            "idempotency_key": "key-first-turn-skill",
            "skill_id": _SKILL_PAYLOAD["skill_id"],
            "skill_input": _SKILL_PAYLOAD,
        },
    )
    assert response.status_code == 410, response.text
    assert (
        response.json()["detail"]["error"] == "humanizer_capability_retired"
    )


def test_other_skill_payload_keeps_user_extensions_retirement(
    client: TestClient, sqlite_app: Any
) -> None:
    _register(client, "3")
    conversation_id = _create_conversation(client)
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={
            "content": "调用某个扩展",
            "skill_id": "some-user-skill",
            "skill_input": {
                "skill_id": "some-user-skill",
                "contract": {
                    "path": "rewrite",
                    "source_text": "示例原文。",
                },
            },
        },
    )
    assert response.status_code == 410, response.text
    assert response.json()["detail"]["error"] == "user_extensions_retired"


# ---------------------------------------------------------------------------
# 重试退出（AC2）
# ---------------------------------------------------------------------------


def test_retry_of_legacy_humanizer_message_is_retired(
    client: TestClient, sqlite_app: Any
) -> None:
    account_id = _register(client, "4")
    conversation_id = _create_conversation(client)
    _seed_legacy_turn(
        sqlite_app,
        account_id,
        conversation_id,
        assistant_status=ChatMessageStatus.ERROR,
    )
    # 首轮普通回答由 queued 运行创建后仍处于 streaming；先停止该运行，
    # 使会话进入可重试状态（重试被拒的判定与运行状态无关）。
    detail = client.get(f"/chat/conversations/{conversation_id}").json()
    streaming = next(
        message
        for message in detail["messages"]
        if message["role"] == "assistant" and message["status"] == "streaming"
    )
    stop = client.post(
        f"/chat/conversations/{conversation_id}/messages/{streaming['message_id']}/stop"
    )
    assert stop.status_code == 200, stop.text
    response = client.post(
        f"/chat/conversations/{conversation_id}/messages/legacy-assistant-1/retry"
    )
    assert response.status_code == 410, response.text
    body = response.json()["detail"]
    assert body["error"] == "humanizer_retry_retired"
    # 重试被拒后不产生新尝试：助手消息只有首轮一条与历史一条。
    detail = client.get(f"/chat/conversations/{conversation_id}").json()
    assistant_ids = [
        message["message_id"]
        for message in detail["messages"]
        if message["role"] == "assistant"
    ]
    assert assistant_ids.count("legacy-assistant-1") == 1
    assert len(assistant_ids) == 2


# ---------------------------------------------------------------------------
# 继续执行退出（AC2）：历史遗留运行收敛为退役错误，不调用模型
# ---------------------------------------------------------------------------


def test_legacy_queued_humanizer_run_converges_retired_without_model_call(
    sqlite_app: Any, client: TestClient
) -> None:
    account_id = _register(client, "5")
    conversation_id = _create_conversation(client)
    _seed_legacy_turn(
        sqlite_app,
        account_id,
        conversation_id,
        assistant_status=ChatMessageStatus.STREAMING,
    )
    adapter = _CountingStreamAdapter()
    registry = CapabilityRegistry()
    registry.register(_chat_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", adapter)
    sqlite_app.state.chat_service._gateway = gateway

    service = sqlite_app.state.chat_service
    from bridges.contracts.projects import ObjectDomain
    from bridges.contracts.workflows import RunContextEnvelope

    run_context = RunContextEnvelope(
        run_id="legacy-resume-run",
        account_id=account_id,
        project_id=conversation_id,
        workflow_name="chat",
        workflow_version="1",
        object_domain=ObjectDomain.PERSONAL_VAULT,
        submitted_at=datetime.now(UTC),
    )
    events = list(
        service.stream_generation(
            account_id, conversation_id, "legacy-assistant-1", run_context
        )
    )
    error_events = [event for event in events if event.kind == "error"]
    assert len(error_events) == 1
    assert error_events[0].error_code == "humanizer_capability_retired"
    # 继续执行被拒绝：不发生任何模型调用，不做二次全文改写。
    assert adapter.calls == 0
    message = service._repo.get_message(account_id, "legacy-assistant-1")
    assert message is not None
    assert message.status == ChatMessageStatus.ERROR
    assert message.error_code == "humanizer_capability_retired"
    assert message.content == "（旧任务已产出的历史草稿正文）"


# ---------------------------------------------------------------------------
# 查看与导出保留（AC3）
# ---------------------------------------------------------------------------


def test_legacy_humanizer_result_stays_viewable(
    client: TestClient, sqlite_app: Any
) -> None:
    account_id = _register(client, "6")
    conversation_id = _create_conversation(client)
    _seed_legacy_turn(
        sqlite_app,
        account_id,
        conversation_id,
        assistant_status=ChatMessageStatus.DONE,
        assistant_skill=_legacy_result_projection().model_dump(mode="json"),
    )
    detail = client.get(f"/chat/conversations/{conversation_id}").json()
    assistant = next(
        message
        for message in detail["messages"]
        if message["message_id"] == "legacy-assistant-1"
    )
    assert assistant["humanizer"] is not None
    humanizer = assistant["humanizer"]
    assert humanizer["status"] == "done"
    assert (
        humanizer["output"]["final_text"]
        == "（历史结果：植物通过光合作用把光能储存为化学能。）"
    )
    # 输入快照（用户消息上的任务契约）同样按原样可读。
    owner = next(
        message
        for message in detail["messages"]
        if message["message_id"] == "legacy-user-1"
    )
    assert owner["skill"] == _SKILL_PAYLOAD


def test_export_includes_legacy_humanizer_result(
    client: TestClient, sqlite_app: Any
) -> None:
    account_id = _register(client, "7")
    conversation_id = _create_conversation(client)
    _seed_legacy_turn(
        sqlite_app,
        account_id,
        conversation_id,
        assistant_status=ChatMessageStatus.DONE,
        assistant_skill=_legacy_result_projection().model_dump(mode="json"),
    )
    response = client.post("/data/export")
    assert response.status_code == 200, response.text
    payload = json.loads(response.content)
    messages = payload["categories"]["messages"]["items"]
    exported = next(
        row for row in messages if row["message_id"] == "legacy-assistant-1"
    )
    assert exported["skill"] is not None
    skill = (
        json.loads(exported["skill"])
        if isinstance(exported["skill"], str)
        else exported["skill"]
    )
    assert skill["output"]["final_text"].startswith("（历史结果")
