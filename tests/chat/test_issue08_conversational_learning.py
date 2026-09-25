"""学习模式从强制状态机迁移到一次性介绍与轻量进度的契约测试。"""

from __future__ import annotations

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


class _CapturingStreamAdapter:
    """捕获模型载荷，并为两轮响应输出可解析的主题。"""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []
        self.calls = 0

    def stream_call(
        self,
        capability: CapabilityRecord,
        run_context: Any,
        payload: dict[str, Any],
    ):
        self.payloads.append(payload)
        self.calls += 1
        topic = "核心思想" if self.calls == 1 else "感受野"
        yield StreamChunk(
            kind="delta",
            delta=f"# {topic}\n\n基于合格来源讲解 [reference:1]。",
        )
        yield StreamChunk(kind="done")


class _IntentAwareSearchClient:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def search(self, query: str) -> list[WebSearchResult]:
        self.queries.append(query)
        if "Transformer" not in query:
            return []
        return [
            WebSearchResult(
                result_id="web-transformer",
                title="Transformer 架构公开讲义",
                site="example.com",
                url="https://example.com/transformer",
                snippet="Transformer 使用自注意力机制并行处理序列。",
                accessed_at=datetime.now(UTC),
                fetched_at=datetime.now(UTC),
                content_summary=(
                    "Transformer 是人工智能模型的架构，包含编码器、解码器和自注意力机制。"
                ),
            )
        ]


def _context(run_id: str = "run-issue08") -> RunContextEnvelope:
    return RunContextEnvelope(
        run_id=run_id,
        account_id="alice",
        project_id="conversation-issue08",
        workflow_name="chat",
        workflow_version="1",
        object_domain=ObjectDomain.PERSONAL_VAULT,
        submitted_at=datetime.now(UTC),
    )


def _make_service(
    tmp_path: Path,
    client: _IntentAwareSearchClient,
    adapter: _CapturingStreamAdapter,
) -> tuple[ChatService, ConversationRepository]:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    repository = ConversationRepository(database)
    registry = CapabilityRegistry()
    registry.register(_chat_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", adapter)
    return (
        ChatService(
            repository=repository,
            gateway=gateway,
            web_search_service=WebSearchService(client=client),
        ),
        repository,
    )


def _send(
    service: ChatService,
    conversation_id: str,
    content: str,
    *,
    account_id: str = "alice",
    run_id: str = "run-issue08",
) -> tuple[Any, Any, list[Any]]:
    user, assistant, _ = service.start_generation(
        account_id, conversation_id, content
    )
    events = list(
        service.stream_generation(
            account_id,
            conversation_id,
            assistant.message_id,
            _context(run_id),
            until_user_message_id=user.message_id,
        )
    )
    final = service.message_projection(account_id, assistant.message_id)
    assert final is not None
    return user, final, events


def _teaching(final: Any) -> TeachingTurnProjection:
    assert final.teaching is not None, "study 模式每轮必须有教学投影。"
    return TeachingTurnProjection.model_validate(final.teaching)


def test_first_round_is_complete_overview_without_legacy_state_machine(
    tmp_path: Path,
) -> None:
    client = _IntentAwareSearchClient()
    adapter = _CapturingStreamAdapter()
    service, _ = _make_service(tmp_path, client, adapter)
    conversation = service.create_conversation("alice", mode=ChatMode.STUDY)

    _, final, events = _send(service, conversation.conversation_id, "我想学习 Transformer")

    assert final.status == ChatMessageStatus.DONE
    teaching = _teaching(final)
    assert teaching.mission is None
    assert teaching.plan is None
    assert teaching.lesson is None
    assert teaching.quiz is None
    assert teaching.learning_progress is not None
    assert teaching.learning_progress.covered_topics == ["核心思想"]
    assert "Transformer" in teaching.learning_progress.goal
    assert "[reference:1]" in final.content
    assert len(adapter.payloads) == 1
    assert client.queries and all("我想学习" not in query for query in client.queries)
    assert [event.kind for event in events if event.kind not in {"stage"}] == [
        "delta",
        "done",
    ]


def test_follow_up_reuses_goal_and_appends_only_lightweight_topic_progress(
    tmp_path: Path,
) -> None:
    client = _IntentAwareSearchClient()
    adapter = _CapturingStreamAdapter()
    service, repository = _make_service(tmp_path, client, adapter)
    conversation = service.create_conversation("alice", mode=ChatMode.STUDY)

    _, first, _ = _send(service, conversation.conversation_id, "我想学习 Transformer")
    first_teaching = _teaching(first)
    _, second, _ = _send(
        service, conversation.conversation_id, "什么是感受野？"
    )
    second_teaching = _teaching(second)

    assert second_teaching.mission is None
    assert second_teaching.plan is None
    assert second_teaching.lesson is None
    assert second_teaching.quiz is None
    assert second_teaching.learning_progress is not None
    assert first_teaching.learning_progress is not None
    assert second_teaching.learning_progress.goal == first_teaching.learning_progress.goal
    assert second_teaching.learning_progress.covered_topics == ["核心思想", "感受野"]
    assert "已覆盖主题：核心思想" in str(adapter.payloads[-1])

    messages = repository.list_messages("alice", conversation.conversation_id)
    assert messages[-1].teaching is not None
    persisted = TeachingTurnProjection.model_validate(messages[-1].teaching)
    assert persisted.learning_progress == second_teaching.learning_progress


def test_missing_goal_asks_for_goal_without_search_or_model(tmp_path: Path) -> None:
    client = _IntentAwareSearchClient()
    adapter = _CapturingStreamAdapter()
    service, _ = _make_service(tmp_path, client, adapter)
    conversation = service.create_conversation("alice", mode=ChatMode.STUDY)

    _, final, _ = _send(service, conversation.conversation_id, "我想学一下")

    assert final.status == ChatMessageStatus.DONE
    teaching = _teaching(final)
    assert "你想学习什么主题" in final.content
    assert teaching.mission is None
    assert teaching.learning_progress is None
    assert adapter.payloads == []
    assert client.queries == []


def test_irrelevant_image_never_becomes_teaching_source(tmp_path: Path) -> None:
    """知识库只有无关图片时，图片不进入教学来源。"""
    from tests.retrieval.conftest import make_retrieval_env, make_storage

    env = make_retrieval_env(make_storage(tmp_path))
    database: BridgesDatabase = env["database"]
    retrieval = env["retrieval"]
    client = _IntentAwareSearchClient()
    adapter = _CapturingStreamAdapter()
    registry = CapabilityRegistry()
    registry.register(_chat_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", adapter)
    service = ChatService(
        repository=ConversationRepository(database),
        gateway=gateway,
        retrieval_service=retrieval,
        web_search_service=WebSearchService(client=client),
    )

    account = env["account_a"]
    stored = env["repository"].create_object(
        account, "巴巴博一.jpg", b"\xff\xd8\xff\xe0", media_type="image/jpeg"
    )
    assert env["ingestion"] is not None
    env["ingestion"].enqueue(account, stored.object_id)
    env["ingestion"].process_pending()

    conversation = service.create_conversation(account, mode=ChatMode.STUDY)
    _, final, _ = _send(
        service,
        conversation.conversation_id,
        "我想学习 Transformer",
        account_id=account,
        run_id="run-img",
    )
    teaching = _teaching(final)
    titles = [
        *(source.title for source in teaching.evidence_gate.local_sources),
        *(source.title for source in teaching.evidence_gate.external_sources),
    ]
    assert all("巴巴博一" not in title for title in titles)
    assert teaching.learning_progress is not None
