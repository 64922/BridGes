"""Issue 02：学习模式首响、追问与轻量进度的纵向切片。"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bridges.ai import ModelGateway
from bridges.ai.adapters import StreamChunk
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.chat.repository import ConversationRepository
from bridges.chat.service import ChatService
from bridges.contracts.ai import CapabilityKind, CapabilityRecord
from bridges.contracts.chat import ChatMessageStatus, ChatMode
from bridges.contracts.projects import ObjectDomain
from bridges.contracts.teaching import TeachingTurnProjection
from bridges.contracts.workflows import RunContextEnvelope
from bridges.storage.database import BridgesDatabase
from bridges.web_search.contracts import WebSearchResult
from bridges.web_search.service import WebSearchService


def _capability() -> CapabilityRecord:
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


class _SearchClient:
    def search(self, query: str) -> list[WebSearchResult]:
        return [
            WebSearchResult(
                result_id="web-1",
                title="卷积神经网络公开资料",
                site="example.com",
                url="https://example.com/cnn",
                snippet="卷积神经网络的基础机制。",
                accessed_at=datetime.now(UTC),
                fetched_at=datetime.now(UTC),
                content_summary="卷积神经网络的基础机制。",
            )
        ]


class _Adapter:
    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def stream_call(self, capability: CapabilityRecord, run_context: Any, payload: dict[str, Any]):
        self.payloads.append(payload)
        if len(self.payloads) == 1:
            content = "## 核心思想\n[reference:1] 卷积通过局部连接提取特征。"
        else:
            content = "## 感受野\n[reference:1] 感受野表示当前位置可利用的输入范围。"
        yield StreamChunk(kind="delta", delta=content)
        yield StreamChunk(kind="done")


def _context(run_id: str) -> RunContextEnvelope:
    return RunContextEnvelope(
        run_id=run_id,
        account_id="alice",
        project_id="conversation-issue02",
        workflow_name="chat",
        workflow_version="1",
        object_domain=ObjectDomain.PERSONAL_VAULT,
        submitted_at=datetime.now(UTC),
    )


def _service(tmp_path: Path, adapter: _Adapter) -> ChatService:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    registry = CapabilityRegistry()
    registry.register(_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", adapter)
    return ChatService(
        repository=ConversationRepository(database),
        gateway=gateway,
        web_search_service=WebSearchService(client=_SearchClient()),
    )


def _send(service: ChatService, conversation_id: str, content: str, run_id: str):
    user, assistant, _ = service.start_generation("alice", conversation_id, content)
    list(
        service.stream_generation(
            "alice",
            conversation_id,
            assistant.message_id,
            _context(run_id),
            until_user_message_id=user.message_id,
        )
    )
    final = service.message_projection("alice", assistant.message_id)
    assert final is not None
    return final


def test_首响直接全面介绍且终态只写轻量学习进度(tmp_path: Path) -> None:
    adapter = _Adapter()
    service = _service(tmp_path, adapter)
    conversation = service.create_conversation("alice", mode=ChatMode.STUDY)

    final = _send(
        service,
        conversation.conversation_id,
        "我想学习卷积神经网络的基础知识",
        "run-issue02-1",
    )

    assert final.status == ChatMessageStatus.DONE
    assert final.teaching is not None
    teaching = TeachingTurnProjection.model_validate(final.teaching)
    assert teaching.mission is None
    assert teaching.plan is None
    assert teaching.lesson is None
    assert teaching.quiz is None
    assert teaching.learning_progress is not None
    assert teaching.learning_progress.covered_topics == ["核心思想"]
    assert service.learning_progress("alice", conversation.conversation_id) == (
        teaching.learning_progress
    )
    database = service._repo.database
    assert database.connection.execute("SELECT COUNT(*) FROM teaching_plans").fetchone()[0] == 0
    assert database.connection.execute("SELECT COUNT(*) FROM learning_progress").fetchone()[0] == 1


def test_追问复用目标并把新主题追加到可恢复进度(tmp_path: Path) -> None:
    adapter = _Adapter()
    service = _service(tmp_path, adapter)
    conversation = service.create_conversation("alice", mode=ChatMode.STUDY)
    _send(service, conversation.conversation_id, "我想学习卷积神经网络的基础知识", "run-issue02-1")

    final = _send(service, conversation.conversation_id, "我想知道感受野", "run-issue02-2")

    assert final.status == ChatMessageStatus.DONE
    teaching = TeachingTurnProjection.model_validate(final.teaching)
    assert teaching.learning_progress is not None
    assert teaching.learning_progress.goal == "学习“卷积神经网络的基础知识”并理解其核心机制"
    assert teaching.learning_progress.covered_topics == ["核心思想", "感受野"]
    assert any(
        "已覆盖主题：核心思想" in str(message.get("content", ""))
        for message in adapter.payloads[1]["messages"]
        if message.get("role") == "system"
    )
