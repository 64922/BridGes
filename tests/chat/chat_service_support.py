"""聊天服务测试：持久化状态机、停止/重试、断流收敛与账户隔离（Issue 11）。

服务层直接使用临时 sqlite 数据库与可编程流式适配器，不经过网络；
生成器通过 ``list(...)`` 消费以模拟流式推进。
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from bridges.ai import ModelGateway
from bridges.ai.adapters import AdapterError, StreamChunk
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.chat.service import (
    ChatService,
)
from bridges.contracts.ai import CapabilityKind, CapabilityRecord
from bridges.contracts.projects import ObjectDomain
from bridges.contracts.workflows import RunContextEnvelope


def _context(run_id: str = "run-1") -> RunContextEnvelope:
    return RunContextEnvelope(
        run_id=run_id,
        account_id="alice",
        project_id="conversation-1",
        workflow_name="chat",
        workflow_version="1",
        object_domain=ObjectDomain.PERSONAL_VAULT,
        submitted_at=datetime.now(UTC),
    )


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


class _ProgrammableStreamAdapter:
    """可编程流式适配器：预置块序列或连接阶段错误。"""

    def __init__(
        self,
        chunks: list[StreamChunk] | None = None,
        connect_error: AdapterError | None = None,
        slow: bool = False,
    ) -> None:
        self._chunks = chunks or []
        self._connect_error = connect_error
        self._slow = slow
        self.payloads: list[dict[str, Any]] = []

    def call(
        self,
        capability: CapabilityRecord,
        run_context: RunContextEnvelope,
        payload: dict[str, Any],
    ) -> Any:
        from bridges.ai.adapters import AdapterResult

        return AdapterResult(
            actual_model_id=capability.model_id,
            output={
                "content": "".join(c.delta for c in self._chunks if c.kind == "delta")
            },
        )

    def stream_call(
        self,
        capability: CapabilityRecord,
        run_context: RunContextEnvelope,
        payload: dict[str, Any],
    ):
        self.payloads.append(payload)
        if self._connect_error is not None:
            raise self._connect_error
        for chunk in self._chunks:
            if self._slow:
                time.sleep(0.02)
            yield chunk


def _start(service: ChatService, conversation_id: str, content: str = "你好"):
    # V2 Issue 02 起 start_generation 返回 (用户消息, 助手消息, 幂等重放标记)
    user_msg, assistant_msg, _ = service.start_generation("alice", conversation_id, content)
    return user_msg, assistant_msg


# ---------------------------------------------------------------------------


# 辅助
# ---------------------------------------------------------------------------


def first_message_of(projection: Any) -> Any:
    return projection.messages[0]


def _with_chunks(  # noqa: E501
    service: ChatService, chunks: list[StreamChunk] | None = None, **kwargs: Any
) -> ModelGateway:
    adapter = _ProgrammableStreamAdapter(chunks=chunks, **kwargs)
    registry = CapabilityRegistry()
    registry.register(_chat_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", adapter)
    return gateway
