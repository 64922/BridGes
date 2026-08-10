"""跨内容统一桌面搜索服务（Issue 24）。

编排三类内容的实时检索、命中片段高亮与索引就绪信号。无进程内缓存：
每次调用都直接查询权威数据库，置顶/改名/删除即时反映。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from bridges.contracts.search import (
    ALL_RESULT_TYPES,
    SearchResponse,
    SearchResultItem,
    SearchResultType,
    SearchSegment,
)
from bridges.search.repository import SearchRepository, like_pattern
from bridges.storage import BridgesDatabase

#: 命中词前后保留的上下文窗口（字符）。
_SNIPPET_WINDOW = 40
#: 未命中片段（如名称命中但展示其他字段）的最大长度。
_SNIPPET_FALLBACK_LEN = 2 * _SNIPPET_WINDOW
#: 服务层默认与路由一致的结果上限（防御性截断）。
_MAX_LIMIT = 50


def highlight(text: str, query: str) -> list[SearchSegment]:
    """从命中文本构造高亮片段：命中词前后各保留约 40 字符，多处命中取第一处。

    片段内的命中段 ``matched=true`` 且文字为原文切片（大小写不敏感定位）。
    文本中找不到查询词时（例如按描述命中但展示名称）退化为未命中截断片段。
    """
    normalized = " ".join(text.split())
    if not normalized:
        return []
    if not query:
        return [SearchSegment(text=normalized[:_SNIPPET_FALLBACK_LEN], matched=False)]
    index = normalized.lower().find(query.lower())
    if index < 0:
        return [SearchSegment(text=normalized[:_SNIPPET_FALLBACK_LEN], matched=False)]
    start = max(0, index - _SNIPPET_WINDOW)
    end = min(len(normalized), index + len(query) + _SNIPPET_WINDOW)
    segments: list[SearchSegment] = []
    prefix = normalized[start:index]
    if start > 0:
        prefix = "…" + prefix.lstrip()
    if prefix:
        segments.append(SearchSegment(text=prefix, matched=False))
    segments.append(SearchSegment(text=normalized[index : index + len(query)], matched=True))
    suffix = normalized[index + len(query) : end]
    if end < len(normalized):
        suffix = suffix.rstrip() + "…"
    if suffix:
        segments.append(SearchSegment(text=suffix, matched=False))
    return segments


def _dt(row_value: object) -> datetime:
    return datetime.fromisoformat(str(row_value))


class SearchService:
    """统一搜索用例：合并三类结果并给出分类计数。"""

    def __init__(self, database: BridgesDatabase) -> None:
        self._repository = SearchRepository(database)

    def search(
        self,
        account_id: str,
        *,
        query: str,
        types: set[SearchResultType] | None = None,
        from_: datetime | None = None,
        to: datetime | None = None,
        limit: int = 10,
    ) -> SearchResponse:
        """执行账户级统一搜索。"""
        index_ready = self._repository.index_ready(account_id)
        active = types if types is not None else set(ALL_RESULT_TYPES)
        counts: dict[str, int] = dict.fromkeys(ALL_RESULT_TYPES, 0)
        normalized = query.strip()
        if not normalized or not active:
            # 空查询/全空白：结果为空，索引就绪信号照常返回。
            return SearchResponse(
                query=query, index_ready=index_ready, results=[], counts=counts
            )
        pattern = like_pattern(normalized)
        # 三类检索全部执行：counts 固定给出三类在筛选条件下的真实命中数
        # （契约：便于前端筛选 tab 计数）；``types`` 只决定哪些类进入结果列表。
        fetchers: list[tuple[SearchResultType, Callable[[], list[SearchResultItem]]]] = [
            (
                "chat",
                lambda: self._search_chat(
                    account_id, pattern, normalized, from_=from_, to=to,
                ),
            ),
            (
                "image",
                lambda: self._search_images(
                    account_id, pattern, normalized, from_=from_, to=to,
                ),
            ),
            (
                "document",
                lambda: self._search_documents(
                    account_id, pattern, normalized, from_=from_, to=to,
                ),
            ),
        ]
        results: list[SearchResultItem] = []
        for result_type, fetch in fetchers:
            items = fetch()
            counts[result_type] = len(items)
            if result_type in active:
                results.extend(items)
        # 合并列表按最近更新倒序，截断到 limit；counts 保持各类型总命中数。
        results.sort(key=lambda item: item.updated_at, reverse=True)
        return SearchResponse(
            query=query,
            index_ready=index_ready,
            results=results[: max(1, min(limit, _MAX_LIMIT))],
            counts=counts,
        )

    # ------------------------------------------------------------------
    # 三类内容的检索与去重规则
    # ------------------------------------------------------------------

    def _search_chat(
        self,
        account_id: str,
        pattern: str,
        query: str,
        *,
        from_: datetime | None,
        to: datetime | None,
    ) -> list[SearchResultItem]:
        """聊天结果：会话标题命中与消息正文命中各成一条（不互相折叠）。"""
        items: list[SearchResultItem] = []
        for row in self._repository.conversation_title_hits(
            account_id, pattern, from_=from_, to=to
        ):
            title = str(row["title"])
            items.append(
                SearchResultItem(
                    result_type="chat",
                    result_id=str(row["conversation_id"]),
                    title=title,
                    snippet=highlight(title, query),
                    updated_at=_dt(row["updated_at"]),
                    conversation_id=str(row["conversation_id"]),
                )
            )
        for row in self._repository.message_hits(
            account_id, pattern, from_=from_, to=to
        ):
            items.append(
                SearchResultItem(
                    result_type="chat",
                    result_id=str(row["message_id"]),
                    title=str(row["conversation_title"]) or "未命名对话",
                    snippet=highlight(str(row["content"]), query),
                    updated_at=_dt(row["updated_at"]),
                    conversation_id=str(row["conversation_id"]),
                    message_id=str(row["message_id"]),
                )
            )
        return items

    def _search_documents(
        self,
        account_id: str,
        pattern: str,
        query: str,
        *,
        from_: datetime | None,
        to: datetime | None,
    ) -> list[SearchResultItem]:
        """文档结果：每份文档至多一条；正文命中优先（带页码/章节锚点），
        仅名称命中时片段取自展示名。展示名统一为原始文件名（与知识库、
        知识库材料列表一致）；解析标题仍参与检索但不作展示名。"""
        items: list[SearchResultItem] = []
        seen: set[str] = set()
        for row in self._repository.document_chunk_hits(
            account_id, pattern, from_=from_, to=to
        ):
            document_id = str(row["document_id"])
            if document_id in seen:
                continue
            seen.add(document_id)
            items.append(
                SearchResultItem(
                    result_type="document",
                    result_id=document_id,
                    title=str(row["original_filename"]),
                    snippet=highlight(str(row["content"]), query),
                    updated_at=_dt(row["updated_at"]),
                    object_id=str(row["object_id"]),
                    page_number=(
                        int(row["page_number"]) if row["page_number"] is not None else None
                    ),
                    section_title=(
                        str(row["section_title"]) if row["section_title"] is not None else None
                    ),
                )
            )
        for row in self._repository.document_name_hits(
            account_id, pattern, from_=from_, to=to
        ):
            document_id = str(row["document_id"])
            if document_id in seen:
                continue
            seen.add(document_id)
            title = str(row["original_filename"])
            items.append(
                SearchResultItem(
                    result_type="document",
                    result_id=document_id,
                    title=title,
                    snippet=highlight(title, query),
                    updated_at=_dt(row["updated_at"]),
                    object_id=str(row["object_id"]),
                )
            )
        return items

    def _search_images(
        self,
        account_id: str,
        pattern: str,
        query: str,
        *,
        from_: datetime | None,
        to: datetime | None,
    ) -> list[SearchResultItem]:
        """图片结果：每个对象至多一条；元数据命中优先，仅文件名命中时
        片段取自文件名。图片检索面为文件名与摄取元数据/说明文本，不把
        图片发送到任何外部服务。"""
        items: list[SearchResultItem] = []
        seen: set[str] = set()
        for row in self._repository.image_chunk_hits(
            account_id, pattern, from_=from_, to=to
        ):
            object_id = str(row["object_id"])
            if object_id in seen:
                continue
            seen.add(object_id)
            items.append(
                SearchResultItem(
                    result_type="image",
                    result_id=object_id,
                    title=str(row["original_filename"]),
                    snippet=highlight(str(row["content"]), query),
                    updated_at=_dt(row["updated_at"]),
                    object_id=object_id,
                )
            )
        for row in self._repository.image_name_hits(
            account_id, pattern, from_=from_, to=to
        ):
            object_id = str(row["object_id"])
            if object_id in seen:
                continue
            seen.add(object_id)
            filename = str(row["original_filename"])
            items.append(
                SearchResultItem(
                    result_type="image",
                    result_id=object_id,
                    title=filename,
                    snippet=highlight(filename, query),
                    updated_at=_dt(row["updated_at"]),
                    object_id=object_id,
                )
            )
        return items
