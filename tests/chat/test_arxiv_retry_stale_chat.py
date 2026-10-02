"""改工单 12：论文路由的 arXiv 生命周期——搜索一次、成功结果可复用。

正文明确的论文请求直达论文模块；首次搜索成功后，重试沿用同一路由与
成功快照，不产生第二次 arXiv 调用，也不静默降级为普通聊天。
"""

from __future__ import annotations

from pathlib import Path

from bridges.arxiv_mcp.service import ArxivSearchService
from tests.chat.test_arxiv_search_chat import (
    _CapturingAdapter,
    _context,
    _FakeArxivClient,
    _service,
)


def test_paper_prompt_searches_once_and_retry_reuses_result(tmp_path: Path) -> None:
    client = _FakeArxivClient()
    service = _service(tmp_path, ArxivSearchService(client=client), _CapturingAdapter())
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

    assert first_final is not None and first_final.status.value == "done"
    assert first_final.route is not None and first_final.route.is_paper_search
    assert first_final.arxiv_search is not None
    assert first_final.arxiv_search.status.value == "success"
    assert len(client.queries) == 1
    assert first_events[-1].kind == "done"

    _, retry, _ = service.retry_generation(
        "alice", conversation.conversation_id, first.message_id
    )
    retry_events = list(
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
    assert final.route is not None and final.route.is_paper_search
    assert final.arxiv_search is not None
    assert final.arxiv_search.status.value == "success"
    assert len(client.queries) == 1
    assert retry_events[-1].kind == "done"
