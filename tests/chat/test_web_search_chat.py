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


def test_search_failure_continues_with_unverified_model_knowledge(tmp_path: Path) -> None:
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
    assert final.status.value == "done"
    assert final.error_code is None
    assert final.web_search is not None
    assert final.web_search.status == WebSearchStatus.ERROR
    assert adapter.payloads
    model_context = str(adapter.payloads[0])
    assert "本轮未联网核实" in model_context
    assert "https://" not in model_context
    assert "[web-" not in model_context
    assert events[-1].kind == "done"


def test_consecutive_failures_keep_last_error_and_unverified_fallback(
    tmp_path: Path,
) -> None:
    """两次尝试都失败时：终态保留最后一次真实错误，正文继续未联网核实回答。"""

    class _AlwaysFailingClient:
        def __init__(self) -> None:
            self.calls = 0

        def search(self, query: str) -> list[WebSearchResult]:
            self.calls += 1
            raise WebSearchError("web_search_connect", "当前无法连接公网搜索。")

    adapter = _CapturingAdapter()
    client = _AlwaysFailingClient()
    service, _ = _service(tmp_path, WebSearchService(client=client), adapter)
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
    assert final.status.value == "done"
    assert final.web_search is not None
    assert final.web_search.status == WebSearchStatus.ERROR
    assert final.web_search.error_code == "web_search_connect"
    assert client.calls == 2, "连续失败最多两次 Tavily 请求"
    assert final.web_search.attempt_count == 2
    assert len(final.web_search.provider_attempts) == 2
    assert [a.attempt_number for a in final.web_search.provider_attempts] == [1, 2]
    model_context = str(adapter.payloads[0])
    assert "本轮未联网核实" in model_context
    assert "https://" not in model_context
    assert events[-1].kind == "done"


def test_unexpected_exception_degrades_to_internal_error_and_model_knowledge(
    tmp_path: Path,
) -> None:
    class _BoomClient:
        def search(self, query: str) -> list[WebSearchResult]:
            raise RuntimeError("内部爆炸")

    adapter = _CapturingAdapter()
    service, _ = _service(tmp_path, WebSearchService(client=_BoomClient()), adapter)
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
    assert final.status.value == "done"
    assert final.web_search is not None
    assert final.web_search.status == WebSearchStatus.ERROR
    assert final.web_search.error_code == "web_search_internal"
    assert final.web_search.can_retry is False
    model_context = str(adapter.payloads[0])
    assert "本轮未联网核实" in model_context
    assert events[-1].kind == "done"


def test_configuration_error_is_terminal_permission_state_without_retry(
    tmp_path: Path,
) -> None:
    """Issue 01：401/403 配置错误在聊天终态可区分、不重试、不暗示稍后重试。"""

    class _ConfigClient:
        def __init__(self) -> None:
            self.calls = 0

        def search(self, query: str) -> list[WebSearchResult]:
            self.calls += 1
            raise WebSearchError(
                "web_search_configuration",
                "搜索凭据无效（Tavily API Key 未通过校验），请检查 Tavily API Key 配置。",
                permission=True,
                retryable=False,
            )

    adapter = _CapturingAdapter()
    client = _ConfigClient()
    service, _ = _service(tmp_path, WebSearchService(client=client), adapter)
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
    assert final.status.value == "done"
    assert final.web_search is not None
    assert final.web_search.status == WebSearchStatus.PERMISSION
    assert final.web_search.error_code == "web_search_configuration"
    assert final.web_search.can_retry is False
    assert client.calls == 1, "配置错误不得自动重试"
    assert "Tavily API Key" in (final.web_search.error_message or "")
    assert "重试" not in (final.web_search.error_message or "")
    model_context = str(adapter.payloads[0])
    assert "本轮未联网核实" in model_context
    assert "https://" not in model_context
    assert events[-1].kind == "done"


def test_retry_after_failure_reruns_search_and_distinguishes_attempts(
    tmp_path: Path,
) -> None:
    """失败后用户重试：重新执行搜索，新旧尝试记录可区分且终态成功。"""

    class _FailOnceClient:
        def __init__(self) -> None:
            self.calls = 0

        def search(self, query: str) -> list[WebSearchResult]:
            self.calls += 1
            if self.calls == 1:
                raise WebSearchError("web_search_timeout", "联网搜索超时，请重试。")
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

    adapter = _CapturingAdapter()
    client = _FailOnceClient()
    service, _ = _service(tmp_path, WebSearchService(client=client), adapter)
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
    assert final.status.value == "done"
    assert final.web_search is not None
    assert final.web_search.status == WebSearchStatus.SUCCESS
    assert client.calls == 2
    assert final.web_search.attempt_count == 2
    assert [a.attempt_number for a in final.web_search.provider_attempts] == [1, 2]
    assert final.web_search.provider_attempts[0].result_code == "web_search_timeout"
    assert final.web_search.provider_attempts[1].result_code == "success"
    assert final.web_search.provider_attempts[0].retry_planned is True
    assert "https://example.com/quantum" in str(adapter.payloads[0])


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
    # Issue 03：搜索失败不阻断回答——以带标注的模型知识降级完成整轮。
    assert final.status.value == "done"
    assert final.web_search is not None
    assert final.web_search.status == WebSearchStatus.ERROR
    assert final.context_note is not None
    assert final.context_note.state.value == "ready"
    assert final.context_note.profile_item_count == 1
    assert final.context_note.material_categories == []
    assert "你已授权的用户背景信息" in final.context_note.note
    assert events[-1].kind == "done"
    assert adapter.payloads
    assert "本轮未联网核实" in str(adapter.payloads[0])


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
