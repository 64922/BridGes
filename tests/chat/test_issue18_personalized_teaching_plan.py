"""Issue 18：首次学习请求的计划、第一课与失败态契约。"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from bridges.ai import ModelGateway
from bridges.ai.adapters import StreamChunk
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.chat.repository import ConversationRepository
from bridges.chat.service import ChatDomainError, ChatService
from bridges.contracts.ai import CapabilityKind, CapabilityRecord
from bridges.contracts.chat import ChatMessageStatus, ChatMode
from bridges.contracts.projects import ObjectDomain
from bridges.contracts.retrieval import (
    CitationProjection,
    RetrievalRoundProjection,
    RetrievalSourceLayer,
    RetrievalSufficiency,
)
from bridges.contracts.teaching import TeachingTurnProjection
from bridges.contracts.workflows import RunContextEnvelope
from bridges.learning.teaching_gate import TeachingTurnService
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
    def __init__(self) -> None:
        self.queries: list[str] = []

    def search(self, query: str) -> list[WebSearchResult]:
        self.queries.append(query)
        return [
            WebSearchResult(
                result_id="web-1",
                title="Transformer 公开讲义",
                site="example.com",
                url="https://example.com/transformer",
                snippet="Transformer 使用自注意力机制并行处理序列。",
                accessed_at=datetime.now(UTC),
            )
        ]


class _Adapter:
    def __init__(self, *, fail: bool = False) -> None:
        self.payloads: list[dict[str, Any]] = []
        self.fail = fail

    def stream_call(self, capability: CapabilityRecord, run_context: Any, payload: dict[str, Any]):
        self.payloads.append(payload)
        if self.fail:
            yield StreamChunk(
                kind="error",
                error_code="provider_error",
                error_message="模拟模型失败",
            )
            return
        yield StreamChunk(kind="delta", delta="基于 [web-1] 的第一课正文。")
        yield StreamChunk(kind="done")


def _context(run_id: str) -> RunContextEnvelope:
    return RunContextEnvelope(
        run_id=run_id,
        account_id="alice",
        project_id="conversation-issue18",
        workflow_name="chat",
        workflow_version="1",
        object_domain=ObjectDomain.PERSONAL_VAULT,
        submitted_at=datetime.now(UTC),
    )


def _service(
    tmp_path: Path,
    adapter: _Adapter,
    client: _SearchClient | None = None,
) -> ChatService:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    registry = CapabilityRegistry()
    registry.register(_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", adapter)
    return ChatService(
        repository=ConversationRepository(database),
        gateway=gateway,
        web_search_service=WebSearchService(client=client) if client is not None else None,
    )


def _send(service: ChatService, conversation_id: str, content: str, run_id: str):
    user, assistant = service.start_generation("alice", conversation_id, content)
    events = list(
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
    return final, events


def test_clear_goal_publishes_one_versioned_plan_and_first_lesson_in_one_reply(
    tmp_path: Path,
) -> None:
    adapter = _Adapter()
    client = _SearchClient()
    service = _service(tmp_path, adapter, client)
    conversation = service.create_conversation("alice", mode=ChatMode.STUDY)

    final, events = _send(
        service, conversation.conversation_id, "我想学习 Transformer", "run-issue18-1"
    )

    assert final.status == ChatMessageStatus.DONE
    assert final.content == "基于 [web-1] 的第一课正文。"
    assert final.teaching is not None
    teaching = TeachingTurnProjection.model_validate(final.teaching)
    assert teaching.plan is not None
    assert teaching.plan.version == 1
    assert teaching.lesson is not None
    assert teaching.lesson.lesson_number == 1
    assert teaching.lesson.plan_id == teaching.plan.plan_id
    assert teaching.lesson.explanation == final.content
    assert teaching.plan.owner_account_id == "alice"
    assert teaching.lesson.owner_account_id == "alice"
    assert teaching.plan.artifact_status.value == "published"
    assert teaching.lesson.artifact_status.value == "published"
    assert teaching.lesson.target_snapshot_id == teaching.plan.target_snapshot_id
    assert teaching.lesson.evidence_snapshot_id == teaching.plan.evidence_snapshot_id
    assert teaching.plan.profile_usage.defaulted is True
    assert len(adapter.payloads) == 1
    assert client.queries and all("我想学习" not in query for query in client.queries)
    assert [event.kind for event in events if event.kind not in {"stage"}] == [
        "delta",
        "done",
    ]
    with pytest.raises(ChatDomainError) as retry_error:
        service.retry_generation("alice", conversation.conversation_id, final.message_id)
    assert retry_error.value.code == "teaching_already_published"

    modified, _ = _send(
        service, conversation.conversation_id, "换个主题学 Python", "run-issue18-5"
    )
    assert modified.teaching is not None
    modified_teaching = TeachingTurnProjection.model_validate(modified.teaching)
    assert modified_teaching.plan is not None
    assert modified_teaching.lesson is not None
    assert modified_teaching.plan.version == 2
    assert modified_teaching.lesson.lesson_number == 1
    assert modified_teaching.lesson.plan_id == modified_teaching.plan.plan_id
    assert modified_teaching.plan.plan_id != teaching.plan.plan_id


@pytest.mark.parametrize("query", ["我想学一下", "帮我制定学习计划", "学习目标是"])
def test_missing_goal_only_asks_for_goal_without_search_or_model(
    tmp_path: Path, query: str
) -> None:
    adapter = _Adapter()
    client = _SearchClient()
    service = _service(tmp_path, adapter, client)
    conversation = service.create_conversation("alice", mode=ChatMode.STUDY)

    final, _ = _send(service, conversation.conversation_id, query, "run-issue18-2")

    assert final.status == ChatMessageStatus.DONE
    assert "你想学习什么主题" in final.content
    assert final.teaching is not None
    teaching = TeachingTurnProjection.model_validate(final.teaching)
    assert teaching.plan is None
    assert teaching.lesson is None
    assert adapter.payloads == []
    assert client.queries == []


def test_missing_evidence_does_not_create_plan_but_allows_marked_model_fallback(
    tmp_path: Path,
) -> None:
    adapter = _Adapter()
    service = _service(tmp_path, adapter)
    conversation = service.create_conversation("alice", mode=ChatMode.STUDY)

    final, _ = _send(
        service, conversation.conversation_id, "我想学习 Transformer", "run-issue18-3"
    )

    assert final.status == ChatMessageStatus.DONE
    assert final.teaching is not None
    teaching = TeachingTurnProjection.model_validate(final.teaching)
    assert teaching.plan is None
    assert teaching.lesson is None
    assert teaching.can_answer_reliably is False
    assert teaching.evidence_gate.gap
    assert teaching.evidence_gate.allow_model_knowledge is True
    assert final.content.startswith("本轮未联网核实：")
    assert adapter.payloads


def test_model_failure_clears_staged_plan_and_allows_retry(tmp_path: Path) -> None:
    adapter = _Adapter(fail=True)
    client = _SearchClient()
    service = _service(tmp_path, adapter, client)
    conversation = service.create_conversation("alice", mode=ChatMode.STUDY)

    final, events = _send(
        service, conversation.conversation_id, "我想学习 Transformer", "run-issue18-4"
    )

    assert final.status == ChatMessageStatus.ERROR
    assert final.teaching is not None
    teaching = TeachingTurnProjection.model_validate(final.teaching)
    assert teaching.plan is None
    assert teaching.lesson is None
    assert teaching.can_retry is True
    assert [event.kind for event in events if event.kind not in {"stage"}] == ["error"]


def test_profile_usage_only_changes_adaptation_fields() -> None:
    service = TeachingTurnService()
    turn = service.prepare(
        "Transformer",
        retrieval=RetrievalRoundProjection(
            round_id="round-issue18",
            message_id="assistant-issue18",
            conversation_id="conversation-issue18",
            use_knowledge_base=True,
            sufficiency=RetrievalSufficiency.SUFFICIENT,
            layers=[],
            citations=[
                CitationProjection(
                    citation_id="cit-issue18",
                    source_layer=RetrievalSourceLayer.ATTACHMENT,
                    object_id="obj-issue18",
                    filename="讲义.txt",
                    media_type="text/plain",
                    snippet="核心机制",
                    rank=1,
                )
            ],
            created_at=datetime.now(UTC),
        ),
    )
    setup = service.mission_setup("我想学习 Transformer", mission_id="mission-issue18")
    assert setup.mission is not None
    mission = service.confirm_mission("", setup.mission)

    personalized = service.compose_first_plan_and_lesson(
        turn,
        mission,
        profile_items=[
            ("academic_status", "大学一年级"),
            ("knowledge_interest", "机器学习"),
            ("hobby", "摄影"),
            ("stage_goal", "完成课程项目"),
        ],
        profile_slice_id="slice-issue18",
    )
    assert personalized.plan is not None
    assert personalized.lesson is not None
    assert personalized.plan.profile_slice_id == "slice-issue18"
    assert personalized.plan.profile_usage.used_categories == [
        "academic_status",
        "hobby",
        "knowledge_interest",
        "stage_goal",
    ]
    assert personalized.plan.profile_usage.applied_to == [
        "difficulty",
        "examples",
        "path",
    ]
    assert any(value in personalized.lesson.examples[0] for value in ("机器学习", "摄影"))
