"""arXiv 结果的进程内结果缓存（规范化查询键 + TTL）。

缓存只存放成功投影（失败与空结果不缓存），以规范化查询为键：大小写、
空白与词序归一，引号短语保持原子（避免把 ``ti:"A B"`` 与 ``ti:"B A"``
误判为同一查询）。TTL 与开关来自 :mod:`bridges.arxiv_mcp.limits`。
缓存不落敏感信息：键只来自本地脱敏后的公开查询词组，值只含 arXiv
返回的公开论文元数据。
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any

from bridges.arxiv_mcp.contracts import ArxivSearchProjection
from bridges.arxiv_mcp.limits import ARXIV_CACHE_TTL_SECONDS

_QUOTED_SEGMENT = re.compile(r'"(?:[^"]*)"|\'(?:[^\']*)\'|“[^”]*”|‘[^’]*’')


def normalize_query_key(query: str) -> str:
    """把查询归一为缓存键：大小写/空白/词序归一，引号短语保持原子。

    - 大小写：``casefold``（``Transformer`` 与 ``transformer`` 同键）；
    - 空白：连续空白折叠为单个空格；
    - 词序：普通词按字典序排序（arXiv 的 ``all:`` 子句顺序无关），
      引号短语整体作为一个原子单元参与排序，短语内部词序不被破坏。
    """
    collapsed = " ".join(query.split()).casefold()
    parts: list[str] = []
    cursor = 0
    for match in _QUOTED_SEGMENT.finditer(collapsed):
        if match.start() > cursor:
            parts.extend(collapsed[cursor : match.start()].split())
        parts.append(match.group(0))
        cursor = match.end()
    parts.extend(collapsed[cursor:].split())
    return " ".join(sorted(parts))


class ArxivResultCache:
    """带 TTL 的进程内结果缓存；测试可注入时钟与 TTL。

    键按账户隔离（与 ``WebSearchCacheRepository`` 先例一致）：不同账户
    的同一查询不会互相命中，避免把账户 A 的查询主题展示到账户 B 的卡片。
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = ARXIV_CACHE_TTL_SECONDS,
        enabled: bool = True,
        clock: Any = None,
    ) -> None:
        self._ttl_seconds = max(0.0, ttl_seconds)
        self._enabled = enabled
        self._clock = clock or time.monotonic
        #: 键 → (到期单调时刻, 投影)
        self._entries: dict[
            tuple[str, str, int, str], tuple[float, ArxivSearchProjection]
        ] = {}
        self._lock = threading.RLock()

    def get(
        self,
        account_id: str,
        query: str,
        max_results: int,
        route_version: str,
    ) -> ArxivSearchProjection | None:
        """命中未过期缓存时返回标记 ``cache_hit`` 的投影副本，否则 None。"""
        if not self._enabled:
            return None
        key = self._key(account_id, query, max_results, route_version)
        now = self._clock()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, projection = entry
            if expires_at <= now:
                self._entries.pop(key, None)
                return None
            return projection.model_copy(update={"cache_hit": True})

    def put(
        self,
        account_id: str,
        query: str,
        max_results: int,
        route_version: str,
        projection: ArxivSearchProjection,
    ) -> None:
        """写入缓存（调用方保证只写成功投影；TTL 从写入时刻起算）。"""
        if not self._enabled:
            return
        key = self._key(account_id, query, max_results, route_version)
        expires_at = self._clock() + self._ttl_seconds
        stored = projection.model_copy(update={"cache_hit": False})
        with self._lock:
            self._entries[key] = (expires_at, stored)

    @staticmethod
    def _key(
        account_id: str, query: str, max_results: int, route_version: str
    ) -> tuple[str, str, int, str]:
        return (account_id, normalize_query_key(query), max_results, route_version)
