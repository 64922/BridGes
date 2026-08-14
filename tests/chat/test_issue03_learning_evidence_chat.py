"""Issue 03：学习模式在聊天编排中复用同一份已裁决来源集。"""

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
from bridges.contracts.observability import AuditAction
from bridges.contracts.projects import ObjectDomain
from bridges.contracts.teaching import TeachingTurnProjection
from bridges.contracts.workflows import RunContextEnvelope
from bridges.observability.service import ObservabilityService
from bridges.storage.database import BridgesDatabase
from bridges.web_search.contracts import (
    WebSearchResult,
    WebSearchStatus,
)
from bridges.web_search.service import WebSearchService


NOW = datetime(2026, 8, 12, tzinfo=UTC)


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
    def __init__(self, results: list[WebSearchResult]) -> None:
        self.results = results

    def search(self, query: str) -> list[WebSearchResult]:
        return list(self.results)


class _Adapter:
    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def stream_call(
        self,
        capability: CapabilityRecord,
        run_context: Any,
        payload: dict[str, Any],
    ):
        self.payloads.append(payload)
        yield StreamChunk(kind="delta", delta="# 核心机制\n\n基于 [reference:1] 的说明。")
        yield StreamChunk(kind="done")


def _result(
    result_id: str,
    title: str,
    snippet: str,
    content: str,
) -> WebSearchResult:
    return WebSearchResult(
        result_id=result_id,
        title=title,
        site="example.com",
        url=f"https://example.com/{result_id}",
        snippet=snippet,
        accessed_at=NOW,
        fetched_at=NOW,
        content_summary=content,
    )


