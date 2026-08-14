"""Issue 23：学习模式在证据不足时的聊天编排集成测试。

Issue 02：本地覆盖不足 + 联网失败 → 带标注降级（本地引用保留、进度零
推进、画像编译注入）；本地冲突维持拒绝（零模型调用、画像只披露）。
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from bridges.ai import ModelGateway
from bridges.ai.adapters import StreamChunk
from bridges.ai.capability_registry import CapabilityRegistry
from bridges.chat.repository import ConversationRepository
from bridges.chat.service import ChatService
from bridges.chat.turn import ensure_unverified_teaching_prefix
from bridges.contracts.ai import CapabilityKind, CapabilityRecord
from bridges.contracts.chat import (
    ChatMessageProjection,
    ChatMessageStatus,
    ChatMode,
    ContextNoteState,
)
from bridges.contracts.profiles import FourDimension, FourDimensionConfidence
from bridges.contracts.projects import ObjectDomain
from bridges.contracts.teaching import TeachingTurnProjection
from bridges.contracts.workflows import RunContextEnvelope
from bridges.profiles import (
    FourDimensionProfileService,
    InMemoryFourDimensionProfileRepository,
)
from bridges.storage.database import BridgesDatabase
from bridges.web_search.client import DuckDuckGoClient, DuckDuckGoResults, WebSearchError
from bridges.web_search.contracts import (
    WebSearchPageClassification,
    WebSearchResult,
    WebSearchStatus,
)
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


def test_study_mode_search_failure_does_not_advance_plan_or_lesson(
    tmp_path: Path,
) -> None:
    """DDG 失败轮不创建/推进 teaching plan、lesson progress 或联网证据。"""
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

    class _FailingClient:
        def search(self, query: str) -> list[WebSearchResult]:
            raise WebSearchError("web_search_timeout", "联网搜索超时，请重试。")

    web_search = WebSearchService(client=_FailingClient())
    service = ChatService(
        repository=ConversationRepository(database),
        gateway=gateway,
        web_search_service=web_search,
    )
    conversation = service.create_conversation("alice", mode=ChatMode.STUDY)
    _, assistant = service.start_generation(
        "alice", conversation.conversation_id, "我想学习Transformer架构"
    )

    list(
        service.stream_generation(
            "alice",
            conversation.conversation_id,
            assistant.message_id,
            RunContextEnvelope(
                run_id="run-fail",
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
    assert final.web_search.error_code == "web_search_timeout"
    assert final.teaching is not None
    # 失败轮不创建教学计划/课次，也不写入联网证据。
    assert final.teaching.plan is None
    assert final.teaching.lesson is None
    assert final.teaching.evidence_gate.external_sources == []
    assert final.teaching.learning_progress is None
    assert final.teaching.can_answer_reliably is False
    assert final.content.count("本轮未联网核实：") == 1
    assert "[web-1]" not in final.content and "https://" not in final.content


def test_study_mode_retry_success_advances_plan_and_lesson_after_real_sources(
    tmp_path: Path,
) -> None:
    """重试成功后才允许基于真实来源创建计划与课次，新旧尝试可区分。"""
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

    class _FailFirstThenServe:
        def __init__(self) -> None:
            self.calls = 0

        def search(self, query: str) -> list[WebSearchResult]:
            self.calls += 1
            if self.calls == 1:
                raise WebSearchError("web_search_timeout", "联网搜索超时，请重试。")
            return DuckDuckGoResults(
                [
                    WebSearchResult(
                        result_id="web-1",
                        title="Transformer 架构公开资料",
                        site="example.com",
                        url="https://example.com/transformer",
                        snippet="介绍模型的层次化结构。",
                        content_summary=(
                            "Transformer architecture uses self-attention to build "
                            "an AI model architecture."
                        ),
                        fetched_at=datetime.now(UTC),
                        accessed_at=datetime.now(UTC),
                    )
                ],
                page_classification=WebSearchPageClassification.NORMAL_RESULTS,
                http_status_category="2xx",
            )

    web_search = WebSearchService(client=_FailFirstThenServe())
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
                run_id="run-retry",
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
    assert final.web_search.status == WebSearchStatus.SUCCESS
    assert final.web_search.results
    # 第二次尝试成功：attempts 保留两次调用，provider 尝试记录可区分。
    assert final.web_search.attempt_count == 2
    assert len(final.web_search.provider_attempts) == 2
    assert final.web_search.provider_attempts[0].result_code == "web_search_timeout"
    assert final.web_search.provider_attempts[1].result_code == "success"
    assert final.teaching is not None
    assert final.teaching.can_answer_reliably is True
    assert final.teaching.evidence_gate.external_sources
    assert final.teaching.learning_progress is not None
    assert final.teaching.learning_progress.source_message_id == assistant.message_id
    assert "本轮未联网核实" not in final.content
    assert "[reference:1]" in final.content


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


# ---------------------------------------------------------------------------
# Issue 02：本地覆盖不足 + 搜索失败 → 带标注降级；CONFLICT 维持拒绝。
# ---------------------------------------------------------------------------


class _LocalAnchorAdapter:
    """降级轮回答以本地命中为锚：保留 [reference:1]，附带网络垃圾供剥离。"""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def stream_call(
        self,
        capability: CapabilityRecord,
        run_context: object,
        payload: dict[str, Any],
    ):
        self.payloads.append(payload)
        yield StreamChunk(
            kind="delta",
            delta=(
                "热力学第二定律的熵表述说明能量自发过程的方向。"
                "[reference:1] [web-1] https://fake.example/source "
                "[arxiv-1] https://arxiv.org/abs/1"
            ),
        )
        yield StreamChunk(kind="done")


class _AlwaysTimeoutClient:
    def search(self, query: str) -> list[WebSearchResult]:
        raise WebSearchError("web_search_timeout", "联网搜索超时，请重试。")


class _PermissionDeniedClient:
    def search(self, query: str) -> list[WebSearchResult]:
        raise WebSearchError(
            "web_search_permission",
            "联网搜索权限未通过。",
            permission=True,
            retryable=False,
        )


class _EmptyResultsClient:
    def search(self, query: str) -> list[WebSearchResult]:
        return DuckDuckGoResults(
            [],
            page_classification=WebSearchPageClassification.NORMAL_RESULTS,
            http_status_category="2xx",
        )


class _VerifiedLocalAdapter:
    """可靠回答适配器：只引用真实本地来源，不携带网络垃圾。"""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def stream_call(
        self,
        capability: CapabilityRecord,
        run_context: object,
        payload: dict[str, Any],
    ):
        self.payloads.append(payload)
        yield StreamChunk(
            kind="delta",
            delta="热力学第二定律的熵增表述说明方向性。[reference:1]",
        )
        yield StreamChunk(kind="done")


def _teaching_service(
    tmp_path: Path,
    adapter: _LocalAnchorAdapter,
    *,
    database: object,
    retrieval: object | None = None,
    client: _AlwaysTimeoutClient | None = None,
    profiles: FourDimensionProfileService | None = None,
    web_search: WebSearchService | None = None,
) -> ChatService:
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
    web_search = web_search or WebSearchService(client=client or _AlwaysTimeoutClient())
    return ChatService(
        repository=ConversationRepository(database),  # type: ignore[arg-type]
        gateway=gateway,
        retrieval_service=retrieval,  # type: ignore[arg-type]
        web_search_service=web_search,
        four_dimension_profile_service=profiles,
    )


def _send_study(
    service: ChatService,
    account_id: str,
    conversation_id: str,
    content: str,
    *,
    use_profile: bool = True,
) -> tuple[ChatMessageProjection, list[object]]:
    _, assistant = service.start_generation(
        account_id,
        conversation_id,
        content,
        use_profile=use_profile,
    )
    events = list(
        service.stream_generation(
            account_id,
            conversation_id,
            assistant.message_id,
            RunContextEnvelope(
                run_id="run-issue02",
                account_id=account_id,
                project_id="conversation-1",
                workflow_name="chat",
                workflow_version="1",
                object_domain=ObjectDomain.PERSONAL_VAULT,
                submitted_at=datetime.now(UTC),
            ),
            use_profile=use_profile,
        )
    )
    final = service.message_projection(account_id, assistant.message_id)
    assert final is not None
    return final, events


def _seed_kb_material(
    env: dict[str, Any], account: str, filename: str, content: str
) -> None:
    """上传并完成摄取一条全局知识库材料（检索服务与聊天服务共用数据库）。"""
    stored = env["repository"].create_object(
        account, filename, content.encode(), media_type="text/plain"
    )
    env["ingestion"].enqueue(account, stored.object_id)
    env["ingestion"].process_pending()


def test_study_mode_insufficient_coverage_with_search_failure_degrades_with_local_anchor(
    tmp_path: Path,
) -> None:
    """本地 docx 命中但覆盖不足 + 搜索超时 → 真实 Qwen 带标注背景回答。

    终态可重载、引用仅真实本地来源、教学进度零推进。
    """
    from tests.retrieval.conftest import make_retrieval_env, make_storage

    env = make_retrieval_env(make_storage(tmp_path))
    account = env["account_a"]
    _seed_kb_material(env, account, "热力学讲义.txt", "热力学第二定律的一句话。")

    adapter = _LocalAnchorAdapter()
    service = _teaching_service(
        tmp_path,
        adapter,
        database=env["database"],
        retrieval=env["retrieval"],
        client=_AlwaysTimeoutClient(),
    )
    conversation = service.create_conversation(account, mode=ChatMode.STUDY)

    final, _events = _send_study(
        service, account, conversation.conversation_id, "我想学习热力学第二定律"
    )

    assert final.status == ChatMessageStatus.DONE
    assert final.teaching is not None
    teaching = TeachingTurnProjection.model_validate(final.teaching)
    assert teaching.evidence_gate.allow_model_knowledge is True
    assert teaching.can_answer_reliably is False
    assert teaching.evidence_gate.local_sources  # 真实本地命中仍在
    assert teaching.evidence_gate.external_sources == []
    assert final.web_search is not None
    assert final.web_search.status == WebSearchStatus.ERROR
    assert final.web_search.error_code == "web_search_timeout"
    assert final.content.startswith("本轮未联网核实：")
    assert "热力学第二定律的熵表述" in final.content
    # 引用只列真实本地来源：本地 [reference:1] 保留，网络引用与 URL 剥离。
    assert "[reference:1]" in final.content
    assert "[web-1]" not in final.content
    assert "[arxiv-1]" not in final.content
    assert "https://fake.example/source" not in final.content
    # 教学计划/课次/联网证据进度零推进。
    assert teaching.plan is None
    assert teaching.lesson is None
    assert teaching.learning_progress is None
    assert len(adapter.payloads) == 1
    payload_text = "\n".join(
        str(message.get("content", "")) for message in adapter.payloads[0]["messages"]
    )
    assert "允许使用模型一般知识" in payload_text
    assert "本轮未联网核实" in payload_text
    # 终态可重载：重新读取投影一致。
    reloaded = service.message_projection(account, final.message_id)
    assert reloaded is not None
    assert TeachingTurnProjection.model_validate(reloaded.teaching) == teaching


def test_study_mode_insufficient_coverage_reloads_terminal_and_keeps_search_attempts(
    tmp_path: Path,
) -> None:
    """降级终态刷新后投影一致，搜索尝试记录如实保留。"""
    from tests.retrieval.conftest import make_retrieval_env, make_storage

    env = make_retrieval_env(make_storage(tmp_path))
    account = env["account_a"]
    _seed_kb_material(env, account, "热力学讲义.txt", "热力学第二定律的一句话。")

    adapter = _LocalAnchorAdapter()
    service = _teaching_service(
        tmp_path,
        adapter,
        database=env["database"],
        retrieval=env["retrieval"],
        client=_AlwaysTimeoutClient(),
    )
    conversation = service.create_conversation(account, mode=ChatMode.STUDY)
    final, _ = _send_study(
        service, account, conversation.conversation_id, "我想学习热力学第二定律"
    )

    message_id = final.message_id
    assert final.web_search is not None
    assert final.web_search.attempt_count >= 1
    assert final.web_search.provider_attempts
    first_attempts = [
        attempt.model_dump(mode="json")
        for attempt in final.web_search.provider_attempts
    ]
    again = service.message_projection(account, message_id)
    assert again is not None
    assert again.status == ChatMessageStatus.DONE
    assert again.web_search is not None
    assert [
        attempt.model_dump(mode="json") for attempt in again.web_search.provider_attempts
    ] == first_attempts


def test_study_mode_degraded_turn_compiles_and_injects_profile_slice(
    tmp_path: Path,
) -> None:
    """降级轮编译并注入画像切片，「本次上下文说明」披露使用条数。"""
    from tests.retrieval.conftest import make_retrieval_env, make_storage

    env = make_retrieval_env(make_storage(tmp_path))
    account = env["account_a"]
    _seed_kb_material(env, account, "热力学讲义.txt", "热力学第二定律的一句话。")

    profiles = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=InMemoryFourDimensionProfileRepository(),
    )
    profiles.upsert_automatic_record(
        account,
        dimension=FourDimension.ACADEMIC_STATUS,
        content="硕士研究生在读",
        action="create",
        confidence=FourDimensionConfidence.HIGH,
    )

    adapter = _LocalAnchorAdapter()
    service = _teaching_service(
        tmp_path,
        adapter,
        database=env["database"],
        retrieval=env["retrieval"],
        client=_AlwaysTimeoutClient(),
        profiles=profiles,
    )
    conversation = service.create_conversation(account, mode=ChatMode.STUDY)
    final, _ = _send_study(
        service, account, conversation.conversation_id, "我想学习热力学第二定律"
    )

    assert final.context_note is not None
    assert final.context_note.state == ContextNoteState.READY
    assert final.context_note.profile_item_count >= 1
    assert "学业情况" in final.context_note.note
    payload_text = "\n".join(
        str(message.get("content", "")) for message in adapter.payloads[0]["messages"]
    )
    assert "以下是本轮为你参考的已授权信息" in payload_text
    assert "硕士研究生在读" in payload_text
    # 「当前水平假设」以画像为准（学业情况可用时）。
    teaching = TeachingTurnProjection.model_validate(final.teaching)
    assert "硕士研究生在读" in teaching.level_assumption


def test_study_mode_degraded_network_reference_fires_invariant_audit(
    tmp_path: Path,
) -> None:
    """降级轮模型输出携带网络引用时剥离并触发不变量告警。"""
    from bridges.contracts.observability import AuditAction, AuditResult
    from bridges.observability.service import ObservabilityService
    from tests.retrieval.conftest import make_retrieval_env, make_storage
    env = make_retrieval_env(make_storage(tmp_path))
    account = env["account_a"]
    _seed_kb_material(env, account, "热力学讲义.txt", "热力学第二定律的一句话。")

    observability = ObservabilityService()
    adapter = _LocalAnchorAdapter()  # 模型输出含 [web-1]/URL/arxiv 垃圾
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
        repository=ConversationRepository(env["database"]),
        gateway=gateway,
        retrieval_service=env["retrieval"],  # type: ignore[arg-type]
        web_search_service=WebSearchService(client=_AlwaysTimeoutClient()),
        observability_service=observability,
    )
    conversation = service.create_conversation(account, mode=ChatMode.STUDY)
    final, _ = _send_study(
        service, account, conversation.conversation_id, "我想学习热力学第二定律"
    )

    assert final.status == ChatMessageStatus.DONE
    assert "[web-1]" not in final.content
    assert "https://" not in final.content
    events = observability.list_audit_events(
        account_id=account, action=AuditAction.LEARNING_INVARIANT
    )
    assert len(events) == 1
    assert events[0].result == AuditResult.BLOCKED
    assert events[0].details == {"invariant": "degraded_network_reference"}


def test_study_mode_degraded_turn_without_profile_keeps_beginner_fallback(
    tmp_path: Path,
) -> None:
    """无画像时保留「暂按初学者处理」兜底且不构成阻塞。"""
    from tests.retrieval.conftest import make_retrieval_env, make_storage

    env = make_retrieval_env(make_storage(tmp_path))
    account = env["account_a"]
    _seed_kb_material(env, account, "热力学讲义.txt", "热力学第二定律的一句话。")

    adapter = _LocalAnchorAdapter()
    service = _teaching_service(
        tmp_path,
        adapter,
        database=env["database"],
        retrieval=env["retrieval"],
        client=_AlwaysTimeoutClient(),
    )
    conversation = service.create_conversation(account, mode=ChatMode.STUDY)
    final, _ = _send_study(
        service, account, conversation.conversation_id, "我想学习热力学第二定律"
    )

    teaching = TeachingTurnProjection.model_validate(final.teaching)
    assert "暂按初学者处理" in teaching.level_assumption
    assert final.status == ChatMessageStatus.DONE


def test_study_mode_degraded_turn_with_profile_disabled_keeps_contract(
    tmp_path: Path,
) -> None:
    """use_profile=False 时降级轮不编译、不注入画像，披露为 off 态。"""
    from tests.retrieval.conftest import make_retrieval_env, make_storage

    env = make_retrieval_env(make_storage(tmp_path))
    account = env["account_a"]
    _seed_kb_material(env, account, "热力学讲义.txt", "热力学第二定律的一句话。")

    profiles = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=InMemoryFourDimensionProfileRepository(),
    )
    profiles.upsert_automatic_record(
        account,
        dimension=FourDimension.ACADEMIC_STATUS,
        content="硕士研究生在读",
        action="create",
        confidence=FourDimensionConfidence.HIGH,
    )

    adapter = _LocalAnchorAdapter()
    service = _teaching_service(
        tmp_path,
        adapter,
        database=env["database"],
        retrieval=env["retrieval"],
        client=_AlwaysTimeoutClient(),
        profiles=profiles,
    )
    conversation = service.create_conversation(account, mode=ChatMode.STUDY)
    final, _ = _send_study(
        service,
        account,
        conversation.conversation_id,
        "我想学习热力学第二定律",
        use_profile=False,
    )

    assert final.context_note is not None
    assert final.context_note.state == ContextNoteState.OFF
    payload_text = "\n".join(
        str(message.get("content", "")) for message in adapter.payloads[0]["messages"]
    )
    assert "以下是本轮为你参考的已授权信息" not in payload_text
    assert "硕士研究生在读" not in payload_text
    teaching = TeachingTurnProjection.model_validate(final.teaching)
    assert "暂按初学者处理" in teaching.level_assumption


def test_study_mode_conflict_keeps_rejection_without_model_call_but_discloses_profile(
    tmp_path: Path,
) -> None:
    """本地 CONFLICT + 搜索失败 → 拒绝语义不变、零模型调用、画像只披露。"""
    from tests.retrieval.conftest import make_retrieval_env, make_storage

    env = make_retrieval_env(make_storage(tmp_path))
    account = env["account_a"]
    # 三份同主题但细节不同的材料：确定性 Embedding 下关键词/向量顶级
    # 命中不一致（实测固定触发 CONFLICT），且对学习目标长查询也成立。
    for content in [
        "热力学第二定律：熵永不减少。",
        "热力学第二定律的统计解释与熵增。",
        "热力学第二定律的工程应用与热机。",
    ]:
        _seed_kb_material(env, account, "材料.txt", content)

    profiles = FourDimensionProfileService(
        source_repository=None,  # type: ignore[arg-type]
        repository=InMemoryFourDimensionProfileRepository(),
    )
    profiles.upsert_automatic_record(
        account,
        dimension=FourDimension.ACADEMIC_STATUS,
        content="硕士研究生在读",
        action="create",
        confidence=FourDimensionConfidence.HIGH,
    )

    adapter = _LocalAnchorAdapter()
    service = _teaching_service(
        tmp_path,
        adapter,
        database=env["database"],
        retrieval=env["retrieval"],
        client=_AlwaysTimeoutClient(),
        profiles=profiles,
    )
    conversation = service.create_conversation(account, mode=ChatMode.STUDY)
    final, _ = _send_study(
        service, account, conversation.conversation_id, "我想学习热力学第二定律"
    )

    assert final.status == ChatMessageStatus.DONE
    teaching = TeachingTurnProjection.model_validate(final.teaching)
    assert teaching.evidence_gate.allow_model_knowledge is False
    assert teaching.can_answer_reliably is False
    assert "这轮我先不把不确定内容说成结论" in final.content
    assert teaching.plan is None
    assert teaching.lesson is None
    assert teaching.learning_progress is None
    # 拒绝轮零模型调用。
    assert adapter.payloads == []
    # 拒绝路径同样披露画像使用情况（编译披露、不注入生成）。
    assert final.context_note is not None
    assert final.context_note.state == ContextNoteState.READY
    assert final.context_note.profile_item_count >= 1
    # 「当前水平假设」保持既有兜底：拒绝轮只披露画像、不注入生成。
    assert "暂按初学者处理" in teaching.level_assumption


@pytest.mark.parametrize(
    "mode",
    ["error", "permission", "empty", "none"],
)
def test_study_mode_insufficient_coverage_all_search_failure_modes_degrades(
    tmp_path: Path, mode: str
) -> None:
    """覆盖不足 + 搜索 ERROR/PERMISSION/EMPTY/None 四种组合全部带标注降级。

    断言统一终态：前缀标注、引用仅本地、Qwen 调用一次、进度零推进。
    """
    from tests.retrieval.conftest import make_retrieval_env, make_storage

    env = make_retrieval_env(make_storage(tmp_path))
    account = env["account_a"]
    _seed_kb_material(env, account, "热力学讲义.txt", "热力学第二定律的一句话。")

    clients = {
        "error": _AlwaysTimeoutClient(),
        "permission": _PermissionDeniedClient(),
        "empty": _EmptyResultsClient(),
        "none": None,
    }
    adapter = _LocalAnchorAdapter()
    service = _teaching_service(
        tmp_path,
        adapter,
        database=env["database"],
        retrieval=env["retrieval"],
        client=clients[mode],  # type: ignore[arg-type]
        web_search=(
            None
            if mode == "none"
            else WebSearchService(client=clients[mode])  # type: ignore[arg-type]
        ),
    )
    conversation = service.create_conversation(account, mode=ChatMode.STUDY)
    final, _ = _send_study(
        service, account, conversation.conversation_id, "我想学习热力学第二定律"
    )

    assert final.status == ChatMessageStatus.DONE
    teaching = TeachingTurnProjection.model_validate(final.teaching)
    assert teaching.evidence_gate.allow_model_knowledge is True
    assert teaching.can_answer_reliably is False
    assert teaching.evidence_gate.local_sources
    assert teaching.evidence_gate.external_sources == []
    assert final.content.startswith("本轮未联网核实：")
    assert "[reference:1]" in final.content
    assert "[web-" not in final.content
    assert "https://" not in final.content
    assert teaching.plan is None
    assert teaching.lesson is None
    assert teaching.learning_progress is None
    assert len(adapter.payloads) == 1


def test_study_mode_degraded_retry_creates_distinguishable_new_attempt(
    tmp_path: Path,
) -> None:
    """降级终态重试：新尝试记录可区分，历史尝试原样保留。"""
    from tests.retrieval.conftest import make_retrieval_env, make_storage

    env = make_retrieval_env(make_storage(tmp_path))
    account = env["account_a"]
    _seed_kb_material(env, account, "热力学讲义.txt", "热力学第二定律的一句话。")

    adapter = _LocalAnchorAdapter()
    service = _teaching_service(
        tmp_path,
        adapter,
        database=env["database"],
        retrieval=env["retrieval"],
        client=_AlwaysTimeoutClient(),
    )
    conversation = service.create_conversation(account, mode=ChatMode.STUDY)
    final, _ = _send_study(
        service, account, conversation.conversation_id, "我想学习热力学第二定律"
    )
    assert final.status == ChatMessageStatus.DONE
    assert final.teaching is not None
    assert TeachingTurnProjection.model_validate(final.teaching).can_answer_reliably is False
    first_web = final.web_search
    assert first_web is not None
    assert first_web.status == WebSearchStatus.ERROR
    first_attempts = [
        attempt.model_dump(mode="json") for attempt in first_web.provider_attempts
    ]
    assert first_attempts

    # 换用可用检索源重试：新助手尝试沿用同一用户消息，尝试号递增。
    class _NowWorkingClient:
        def search(self, query: str) -> list[WebSearchResult]:
            return DuckDuckGoResults(
                [
                    WebSearchResult(
                        result_id="web-ok",
                        title="热力学第二定律公开资料",
                        site="example.com",
                        url="https://example.com/thermo",
                        snippet="熵增表述说明方向性。",
                        content_summary="热力学第二定律的熵增表述。",
                        fetched_at=datetime.now(UTC),
                        accessed_at=datetime.now(UTC),
                    )
                ],
                page_classification=WebSearchPageClassification.NORMAL_RESULTS,
                http_status_category="2xx",
            )

    retry_service = _teaching_service(
        tmp_path,
        _VerifiedLocalAdapter(),
        database=env["database"],
        retrieval=env["retrieval"],
        client=_NowWorkingClient(),  # type: ignore[arg-type]
    )
    _, retried = retry_service.retry_generation(
        account, conversation.conversation_id, final.message_id
    )
    events = list(
        retry_service.stream_generation(
            account,
            conversation.conversation_id,
            retried.message_id,
            RunContextEnvelope(
                run_id="run-issue02-retry",
                account_id=account,
                project_id="conversation-1",
                workflow_name="chat",
                workflow_version="1",
                object_domain=ObjectDomain.PERSONAL_VAULT,
                submitted_at=datetime.now(UTC),
            ),
            use_profile=True,
        )
    )
    assert [event.kind for event in events if event.kind not in {"stage"}] == [
        "delta",
        "done",
    ]
    retried_final = retry_service.message_projection(account, retried.message_id)
    assert retried_final is not None
    assert retried_final.status == ChatMessageStatus.DONE
    assert retried_final.attempt_number == 2
    retried_teaching = TeachingTurnProjection.model_validate(retried_final.teaching)
    assert retried_teaching.can_answer_reliably is True
    assert retried_final.web_search is not None
    assert retried_final.web_search.status == WebSearchStatus.SUCCESS
    assert retried_final.web_search.provider_attempts
    assert retried_final.web_search.provider_attempts[-1].result_code == "success"
    # 历史尝试原样保留，投影一致。
    old = retry_service.message_projection(account, final.message_id)
    assert old is not None
    assert old.attempt_number == 1
    assert old.status == ChatMessageStatus.DONE
    assert old.web_search is not None
    assert [
        attempt.model_dump(mode="json") for attempt in old.web_search.provider_attempts
    ] == first_attempts
