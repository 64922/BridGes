"""Issue 04：arXiv 自动重试与 stale 兜底的聊天级 SSE 终态投影测试。

覆盖两条端到端链路：瞬时抖动→预算内自动重试→成功（``attempt_count=2``
且上游调用数一致）；上游持续故障→stale 兜底→标注（``stale=True``）。
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bridges.arxiv_mcp.cache import ArxivResultCache
from bridges.arxiv_mcp.client import ArxivMcpError
from bridges.arxiv_mcp.contracts import ArxivPaper, ArxivSearchStatus
from bridges.arxiv_mcp.guard import ArxivCooldown, ArxivThrottle
from bridges.arxiv_mcp.service import ArxivSearchService
from tests.chat.test_arxiv_search_chat import _CapturingAdapter, _context, _service


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


class _OnceFailingClient:
    """第一次调用抛超时，随后成功——模拟瞬时抖动。"""

    def __init__(self) -> None:
        self.calls = 0

    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        stop_event: Any | None = None,
        deadline: float | None = None,
    ) -> list[ArxivPaper]:
        self.calls += 1
        if self.calls == 1:
            raise ArxivMcpError(
                "arxiv_timeout", "arXiv 搜索超时，请重试。", upstream_status="timeout"
            )
        return [_paper()]


class _PersistentTimeoutClient:
    """始终抛超时——模拟上游持续故障。"""

    def __init__(self, papers: list[ArxivPaper] | None = None) -> None:
        self.calls = 0
        self.papers = papers if papers is not None else [_paper()]

    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        stop_event: Any | None = None,
        deadline: float | None = None,
    ) -> list[ArxivPaper]:
        self.calls += 1
        raise ArxivMcpError(
            "arxiv_timeout", "arXiv 搜索超时，请重试。", upstream_status="timeout"
        )


class _SwitchingClient:
    """首次搜索成功（填充缓存），此后持续失败。"""

    def __init__(self) -> None:
        self.calls = 0
        self._inner = _PersistentTimeoutClient()

    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        stop_event: Any | None = None,
        deadline: float | None = None,
    ) -> list[ArxivPaper]:
        self.calls += 1
        if self.calls == 1:
            return [_paper()]
        return self._inner.search(
            query,
            max_results=max_results,
            stop_event=stop_event,
            deadline=deadline,
        )


def _fast_reliability_service(client: object) -> ArxivSearchService:
    """短 TTL/冷却/零节流的确定性服务（配合真实时钟）。"""
    return ArxivSearchService(
        client=client,  # type: ignore[arg-type]
        cache=ArxivResultCache(ttl_seconds=0.05),
        throttle=ArxivThrottle(min_interval=0.0),
        cooldown=ArxivCooldown(cooldown_seconds=0.05),
    )


def test_chat_retry_succeeds_after_transient_timeout(
    tmp_path: Path,
) -> None:
    """瞬时抖动→预算内自动重试→成功：SSE 终态 done，attempt_count=2。"""
    client = _OnceFailingClient()
    adapter = _CapturingAdapter()
    service = _service(tmp_path, ArxivSearchService(client=client), adapter)
    conversation = service.create_conversation("alice")
    user, assistant = service.start_generation(
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
    assert final.arxiv_search is not None
    assert final.arxiv_search.status == ArxivSearchStatus.SUCCESS
    assert final.arxiv_search.attempt_count == 2  # 首次超时 + 自动重发
    assert final.arxiv_search.stale is False
    assert client.calls == 2  # 上游调用数与 attempt_count 一致
    assert events[-1].kind == "done"


def test_chat_stale_fallback_marks_terminal_projection(
    tmp_path: Path,
) -> None:
    """上游持续故障→stale 兜底→标注：SSE 终态 done 且 stale=True。"""
    client = _SwitchingClient()
    adapter = _CapturingAdapter()
    service = _service(tmp_path, _fast_reliability_service(client), adapter)
    conversation = service.create_conversation("alice")
    user, first = service.start_generation(
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
    first_final = service.message_projection("alice", first.message_id)
    assert first_final is not None and first_final.arxiv_search is not None
    assert first_final.arxiv_search.status == ArxivSearchStatus.SUCCESS

    time.sleep(0.1)  # 越过 TTL：条目进入 stale 保留窗口
    second_conversation = service.create_conversation("alice")
    user2, second = service.start_generation(
        "alice", second_conversation.conversation_id, "搜索量子纠错论文"
    )
    events = list(
        service.stream_generation(
            "alice",
            second_conversation.conversation_id,
            second.message_id,
            _context(),
            until_user_message_id=user2.message_id,
        )
    )
    final = service.message_projection("alice", second.message_id)

    assert final is not None and final.status.value == "done"
    assert final.arxiv_search is not None
    assert final.arxiv_search.status == ArxivSearchStatus.SUCCESS
    assert final.arxiv_search.stale is True  # 兜底结果带「可能不是最新」标注
    assert final.arxiv_search.cache_hit is False
    assert final.arxiv_search.attempt_count == 2
    assert client.calls == 3  # 1 次成功 + 2 次失败重试
    assert events[-1].kind == "done"
