"""未选择论文模块时，普通聊天不进入 arXiv 重试或 stale 工作流。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bridges.arxiv_mcp.service import ArxivSearchService
from tests.chat.test_arxiv_search_chat import _CapturingAdapter, _context, _service


class _TrackingArxivClient:
    def __init__(self) -> None:
        self.calls = 0

    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        stop_event: Any | None = None,
    ) -> list[Any]:
        self.calls += 1
        return []


def test_unselected_paper_prompt_does_not_retry_arxiv(tmp_path: Path) -> None:
    client = _TrackingArxivClient()
    service = _service(
        tmp_path,
        ArxivSearchService(client=client),  # type: ignore[arg-type]
        _CapturingAdapter(),
    )
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

    assert final is not None and final.status.value == "done"
    assert final.route is not None
    assert final.route.main_capability.value == "ordinary_chat"
    assert final.arxiv_search is None
    assert client.calls == 0
    assert events[-1].kind == "done"


def test_retry_of_unselected_paper_prompt_stays_ordinary(tmp_path: Path) -> None:
    client = _TrackingArxivClient()
    service = _service(
        tmp_path,
        ArxivSearchService(client=client),  # type: ignore[arg-type]
        _CapturingAdapter(),
    )
    conversation = service.create_conversation("alice")
    user, first, _ = service.start_generation(
        "alice", conversation.conversation_id, "搜索量子纠错论文"
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

    _, retry, _ = service.retry_generation("alice", conversation.conversation_id, first.message_id)
    events = list(
        service.stream_generation(
            "alice",
            conversation.conversation_id,
            retry.message_id,
            _context(),
            until_user_message_id=user.message_id,
        )
    )
    final = service.message_projection("alice", retry.message_id)

    assert final is not None and final.status.value == "done"
    assert final.route is not None
    assert final.route.main_capability.value == "ordinary_chat"
    assert final.arxiv_search is None
    assert client.calls == 0
    assert events[-1].kind == "done"
