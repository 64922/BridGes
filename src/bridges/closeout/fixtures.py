"""Issue 09 的协议 fixture：只在 test 环境显式开启时替换外部来源。

fixture 走真实 WebSearchService、ArxivSearchService、聊天 API、SQLite 和 SSE
边界，但不访问公网或预置用户数据。生产环境不会从这里实例化任何客户端。
"""

from __future__ import annotations

from datetime import UTC, datetime
from threading import Event
from time import monotonic
from typing import Any

from bridges.ai.adapters import AdapterResult, StubQwenAdapter
from bridges.arxiv_mcp.client import ArxivMcpError
from bridges.arxiv_mcp.contracts import ArxivPaper
from bridges.contracts.ai import CapabilityRecord
from bridges.contracts.workflows import RunContextEnvelope
from bridges.web_search.client import WebSearchError
from bridges.web_search.contracts import (
    WebSearchHealth,
    WebSearchHealthStatus,
    WebSearchResult,
    WebSearchVerification,
)

CLOSEOUT_FIXTURE_PROVIDER = "duckduckgo"
CLOSEOUT_FIXTURE_PROVIDER_VERSION = "closeout-web-fixture-v1"
CLOSEOUT_FIXTURE_ARXIV_VERSION = "closeout-arxiv-fixture-v1"
CLOSEOUT_FIXTURE_TIME = datetime(2026, 8, 12, 0, 0, tzinfo=UTC)


class CloseoutWebSearchClient:
    """返回 Transformer 公开资料的最小、可重复搜索协议 fixture。"""

    provider_name = CLOSEOUT_FIXTURE_PROVIDER
    provider_version = CLOSEOUT_FIXTURE_PROVIDER_VERSION

    def __init__(self) -> None:
        self.closed = False
        self.queries: list[str] = []

    def search(
        self,
        query: str,
        *,
        timeout: float | None = None,
        deadline: float | None = None,
        stop_event: Event | None = None,
    ) -> list[WebSearchResult]:
        del timeout
        self._check_deadline(deadline, stop_event)
        self.queries.append(query)
        return [
            WebSearchResult(
                result_id="closeout-transformer-source",
                title="Transformer 架构公开资料",
                site="arxiv.org",
                url="https://arxiv.org/abs/1706.03762",
                snippet="Transformer architecture uses attention to model sequence relationships.",
                accessed_at=CLOSEOUT_FIXTURE_TIME,
                fetched_at=CLOSEOUT_FIXTURE_TIME,
                content_summary=(
                    "Transformer architecture uses self-attention and positional information "
                    "to model relationships in a sequence."
                ),
                verification=WebSearchVerification.VERIFIED,
                provider=self.provider_name,
                provider_version=self.provider_version,
            )
        ]

    def health_check(self) -> WebSearchHealth:
        return WebSearchHealth(
            provider=self.provider_name,
            provider_version=self.provider_version,
            status=WebSearchHealthStatus.READY,
            checked_at=CLOSEOUT_FIXTURE_TIME,
        )

    def close(self) -> None:
        self.closed = True

    @staticmethod
    def _check_deadline(deadline: float | None, stop_event: Event | None) -> None:
        if stop_event is not None and stop_event.is_set():
            raise WebSearchError("web_search_cancelled", "已取消本轮联网搜索。")
        if deadline is not None and deadline <= monotonic():
            raise WebSearchError("web_search_timeout", "联网搜索超时，请重试。")


class CloseoutArxivClient:
    """返回 Transformer 论文元数据的无子进程协议 fixture。"""

    provider_name = "arxiv"
    provider_version = CLOSEOUT_FIXTURE_ARXIV_VERSION

    def __init__(self) -> None:
        self.closed = False
        self.queries: list[str] = []

    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        stop_event: Event | None = None,
        deadline: float | None = None,
    ) -> list[ArxivPaper]:
        if stop_event is not None and stop_event.is_set():
            raise ArxivMcpError("arxiv_cancelled", "已取消本轮论文搜索。")
        if deadline is not None and deadline <= monotonic():
            raise ArxivMcpError("arxiv_timeout", "arXiv 搜索超时，请重试。")
        if not query.strip():
            raise ArxivMcpError("arxiv_request", "论文搜索参数不合法，请重试。")
        self.queries.append(query)
        return [
            ArxivPaper(
                arxiv_id="1706.03762",
                title="Attention Is All You Need: Transformer Architecture",
                authors=["Ashish Vaswani", "Noam Shazeer"],
                published_at=datetime(2017, 6, 12, tzinfo=UTC),
                abs_url="https://arxiv.org/abs/1706.03762",
                pdf_url="https://arxiv.org/pdf/1706.03762",
                abstract=(
                    "We propose the Transformer architecture, based solely on attention "
                    "mechanisms for sequence transduction tasks."
                ),
            )
        ][: max(1, min(max_results, 10))]

    def close(self) -> None:
        self.closed = True


class CloseoutQwenAdapter(StubQwenAdapter):
    """为三条收尾旅程返回符合引用合同的确定性聊天回答。"""

    def call(
        self,
        capability: CapabilityRecord,
        run_context: RunContextEnvelope,
        payload: dict[str, Any],
    ) -> AdapterResult:
        result = super().call(capability, run_context, payload)
        material = "\n".join(
            str(message.get("content", ""))
            for message in payload.get("messages", [])
            if isinstance(message, dict)
        )
        if "arXiv" in material or "论文" in material:
            content = "基于 [arxiv-1] 返回的真实论文结果，Transformer 是本轮确认的主题。"
        elif "教学" in material or "证据门" in material:
            content = "基于 [reference:1] 的公开资料，Transformer 使用注意力机制建模序列关系。"
        else:
            content = "这是一条来自本地替身模式的确定性测试回答。"
        return AdapterResult(
            actual_model_id=result.actual_model_id,
            output={**result.output, "content": content},
            usage=result.usage,
        )
