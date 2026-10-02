"""Issue 06 / 改进工单 12：论文文本路由与 arXiv 生成链路集成测试。

工单 12 起，正文明确的论文请求（含英文）直达论文模块；含糊主题只问
一个必要问题、不先检索猜测领域；通用公网搜索绝不与论文路由同时执行。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bridges.arxiv_mcp.service import ArxivSearchService
from bridges.chat.service import ChatService
from bridges.web_search.service import SearchPlan
from tests.chat.test_arxiv_search_chat import (
    _CapturingAdapter,
    _context,
    _FakeArxivClient,
    _service,
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


def test_body_intent_paper_request_dispatches_and_searches(tmp_path: Path) -> None:
    client = _FakeArxivClient()
    service = _service(tmp_path, ArxivSearchService(client=client), _CapturingAdapter())
    unexpected_web = _UnexpectedWebSearch()
    service._turn._web_search = unexpected_web  # noqa: SLF001 - explicit routing seam
    conversation = service.create_conversation("alice")

    user, assistant, _ = service.start_generation(
        "alice",
        conversation.conversation_id,
        "Find at most 3 papers about quantum error correction.",
    )

    assert user.module_id is None
    assert assistant.route.module_id == "paper"
    assert user.route is not None and user.route == assistant.route
    assert assistant.route is not None
    assert assistant.route.is_paper_search
    assert assistant.route.route_source == "body_intent"
    assert assistant.route.web_search_allowed is False
    assert assistant.arxiv_search is not None
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
    assert client.queries
    assert unexpected_web.search_calls == 0


def test_ambiguous_paper_request_asks_before_any_side_effect(tmp_path: Path) -> None:
    client = _FakeArxivClient()
    adapter = _CapturingAdapter()
    service: ChatService = _service(tmp_path, ArxivSearchService(client=client), adapter)
    conversation = service.create_conversation("alice")

    user, assistant, _ = service.start_generation(
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
    assert final.route.clarification_question == final.content
    assert final.arxiv_search is None
    assert client.queries == []
    assert adapter.payloads == []
    assert events[-1].kind == "done"


def test_empty_chinese_paper_topic_clarifies_without_arxiv_call(tmp_path: Path) -> None:
    client = _FakeArxivClient()
    adapter = _CapturingAdapter()
    service: ChatService = _service(tmp_path, ArxivSearchService(client=client), adapter)
    conversation = service.create_conversation("alice")

    user, assistant, _ = service.start_generation(
        "alice", conversation.conversation_id, "给我找几篇论文"
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
    assert final.route is not None and final.route.status.value == "clarify"
    assert final.content == final.route.clarification_question
    assert final.arxiv_search is None
    assert client.queries == []
    assert adapter.payloads == []
    assert events[-1].kind == "done"


def test_retry_keeps_paper_route_and_reuses_search(tmp_path: Path) -> None:
    client = _FakeArxivClient()
    service = _service(tmp_path, ArxivSearchService(client=client), _CapturingAdapter())
    conversation = service.create_conversation("alice")
    user, first, _ = service.start_generation(
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
    assert first.route is not None
    assert first.route.is_paper_search
    assert len(client.queries) == 1

    _, retry, _ = service.retry_generation("alice", conversation.conversation_id, first.message_id)
    assert retry.route is not None
    assert retry.route.is_paper_search
    assert retry.route.route_source == "body_intent"
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
    assert final.arxiv_search.status.value == "success"
    assert len(client.queries) == 1
