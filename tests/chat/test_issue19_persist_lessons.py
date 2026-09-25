"""历史教学定义只读兼容与新轻量学习进度的恢复契约。"""

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


class _Adapter:
    def __init__(self) -> None:
        self.calls = 0

    def stream_call(
        self, capability: CapabilityRecord, run_context: Any, payload: dict[str, Any]
    ):
        self.calls += 1
        topic = "核心思想" if self.calls == 1 else "自注意力机制"
        yield StreamChunk(
            kind="delta", delta=f"# {topic}\n\n基于 [reference:1] 的教学正文。"
        )
        yield StreamChunk(kind="done")


class _SearchClient:
    def search(self, query: str) -> list[WebSearchResult]:
        return [
            WebSearchResult(
                result_id="web-1",
                title="Transformer 公开讲义",
                site="example.com",
                url="https://example.com/transformer",
                snippet="Transformer 使用自注意力机制处理序列。",
                accessed_at=datetime.now(UTC),
                fetched_at=datetime.now(UTC),
                content_summary=(
                    "Transformer 是人工智能模型的架构，包含编码器、解码器和自注意力机制。"
                ),
            )
        ]


def _service(tmp_path: Path) -> ChatService:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    repository = ConversationRepository(database)
    registry = CapabilityRegistry()
    registry.register(_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", _Adapter())
    return ChatService(
        repository=repository,
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
            RunContextEnvelope(
                run_id=run_id,
                account_id="alice",
                project_id="issue19",
                workflow_name="chat",
                workflow_version="1",
                object_domain=ObjectDomain.PERSONAL_VAULT,
                submitted_at=datetime.now(UTC),
            ),
            until_user_message_id=user.message_id,
        )
    )
    final = service.message_projection("alice", assistant.message_id)
    assert final is not None
    return final


def test_chat_persists_lightweight_progress_and_keeps_legacy_tables_read_only(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    conversation = service.create_conversation("alice", mode=ChatMode.STUDY)
    first = _send(
        service, conversation.conversation_id, "我想学习 Transformer", "issue19-1"
    )
    assert first.status == ChatMessageStatus.DONE
    assert first.teaching is not None
    assert first.teaching.progress is None
    assert first.teaching.learning_progress is not None
    assert first.teaching.learning_progress.covered_topics == ["核心思想"]

    second = _send(
        service,
        conversation.conversation_id,
        "Transformer 用自注意力机制处理序列。",
        "issue19-2",
    )
    assert second.status == ChatMessageStatus.DONE
    assert second.teaching is not None
    assert second.teaching.progress is None
    assert second.teaching.learning_progress is not None
    assert second.teaching.learning_progress.covered_topics == [
        "核心思想",
        "自注意力机制",
    ]
    assert len(service._teaching_progress.list_lessons("alice", conversation.conversation_id)) == 0
    assert len(service._teaching_progress.list_quizzes("alice", conversation.conversation_id)) == 0

    progress_after_restart = service.learning_progress("alice", conversation.conversation_id)
    assert progress_after_restart is not None
    assert progress_after_restart.covered_topics == ["核心思想", "自注意力机制"]

    skipped = _send(
        service, conversation.conversation_id, "跳过", "issue19-invalid-answer"
    )
    assert skipped.status == ChatMessageStatus.DONE
    progress_after_invalid = service.learning_progress(
        "alice", conversation.conversation_id
    )
    assert progress_after_invalid is not None
    assert progress_after_invalid.covered_topics == progress_after_restart.covered_topics
