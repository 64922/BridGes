"""Issue 21：公网搜索接入聊天生成的确定性测试。"""

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
from bridges.contracts.profiles import ManualAssertionCreateRequest, ProfileDimension
from bridges.contracts.projects import ObjectDomain
from bridges.contracts.workflows import RunContextEnvelope
from bridges.profiles import ProfileService
from bridges.profiles.sqlite_repository import SqliteProfileRepository
from bridges.storage.database import BridgesDatabase
from bridges.web_search.client import WebSearchError
from bridges.web_search.contracts import WebSearchResult, WebSearchStatus
from bridges.web_search.service import WebSearchService


def _context() -> RunContextEnvelope:
    return RunContextEnvelope(
        run_id="run-web-1",
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


class _CapturingAdapter:
    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def stream_call(self, capability: CapabilityRecord, run_context: Any, payload: dict[str, Any]):
        self.payloads.append(payload)
        yield StreamChunk(kind="delta", delta="基于公开来源回答 [web-1]。")
        yield StreamChunk(kind="done")


class _FakeSearchClient:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def search(self, query: str) -> list[WebSearchResult]:
        self.queries.append(query)
        return [
            WebSearchResult(
                result_id="web-1",
                title="量子计算公开报道",
                site="example.com",
                url="https://example.com/quantum",
                snippet="公开摘要。",
                accessed_at=datetime.now(UTC),
            )
        ]


def _service(
    tmp_path: Path,
    search_service: WebSearchService,
    adapter: _CapturingAdapter,
    *,
    with_profile: bool = False,
) -> tuple[ChatService, ProfileService | None]:
    database = BridgesDatabase(tmp_path / "bridges.db")
    database.initialize()
    repository = ConversationRepository(database)
    registry = CapabilityRegistry()
    registry.register(_capability())
    gateway = ModelGateway(registry)
    gateway.register_adapter("qwen_text_chat", "1", adapter)
    profile_service = (
        ProfileService(SqliteProfileRepository(database)) if with_profile else None
    )
    return ChatService(
        repository=repository,
        gateway=gateway,
        web_search_service=search_service,
        profile_service=profile_service,
    ), profile_service


def _seed_profile(profile: ProfileService) -> None:
    profile.manual_create_assertion(
        "alice",
        ManualAssertionCreateRequest(
            dimension=ProfileDimension.EXPRESSION_HABIT,
            value_or_rule="喜欢简洁回答",
            applicable_scenes=["companion"],
        ),
    )


def test_explicit_search_is_persisted_and_only_public_results_reach_model(
    tmp_path: Path,
) -> None:
    client = _FakeSearchClient()
    adapter = _CapturingAdapter()
    service, _ = _service(tmp_path, WebSearchService(client=client), adapter)
    conversation = service.create_conversation("alice")
    user, assistant = service.start_generation(
        "alice",
        conversation.conversation_id,
        "请联网核实量子计算最新进展。私人文档：内部代号蓝鲸，密码=secret-123。",
    )

    assert assistant.web_search is not None
    assert assistant.web_search.status == WebSearchStatus.LOADING
    assert user.web_search is None
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

    assert final is not None
    assert final.status.value == "done"
    assert final.web_search is not None
    assert final.web_search.status == WebSearchStatus.SUCCESS
    assert client.queries and "内部代号蓝鲸" not in client.queries[0]
    assert "secret-123" not in client.queries[0]
    model_context = str(adapter.payloads[0])
    assert "https://example.com/quantum" in model_context
    assert any(event.kind == "done" for event in events)


def test_search_failure_is_fail_closed_and_does_not_call_model(tmp_path: Path) -> None:
    class _FailingClient:
        def search(self, query: str) -> list[WebSearchResult]:
            raise WebSearchError("web_search_timeout", "联网搜索超时，请重试。")

    adapter = _CapturingAdapter()
    service, _ = _service(tmp_path, WebSearchService(client=_FailingClient()), adapter)
    conversation = service.create_conversation("alice")
    user, assistant = service.start_generation(
        "alice", conversation.conversation_id, "请联网核实这个说法是否属实"
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

    assert final is not None
    assert final.status.value == "error"
    assert final.error_code == "web_search_timeout"
    assert final.web_search is not None
    assert final.web_search.status == WebSearchStatus.ERROR
    assert adapter.payloads == []
    assert events[-1].kind == "error"


def test_profile_ready_coexists_with_search_failure(tmp_path: Path) -> None:
    class _FailingClient:
        def search(self, query: str) -> list[WebSearchResult]:
            raise WebSearchError("web_search_timeout", "联网搜索超时，请重试。")

    adapter = _CapturingAdapter()
    service, profile = _service(
        tmp_path,
        WebSearchService(client=_FailingClient()),
        adapter,
        with_profile=True,
    )
    assert profile is not None
    _seed_profile(profile)
    conversation = service.create_conversation("alice")
    user, assistant = service.start_generation(
        "alice", conversation.conversation_id, "请联网核实这个说法是否属实"
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
    assert final is not None
    assert final.status.value == "error"
    assert final.context_note is not None
    assert final.context_note.state.value == "ready"
    assert final.context_note.profile_item_count == 1
    assert final.context_note.material_categories == []
    assert "你已授权的用户背景信息" in final.context_note.note
    assert events[-1].kind == "error"


def test_profile_count_excludes_successful_web_sources(tmp_path: Path) -> None:
    adapter = _CapturingAdapter()
    service, profile = _service(
        tmp_path,
        WebSearchService(client=_FakeSearchClient()),
        adapter,
        with_profile=True,
    )
    assert profile is not None
    _seed_profile(profile)
    conversation = service.create_conversation("alice")
    user, assistant = service.start_generation(
        "alice", conversation.conversation_id, "请联网核实量子计算最新进展"
    )

    list(
        service.stream_generation(
            "alice",
            conversation.conversation_id,
            assistant.message_id,
            _context(),
            until_user_message_id=user.message_id,
        )
    )

    final = service.message_projection("alice", assistant.message_id)
    assert final is not None
    assert final.context_note is not None
    assert final.context_note.profile_item_count == 1
    assert final.context_note.material_categories == ["联网来源"]


def test_successful_web_source_coexists_with_profile_off(tmp_path: Path) -> None:
    adapter = _CapturingAdapter()
    service, profile = _service(
        tmp_path,
        WebSearchService(client=_FakeSearchClient()),
        adapter,
        with_profile=True,
    )
    assert profile is not None
    _seed_profile(profile)
    conversation = service.create_conversation("alice")
    user, assistant = service.start_generation(
        "alice", conversation.conversation_id, "请联网核实量子计算最新进展"
    )

    list(
        service.stream_generation(
            "alice",
            conversation.conversation_id,
            assistant.message_id,
            _context(),
            until_user_message_id=user.message_id,
            use_profile=False,
        )
    )

    final = service.message_projection("alice", assistant.message_id)
    assert final is not None
    assert final.status.value == "done"
    assert final.web_search is not None
    assert final.web_search.status == WebSearchStatus.SUCCESS
    assert final.context_note is not None
    assert final.context_note.state.value == "off"
    assert final.context_note.profile_item_count == 0
    assert final.context_note.material_categories == ["联网来源"]
    assert "你已授权的用户背景信息" in final.context_note.note
    assert "关闭" in final.context_note.note


def test_search_answer_without_valid_citation_is_rejected(tmp_path: Path) -> None:
    class _UncitedAdapter(_CapturingAdapter):
        def stream_call(
            self, capability: CapabilityRecord, run_context: Any, payload: dict[str, Any]
        ):
            self.payloads.append(payload)
            yield StreamChunk(kind="delta", delta="没有明确来源的回答。")
            yield StreamChunk(kind="done")

    adapter = _UncitedAdapter()
    service, _ = _service(tmp_path, WebSearchService(client=_FakeSearchClient()), adapter)
    conversation = service.create_conversation("alice")
    user, assistant = service.start_generation(
        "alice", conversation.conversation_id, "请联网核实量子计算最新进展"
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
    assert final is not None
    assert final.status.value == "error"
    assert final.error_code == "web_search_citation_invalid"
    assert final.web_search is not None
    assert final.web_search.status == WebSearchStatus.ERROR
    assert events[-1].kind == "error"


def test_non_search_message_does_not_call_provider(tmp_path: Path) -> None:
    client = _FakeSearchClient()
    adapter = _CapturingAdapter()
    service, _ = _service(tmp_path, WebSearchService(client=client), adapter)
    conversation = service.create_conversation("alice")
    user, assistant = service.start_generation(
        "alice", conversation.conversation_id, "陪我聊聊我的心情"
    )

    list(
        service.stream_generation(
            "alice",
            conversation.conversation_id,
            assistant.message_id,
            _context(),
            until_user_message_id=user.message_id,
        )
    )

    final = service.message_projection("alice", assistant.message_id)
    assert final is not None and final.web_search is None
    assert client.queries == []


def test_stop_persists_cancelled_search_state(tmp_path: Path) -> None:
    client = _FakeSearchClient()
    adapter = _CapturingAdapter()
    service, _ = _service(tmp_path, WebSearchService(client=client), adapter)
    conversation = service.create_conversation("alice")
    _, assistant = service.start_generation(
        "alice", conversation.conversation_id, "请联网核实量子计算最新进展"
    )

    stopped = service.stop_generation("alice", conversation.conversation_id, assistant.message_id)

    assert stopped.status.value == "stopped"
    assert stopped.web_search is not None
    assert stopped.web_search.status == WebSearchStatus.CANCELLED
    assert stopped.web_search.can_cancel is False
    assert client.queries == []
