"""V2 Issue 09 验收测试：运行级模型锁定与新旧轮次采用新配置。

覆盖验收标准（``.scratch/bridges-v2/issues/09-qwen-model-configuration.md``）：
- 验证通过的模型配置原子激活后，**新旧会话的下一轮**采用新配置；
- **进行中的轮次**维持启动时锁定的模型，不因激活而中途切换；
- 历史回复的模型记录不改写（消息记录的是该轮实际使用的模型）。
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from bridges.ai.adapters import AdapterResult, StreamChunk
from bridges.ai.fixed_models import CHAT_MODEL_ID
from bridges.contracts.ai import ModelCapabilities
from tests.chat.test_chat_api import (
    _create_conversation,
    _gateway_with,
    _register,
)
from tests.chat.test_issue02_durable_generation import (  # noqa: F401 - 复用夹具
    client,
    sqlite_app,
)

_MODEL_A = "qwen-user-model-a"
_MODEL_B = "qwen-user-model-b"


class _RecordingChatAdapter:
    """流式聊天替身：记录每次调用实际使用的模型 ID。"""

    def __init__(self) -> None:
        self.models: list[str] = []

    def call(
        self, capability: Any, run_context: Any, payload: dict[str, Any]
    ) -> AdapterResult:
        self.models.append(capability.model_id or "")
        return AdapterResult(
            actual_model_id=capability.model_id, output={"content": "好的"}
        )

    def stream_call(
        self, capability: Any, run_context: Any, payload: dict[str, Any]
    ) -> Any:
        self.models.append(capability.model_id or "")
        yield StreamChunk(kind="delta", delta="好的")
        yield StreamChunk(kind="done", actual_model_id=capability.model_id)


def _activate(app: Any, model_id: str) -> None:
    app.state.run_model_config_provider.activate(
        model_id=model_id,
        capabilities=ModelCapabilities(
            text=True, image=True, tool_calling=True, structured_output=True
        ),
        context_window=131_072,
        max_input_tokens=130_048,
        validated_at=None,
    )


def _assistant_projection(client: TestClient, conversation_id: str) -> list[dict[str, Any]]:
    projection = client.get(f"/chat/conversations/{conversation_id}").json()
    return [message for message in projection["messages"] if message["role"] == "assistant"]


def test_in_flight_run_keeps_its_model_and_next_run_takes_the_new_one(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    account = _register(client)
    adapter = _RecordingChatAdapter()
    sqlite_app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
    _activate(sqlite_app, _MODEL_A)
    conversation_id = _create_conversation(client)

    first = generation_helpers["send"](client, conversation_id, content="第一轮")
    first_run = sqlite_app.state.chat_service.generation_run(
        account["id"], first["run_id"]
    )
    assert first_run is not None and first_run.config is not None
    # 运行在创建（= 本轮启动）时锁定当时的运行配置。
    assert first_run.config["run_model_id"] == _MODEL_A

    # 轮次进行中：激活新配置不得切换本轮模型。
    _activate(sqlite_app, _MODEL_B)
    generation_helpers["drive"](sqlite_app)

    assert adapter.models == [_MODEL_A]
    completed_run = sqlite_app.state.chat_service.generation_run(
        account["id"], first["run_id"]
    )
    assert completed_run is not None and completed_run.status == "done"
    first_reply = _assistant_projection(client, conversation_id)[0]
    assert first_reply["model_id"] == _MODEL_A

    # 下一轮采用新配置；历史回复的模型记录不改写。
    second = generation_helpers["send"](client, conversation_id, content="第二轮")
    second_run = sqlite_app.state.chat_service.generation_run(
        account["id"], second["run_id"]
    )
    assert second_run is not None and second_run.config is not None
    assert second_run.config["run_model_id"] == _MODEL_B
    generation_helpers["drive"](sqlite_app)

    assert adapter.models == [_MODEL_A, _MODEL_B]
    replies = _assistant_projection(client, conversation_id)
    assert [reply["model_id"] for reply in replies] == [_MODEL_A, _MODEL_B]


def test_runs_without_a_provider_keep_the_factory_matrix_binding(
    sqlite_app: Any, client: TestClient, generation_helpers: dict[str, Any]
) -> None:
    """未装配运行配置提供者时运行配置不写入模型锁定（沿用出厂矩阵）。"""
    account = _register(client, tag="2")
    adapter = _RecordingChatAdapter()
    sqlite_app.state.chat_service._gateway = _gateway_with(adapter)  # noqa: SLF001
    sqlite_app.state.chat_service._model_config_provider = None  # noqa: SLF001
    conversation_id = _create_conversation(client)

    created = generation_helpers["send"](client, conversation_id, content="普通一轮")
    run = sqlite_app.state.chat_service.generation_run(account["id"], created["run_id"])

    assert run is not None and run.config is not None
    assert "run_model_id" not in run.config
    generation_helpers["drive"](sqlite_app)

    assert adapter.models == [CHAT_MODEL_ID]
