"""Issue 23：学习模式在证据不足时的聊天编排集成测试。"""

from datetime import UTC, datetime
from pathlib import Path

import httpx

from bridges.ai import ModelGateway
from bridges.ai.adapters import StreamChunk
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.chat.repository import ConversationRepository
from bridges.chat.service import ChatService
from bridges.chat.turn import ensure_unverified_teaching_prefix
from bridges.contracts.ai import CapabilityKind, CapabilityRecord
from bridges.contracts.chat import ChatMessageStatus, ChatMode
from bridges.contracts.projects import ObjectDomain
from bridges.contracts.workflows import RunContextEnvelope
from bridges.storage.database import BridgesDatabase
from bridges.web_search.client import DuckDuckGoClient
from bridges.web_search.contracts import WebSearchStatus
from bridges.web_search.service import WebSearchService


class _FallbackAdapter:
    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []

    def stream_call(
        self,
        capability: CapabilityRecord,
        run_context: object,
        payload: dict[str, object],
    ):
        self.payloads.append(payload)
        yield StreamChunk(
            kind="delta",
            delta=(
                "本轮未联网核实：卷积神经网络通常用于从局部模式开始提取特征。"
                "[web-1] https://fake.example/source [arxiv-1] https://arxiv.org/abs/1"
            ),
        )
        yield StreamChunk(kind="done")


class _VerifiedAdapter(_FallbackAdapter):
    def stream_call(
        self,
        capability: CapabilityRecord,
        run_context: object,
        payload: dict[str, object],
    ):
        self.payloads.append(payload)
        yield StreamChunk(
            kind="delta",
            delta="Transformer 架构的层次化表示可以帮助组织复杂模型。[reference:1]",
        )
        yield StreamChunk(kind="done")


def test_study_mode_marks_unverified_fallback_and_calls_model_without_evidence(
    tmp_path: Path,
) -> None:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    adapter = _FallbackAdapter()
    registry = CapabilityRegistry()
    registry.register(
        CapabilityRecord(
            name="qwen_text_chat",
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id="qwen3.7-plus-2026-05-26",
            input_schema_version="chat-messages-v1",
            output_schema_version="chat-completion-v1",
        )
    )
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", adapter)
    service = ChatService(
        repository=ConversationRepository(database),
        gateway=gateway,
    )
    conversation = service.create_conversation("alice", mode=ChatMode.STUDY)
    _, assistant = service.start_generation(
        "alice", conversation.conversation_id, "解释量子纠缠"
    )

    events = list(
        service.stream_generation(
            "alice",
            conversation.conversation_id,
            assistant.message_id,
            RunContextEnvelope(
                run_id="run-1",
                account_id="alice",
                project_id="conversation-1",
                workflow_name="chat",
                workflow_version="1",
                object_domain=ObjectDomain.PERSONAL_VAULT,
                submitted_at=datetime.now(UTC),
            ),
        )
    )

    final = service.message_projection("alice", assistant.message_id)
    assert final is not None
    assert final.status == ChatMessageStatus.DONE
    assert final.teaching is not None
    assert final.teaching.can_answer_reliably is False
    assert final.teaching.evidence_gate.gap
    assert final.teaching.evidence_gate.allow_model_knowledge is True
    assert final.content.startswith("本轮未联网核实：")
    assert "卷积神经网络" in final.content
    assert "[web-1]" not in final.content
    assert "https://fake.example/source" not in final.content
    assert "[arxiv-1]" not in final.content
    assert "https://arxiv.org/abs/1" not in final.content
    assert adapter.payloads
    assert "允许使用模型一般知识" in str(adapter.payloads[0])
    assert final.content != final.teaching.gap_response
    assert [event.kind for event in events if event.kind != "stage"] == [
        "delta",
        "delta",
        "done",
    ]


def test_unverified_teaching_prefix_is_normalized_to_once() -> None:
    content = "本轮未联网核实：\n本轮未联网核实：\n谨慎说明。"

    normalized = ensure_unverified_teaching_prefix(content)

    assert normalized.count("本轮未联网核实：") == 1
    assert normalized.endswith("谨慎说明。")


