"""Issue 06：聊天文本路由与 arXiv 生成链路集成测试。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bridges.arxiv_mcp.contracts import ArxivSearchStatus
from bridges.arxiv_mcp.service import ArxivSearchService
from bridges.chat.service import ChatService
from bridges.web_search.service import SearchPlan
from tests.chat.test_arxiv_search_chat import (
    _CapturingAdapter,
    _FakeArxivClient,
    _service,
    _context,
)


class _UnexpectedWebSearch:
    """论文路由不应执行的通用网络搜索替身。"""

    def __init__(self) -> None:
        self.plan_calls = 0
        self.search_calls = 0

    def plan(self, content: str, mode: Any, *, force: bool = False) -> SearchPlan:
        self.plan_calls += 1
        return SearchPlan(True, "unexpected", "unexpected")

    def search(self, account_id: str, plan: SearchPlan, *, stop_event: Any = None) -> None:
        self.search_calls += 1
        raise AssertionError("paper route must not call web search")


def test_paper_route_is_persisted_before_search_and_suppresses_web(tmp_path: Path) -> None:
    client = _FakeArxivClient()
    service = _service(tmp_path, ArxivSearchService(client=client), _CapturingAdapter())
    unexpected_web = _UnexpectedWebSearch()
    service._turn._web_search = unexpected_web  # noqa: SLF001 - explicit routing seam
    conversation = service.create_conversation("alice")

    user, assistant = service.start_generation(
        "alice",
        conversation.conversation_id,
        "Find at most 3 papers about quantum error correction.",
    )

    assert user.route is not None
    assert assistant.route == user.route
    assert assistant.route.main_capability.value == "paper_search"
    assert assistant.arxiv_search is not None
    assert assistant.web_search is None
    persisted = service._repo.get_message("alice", assistant.message_id)  # noqa: SLF001
    assert (
        persisted is not None
        and persisted.route == assistant.route.model_dump(mode="json")
    )
    assert service.message_projection("other-account", assistant.message_id) is None

    events = list(
        service.stream_generation(
            "alice",
            conversation.conversation_id,
            assistant.message_id,
            _context(),
            until_user_message_id=user.message_id,
        )
    )

    assert any(event.kind == "done" for event in events)
    assert len(client.queries) == 1
    assert unexpected_web.plan_calls == 0
    assert unexpected_web.search_calls == 0


def test_ambiguous_paper_request_asks_before_any_side_effect(tmp_path: Path) -> None:
    client = _FakeArxivClient()
    adapter = _CapturingAdapter()
    service: ChatService = _service(tmp_path, ArxivSearchService(client=client), adapter)
    conversation = service.create_conversation("alice")

    user, assistant = service.start_generation(
        "alice", conversation.conversation_id, "Please help me find papers"
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
    assert final is not None and final.content
    assert final.route is not None and final.route.status.value == "clarify"
    assert final.arxiv_search is None
    assert client.queries == []
    assert adapter.payloads == []
    assert events[-1].kind == "done"


def test_retry_reuses_successful_paper_snapshot_without_requery(tmp_path: Path) -> None:
    client = _FakeArxivClient()
    service = _service(tmp_path, ArxivSearchService(client=client), _CapturingAdapter())
    conversation = service.create_conversation("alice")
    user, first = service.start_generation(
        "alice", conversation.conversation_id, "Find papers about quantum error correction."
    )

    list(
        service.stream_generation(
            "alice",
            conversation.conversation_id,
            first.message_id,
            _context(),
            until_user_message_id=user.message_id,
        )
    )
    assert client.queries == ["quantum error correction"]

    _, retry = service.retry_generation("alice", conversation.conversation_id, first.message_id)
    assert retry.arxiv_search is not None
    assert retry.arxiv_search.status == ArxivSearchStatus.SUCCESS
    list(
        service.stream_generation(
            "alice",
            conversation.conversation_id,
            retry.message_id,
            _context(),
            until_user_message_id=user.message_id,
        )
    )

    final = service.message_projection("alice", retry.message_id)
    assert final is not None and final.arxiv_search is not None
    assert final.arxiv_search.status == ArxivSearchStatus.SUCCESS
    assert client.queries == ["quantum error correction"]
