"""Issue 22：arXiv 搜索进入聊天持久化与 SSE 生成链路的测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bridges.ai import ModelGateway
from bridges.ai.adapters import StreamChunk
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.arxiv_mcp.client import ArxivMcpError
from bridges.arxiv_mcp.contracts import ArxivPaper
from bridges.arxiv_mcp.service import ArxivSearchService
from bridges.chat.repository import ConversationRepository
from bridges.chat.service import ChatService
from bridges.contracts.ai import CapabilityKind, CapabilityRecord
from bridges.contracts.projects import ObjectDomain
from bridges.contracts.workflows import RunContextEnvelope
from bridges.storage.database import BridgesDatabase


def _context() -> RunContextEnvelope:
    return RunContextEnvelope(
        run_id="run-arxiv-1",
        account_id="alice",
        project_id="conversation-1",
        workflow_name="chat",
        workflow_version="1",
        object_domain=ObjectDomain.PERSONAL_VAULT,
        submitted_at=datetime.now(UTC),
    )


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


def _paper() -> ArxivPaper:
    return ArxivPaper(
        arxiv_id="2401.12345v2",
        title="Quantum Error Correction with Structured Codes",
        authors=["Ada Lovelace"],
        published_at=datetime(2024, 1, 18, tzinfo=UTC),
        abs_url="https://arxiv.org/abs/2401.12345v2",
        pdf_url="https://arxiv.org/pdf/2401.12345v2",
        abstract="We study structured codes for correcting quantum errors.",
    )


class _FakeArxivClient:
    def __init__(self, papers: list[ArxivPaper] | None = None) -> None:
        self.papers = papers if papers is not None else [_paper()]
        self.queries: list[str] = []

    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        stop_event: Any | None = None,
    ) -> list[ArxivPaper]:
        self.queries.append(query)
        return self.papers[:max_results]


class _CapturingAdapter:
    def __init__(self, answer: str = "依据 [arxiv-1] 回答。") -> None:
        self.payloads: list[dict[str, Any]] = []
        self.answer = answer

    def stream_call(self, capability: CapabilityRecord, run_context: Any, payload: dict[str, Any]):
        self.payloads.append(payload)
        yield StreamChunk(kind="delta", delta=self.answer)
        yield StreamChunk(kind="done")


def _service(
    tmp_path: Path,
    arxiv_service: ArxivSearchService,
    adapter: _CapturingAdapter,
) -> ChatService:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    repository = ConversationRepository(database)
    registry = CapabilityRegistry()
    registry.register(_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", adapter)
    return ChatService(
        repository=repository,
        gateway=gateway,
        arxiv_search_service=arxiv_service,
    )


def test_paper_prompt_dispatches_module_and_scrubs_private_text(tmp_path: Path) -> None:
    """工单 12：正文明确论文意图直达模块；查询只含脱敏后的最小词组。"""

    client = _FakeArxivClient()
    adapter = _CapturingAdapter()
    service = _service(tmp_path, ArxivSearchService(client=client), adapter)
    conversation = service.create_conversation("alice")
    user, assistant, _ = service.start_generation(
        "alice",
        conversation.conversation_id,
        "请找近三年量子纠错论文。私人文档：内部代号蓝鲸，密码=secret-123。",
    )

    assert user.module_id is None
    assert assistant.route.module_id == "paper"
    assert assistant.route is not None and assistant.route.is_paper_search
    assert assistant.arxiv_search is not None
    events = list(
        service.stream_generation(
            "alice",
            conversation.conversation_id,
            assistant.message_id,
            _context(),
            until_user_message_id=user.message_id,
        )
    )
    final = service.message_projection("alice", assistant.message_id)

    assert final is not None and final.status.value == "done"
    assert final.arxiv_search is not None
    assert final.arxiv_search.status.value == "success"
    assert client.queries and "内部代号蓝鲸" not in client.queries[0]
    assert "secret-123" not in client.queries[0]
    model_context = str(adapter.payloads[0])
    assert "Quantum Error Correction with Structured Codes" in model_context
    assert any(event.kind == "done" for event in events)


def test_empty_arxiv_results_fail_closed_without_model_fallback(tmp_path: Path) -> None:
    """论文模块空结果如实失败：不调用模型、不编造来源。"""

    adapter = _CapturingAdapter()
    client = _FakeArxivClient([])
    service = _service(tmp_path, ArxivSearchService(client=client), adapter)
    conversation = service.create_conversation("alice")
    user, assistant, _ = service.start_generation(
        "alice", conversation.conversation_id, "帮我找不存在领域的 arXiv 论文"
    )

    events = list(
        service.stream_generation(
            "alice",
            conversation.conversation_id,
            assistant.message_id,
            _context(),
            until_user_message_id=user.message_id,
        )
    )
    final = service.message_projection("alice", assistant.message_id)

    assert final is not None and final.status.value == "error"
    assert final.error_code == "arxiv_no_results"
    assert final.arxiv_search is not None and final.arxiv_search.status.value == "empty"
    assert client.queries
    assert adapter.payloads == []
    assert events[-1].kind == "error"


def test_paper_answer_without_citation_is_rejected(tmp_path: Path) -> None:
    """论文路由的答案必须带真实论文引用；无引用视为核验失败。"""

    adapter = _CapturingAdapter(answer="没有明确 arXiv 来源的回答。")
    client = _FakeArxivClient()
    service = _service(tmp_path, ArxivSearchService(client=client), adapter)
    conversation = service.create_conversation("alice")
    user, assistant, _ = service.start_generation(
        "alice", conversation.conversation_id, "搜索量子纠错论文"
    )

    events = list(
        service.stream_generation(
            "alice",
            conversation.conversation_id,
            assistant.message_id,
            _context(),
            until_user_message_id=user.message_id,
        )
    )
    final = service.message_projection("alice", assistant.message_id)

    assert final is not None and final.status.value == "error"
    assert final.error_code == "arxiv_citation_invalid"
    assert client.queries
    assert events[-1].kind == "error"


class _RateLimitArxivClient(_FakeArxivClient):
    """始终返回 429 分类错误的上游替身（记录查询与调用次数）。"""

    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        stop_event: Any | None = None,
    ) -> list[ArxivPaper]:
        self.queries.append(query)
        raise ArxivMcpError(
            "arxiv_rate_limit",
            "arXiv 请求过于频繁，请稍后重试。",
            upstream_status="http_429",
        )


def test_paper_prompt_retry_keeps_route_and_failure_state(
    tmp_path: Path,
) -> None:
    """论文路由的重试沿用同一路由与失败状态，不静默降级为普通聊天。"""

    client = _RateLimitArxivClient()
    adapter = _CapturingAdapter()
    service = _service(tmp_path, ArxivSearchService(client=client), adapter)
    conversation = service.create_conversation("alice")
    user, first, _ = service.start_generation(
        "alice", conversation.conversation_id, "搜索量子纠错论文"
    )

    first_events = list(
        service.stream_generation(
            "alice",
            conversation.conversation_id,
            first.message_id,
            _context(),
            until_user_message_id=user.message_id,
        )
    )
    first_final = service.message_projection("alice", first.message_id)
    assert first_final is not None and first_final.status.value == "error"
    assert first_final.error_code == "arxiv_rate_limit"
    assert first_final.arxiv_search is not None
    assert first_events[-1].kind == "error"
    assert len(client.queries) == 1

    _, retry, _ = service.retry_generation(
        "alice", conversation.conversation_id, first.message_id
    )
    assert retry.route is not None and retry.route.is_paper_search
    retry_events = list(
        service.stream_generation(
            "alice",
            conversation.conversation_id,
            retry.message_id,
            _context(),
            until_user_message_id=user.message_id,
        )
    )
    retry_final = service.message_projection("alice", retry.message_id)

    assert retry_final is not None and retry_final.status.value == "error"
    assert retry_final.error_code == "arxiv_rate_limit"
    assert retry_events[-1].kind == "error"
    assert len(client.queries) == 1