def _service(
    tmp_path: Path,
    results: list[WebSearchResult],
    observability: ObservabilityService | None = None,
) -> tuple[ChatService, _Adapter]:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    adapter = _Adapter()
    registry = CapabilityRegistry()
    registry.register(_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", adapter)
    return (
        ChatService(
            repository=ConversationRepository(database),
            gateway=gateway,
            web_search_service=WebSearchService(client=_SearchClient(results)),
            observability_service=observability,
        ),
        adapter,
    )


def _send(service: ChatService, conversation_id: str, content: str) -> Any:
    _, assistant = service.start_generation("alice", conversation_id, content)
    list(
        service.stream_generation(
            "alice",
            conversation_id,
            assistant.message_id,
            RunContextEnvelope(
                run_id="run-issue03",
                account_id="alice",
                project_id="conversation-issue03",
                workflow_name="chat",
                workflow_version="1",
                object_domain=ObjectDomain.PERSONAL_VAULT,
                submitted_at=NOW,
            ),
        )
    )
    final = service.message_projection("alice", assistant.message_id)
    assert final is not None
    return final


def test_chat_只把已接受来源放进模型上下文并推进一次进度(tmp_path: Path) -> None:
    service, adapter = _service(
        tmp_path,
        [
            _result(
                "web-ai",
                "AI Transformer 架构与自注意力",
                "Transformer 架构使用自注意力机制处理序列。",
                "Transformer 是人工智能模型的神经网络架构，由编码器、解码器和自注意力机制组成。",
            ),
            _result(
                "web-power",
                "电力变压器工作原理",
                "电力系统中的变压器用于改变电压。",
                "电力变压器通过电磁感应改变交流电压。",
            ),
        ],
    )
    conversation = service.create_conversation("alice", mode=ChatMode.STUDY)

    final = _send(service, conversation.conversation_id, "我想学习Transformer架构的相关知识")

    assert final.status == ChatMessageStatus.DONE
    assert final.web_search is not None
    assert {result.result_id for result in final.web_search.results} == {
        "web-ai",
        "web-power",
    }
    assert final.teaching is not None
    teaching = TeachingTurnProjection.model_validate(final.teaching)
    assert teaching.can_answer_reliably is True
    assert [source.source_id for source in teaching.evidence_gate.external_sources] == [
        "web-ai"
    ]
    payload_text = "\n".join(
        str(message.get("content", "")) for message in adapter.payloads[0]["messages"]
    )
    assert "AI Transformer 架构与自注意力" in payload_text
    assert "电力变压器工作原理" not in payload_text
    assert "[reference:1]" in final.content
    assert "未联网核实" not in final.content
    assert teaching.learning_progress is not None


def test_chat_cnn_source_is_accepted_from_the_learning_goal(tmp_path: Path) -> None:
    service, adapter = _service(
        tmp_path,
        [
            _result(
                "web-cnn",
                "卷积神经网络（CNN）基础知识入门",
                "卷积神经网络是常见的神经网络架构。",
                "卷积神经网络通过局部感受野提取特征，构成其核心机制。",
            ),
            _result(
                "web-power",
                "电力变压器工作原理",
                "电力系统中的变压器用于改变电压。",
                "电力变压器通过电磁感应改变交流电压。",
            ),
        ],
    )
    conversation = service.create_conversation("alice", mode=ChatMode.STUDY)

    final = _send(
        service,
        conversation.conversation_id,
        "我想学习卷积神经网络的相关基础知识",
    )

    assert final.status == ChatMessageStatus.DONE
    assert final.teaching is not None
    teaching = TeachingTurnProjection.model_validate(final.teaching)
    assert teaching.can_answer_reliably is True
    assert teaching.status.value == "ready"
    assert [source.source_id for source in teaching.evidence_gate.external_sources] == [
        "web-cnn"
    ]
    assert "未联网核实" not in final.content
    assert "已降级为模型知识回答" not in final.content
    assert "[reference:1]" in final.content
    assert teaching.learning_progress is not None
    assert service.learning_progress("alice", conversation.conversation_id) is not None
    payload_text = "\n".join(
        str(message.get("content", "")) for message in adapter.payloads[0]["messages"]
    )
    assert "卷积神经网络（CNN）基础知识入门" in payload_text
    assert "电力变压器工作原理" not in payload_text


def test_chat_搜索成功但无覆盖时不伪造引用也不写入学习进度(tmp_path: Path) -> None:
    service, adapter = _service(
        tmp_path,
        [
            _result(
                "web-power",
                "电力变压器工作原理",
                "电力系统中的变压器用于改变电压。",
                "电力变压器通过电磁感应改变交流电压。",
            )
        ],
    )
    conversation = service.create_conversation("alice", mode=ChatMode.STUDY)

    final = _send(service, conversation.conversation_id, "我想学习Transformer架构的相关知识")

    assert final.status == ChatMessageStatus.DONE
    assert final.web_search is not None
    assert final.web_search.status.value == "success"
    assert final.web_search.results[0].result_id == "web-power"
    assert final.teaching is not None
    teaching = TeachingTurnProjection.model_validate(final.teaching)
    assert teaching.can_answer_reliably is False
    assert teaching.evidence_gate.external_sources == []
    assert "已搜索但未覆盖本轮目标" in teaching.evidence_gate.reason
    assert final.content.startswith("本轮未联网核实：")
    assert "[reference:1]" not in final.content
    assert adapter.payloads
    payload_text = "\n".join(
        str(message.get("content", "")) for message in adapter.payloads[0]["messages"]
    )
    assert "电力变压器工作原理" not in payload_text
    assert teaching.learning_progress is None
    assert service.learning_progress("alice", conversation.conversation_id) is None


def test_chat_覆盖裁决审计只记录脱敏计数(tmp_path: Path) -> None:
    observability = ObservabilityService()
    service, _ = _service(
        tmp_path,
        [
            _result(
                "web-power",
                "电力变压器工作原理",
                "电力系统中的变压器用于改变电压。",
                "电力变压器通过电磁感应改变交流电压。",
            )
        ],
        observability,
    )
    conversation = service.create_conversation("alice", mode=ChatMode.STUDY)

    _send(service, conversation.conversation_id, "我想学习Transformer架构的相关知识")

    events = observability.list_audit_events(
        account_id="alice",
        action=AuditAction.TEACHING_EVIDENCE_ADJUDICATION,
    )
    assert len(events) == 1
    details = events[0].details
    assert details["candidate_count"] == 1
    assert details["fetched_count"] == 1
    assert details["accepted_count"] == 0
    assert details["rejection_counts"] == {"topic_mismatch": 1}
    assert details["topic_aliases_version"] == "learning-evidence-topic-aliases-v1"
    assert "Transformer" not in str(details)


def test_chat_arxiv_and_web_parallel_reloadable_with_single_terminal(
    tmp_path: Path,
) -> None:
    """AC11：arXiv+Tavily 并行场景保持终态可重载，SSE 只发出一个最终搜索终态。"""
    from bridges.arxiv_mcp.contracts import (
        ArxivPaper,
        ArxivSearchProjection,
        ArxivSearchStatus,
    )
    from bridges.arxiv_mcp.service import ArxivSearchPlan, ArxivSearchService

    class _PaperClient:
        def __init__(self) -> None:
            self.queries: list[str] = []

        def search(
            self, query: str, *, max_results: int = 5, stop_event: Any | None = None
        ) -> list[ArxivPaper]:
            self.queries.append(query)
            return [
                ArxivPaper(
                    arxiv_id="2401.12345v2",
                    title="Transformer 架构研究综述",
                    authors=["Ada"],
                    published_at=datetime(2024, 1, 18, tzinfo=UTC),
                    abs_url="https://arxiv.org/abs/2401.12345v2",
                    pdf_url="https://arxiv.org/pdf/2401.12345v2",
                    abstract=(
                        "学习最新公开Transformer架构研究综述并理解其核心机制，"
                        "使用自注意力机制处理序列。"
                    ),
                )
            ][:max_results]

    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    adapter = _Adapter()
    registry = CapabilityRegistry()
    registry.register(_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", adapter)
    web_results = [
        _result(
            "web-ai",
            "AI Transformer 架构与自注意力",
            "Transformer 架构使用自注意力机制处理序列。",
            "Transformer 是人工智能模型的神经网络架构，由编码器、解码器和自注意力机制组成。",
        )
    ]
    service = ChatService(
        repository=ConversationRepository(database),
        gateway=gateway,
        web_search_service=WebSearchService(client=_SearchClient(web_results)),
        arxiv_search_service=ArxivSearchService(client=_PaperClient()),
    )
    conversation = service.create_conversation("alice", mode=ChatMode.STUDY)
    user, assistant = service.start_generation(
        "alice",
        conversation.conversation_id,
        "我想学习最新公开的Transformer架构论文研究综述",
    )

    events = list(
        service.stream_generation(
            "alice",
            conversation.conversation_id,
            assistant.message_id,
            RunContextEnvelope(
                run_id="run-both",
                account_id="alice",
                project_id="conversation-both",
                workflow_name="chat",
                workflow_version="1",
                object_domain=ObjectDomain.PERSONAL_VAULT,
                submitted_at=NOW,
            ),
            until_user_message_id=user.message_id,
        )
    )
    final = service.message_projection("alice", assistant.message_id)
    assert final is not None
    assert final.status == ChatMessageStatus.DONE
    assert final.web_search is not None
    assert final.web_search.status == WebSearchStatus.SUCCESS
    assert final.arxiv_search is not None
    assert final.arxiv_search.status == ArxivSearchStatus.SUCCESS
    assert final.arxiv_search.papers[0].arxiv_id == "2401.12345v2"
    # SSE 只发出一个最终搜索终态：done 是最后一个事件，且没有重复终态。
    terminal = [event for event in events if event.kind in {"done", "error", "stopped"}]
    assert len(terminal) == 1
    assert terminal[0].kind == "done"
    assert events[-1].kind == "done"
    # 终态可重载：再次读取同一投影仍是同一终态。
    reloaded = service.message_projection("alice", assistant.message_id)
    assert reloaded is not None
    assert reloaded.status == ChatMessageStatus.DONE
    assert reloaded.web_search.status == WebSearchStatus.SUCCESS
    assert reloaded.arxiv_search.status == ArxivSearchStatus.SUCCESS