def test_study_mode_challenge_is_persisted_as_provider_blocked_without_citations(
    tmp_path: Path,
) -> None:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    adapter = _FallbackAdapter()
    registry = CapabilityRegistry()
    registry.register(
        CapabilityRecord(
            name="qwen_text_chat",
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id="qwen3.7-plus-2026-05-26",
            input_schema_version="chat-messages-v1",
            output_schema_version="chat-completion-v1",
        )
    )
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", adapter)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            202,
            content=(
                "<html><body><h1>Complete human verification challenge</h1>"
                "</body></html>"
            ).encode(),
        )

    web_search = WebSearchService(
        client=DuckDuckGoClient(
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
            fetch_sources=False,
        )
    )
    service = ChatService(
        repository=ConversationRepository(database),
        gateway=gateway,
        web_search_service=web_search,
    )
    conversation = service.create_conversation("alice", mode=ChatMode.STUDY)
    _, assistant = service.start_generation(
        "alice", conversation.conversation_id, "解释量子纠缠"
    )

    list(
        service.stream_generation(
            "alice",
            conversation.conversation_id,
            assistant.message_id,
            RunContextEnvelope(
                run_id="run-challenge",
                account_id="alice",
                project_id="conversation-1",
                workflow_name="chat",
                workflow_version="1",
                object_domain=ObjectDomain.PERSONAL_VAULT,
                submitted_at=datetime.now(UTC),
            ),
        )
    )

    final = service.message_projection("alice", assistant.message_id)
    assert final is not None
    assert final.status == ChatMessageStatus.DONE
    assert final.web_search is not None
    assert final.web_search.status == WebSearchStatus.ERROR
    assert final.web_search.error_code == "web_search_provider_challenge"
    assert final.web_search.query_count == 1
    assert final.web_search.results == []
    assert len(requests) == 1
    assert final.teaching is not None
    assert final.teaching.evidence_gate.search_error_code == (
        "web_search_provider_challenge"
    )
    assert final.teaching.evidence_gate.external_sources == []
    assert final.teaching.can_answer_reliably is False
    assert final.content.count("本轮未联网核实：") == 1
    assert "[web-1]" not in final.content


def test_study_mode_normal_results_provide_verified_source_and_bindable_citation(
    tmp_path: Path,
) -> None:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    adapter = _VerifiedAdapter()
    registry = CapabilityRegistry()
    registry.register(
        CapabilityRecord(
            name="qwen_text_chat",
            version="1",
            kind=CapabilityKind.MODEL,
            vendor="qwen",
            region="cn-beijing",
            model_id="qwen3.7-plus-2026-05-26",
            input_schema_version="chat-messages-v1",
            output_schema_version="chat-completion-v1",
        )
    )
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", adapter)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "html.duckduckgo.com":
            return httpx.Response(
                200,
                content=(
                    "<html><body>"
                    "<a class='result__a' href='https://example.com/transformer'>"
                    "Transformer 架构公开资料</a>"
                    "<a class='result__snippet'>介绍模型的层次化结构。</a>"
                    "</body></html>"
                ).encode(),
            )
        return httpx.Response(
            200,
            content=(
                "<html><head><title>Transformer 架构</title></head>"
                "<body>Transformer architecture uses self-attention to build "
                "an AI model architecture.</body></html>"
            ).encode(),
        )

    web_search = WebSearchService(
        client=DuckDuckGoClient(
            http_client=httpx.Client(transport=httpx.MockTransport(handler))
        )
    )
    service = ChatService(
        repository=ConversationRepository(database),
        gateway=gateway,
        web_search_service=web_search,
    )
    conversation = service.create_conversation("alice", mode=ChatMode.STUDY)
    _, assistant = service.start_generation(
        "alice", conversation.conversation_id, "我想学习Transformer架构的相关知识"
    )

    list(
        service.stream_generation(
            "alice",
            conversation.conversation_id,
            assistant.message_id,
            RunContextEnvelope(
                run_id="run-normal-results",
                account_id="alice",
                project_id="conversation-1",
                workflow_name="chat",
                workflow_version="1",
                object_domain=ObjectDomain.PERSONAL_VAULT,
                submitted_at=datetime.now(UTC),
            ),
        )
    )

    final = service.message_projection("alice", assistant.message_id)
    assert final is not None
    assert final.status == ChatMessageStatus.DONE
    assert final.web_search is not None
    assert final.web_search.status in {WebSearchStatus.SUCCESS, WebSearchStatus.PARTIAL}
    assert final.web_search.http_status_category == "2xx"
    assert final.web_search.results
    assert final.web_search.results[0].verification == "verified"
    assert final.teaching is not None
    assert final.teaching.can_answer_reliably is True
    assert len(final.teaching.evidence_gate.external_sources) == 1
    assert "[reference:1]" in final.content
    assert "本轮未联网核实" not in final.content
    assert sum(request.url.host == "html.duckduckgo.com" for request in requests) == 1
