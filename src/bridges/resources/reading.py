"""``resources.read`` 的图书证据读取：只读取公开作品页能取得的内容。

单个候选的读取范围固定且如实记录：

- Open Library 作品页（``{url}.json``）：简介、主题标签与目录（未提供则为空）；
- 其他书目来源（OpenAlex 等）：不额外发请求，标记为「仅有书目元数据」——
  证据层次保持 title，绝不把来源返回的标题冒充内容证据。

读取失败按稳定错误码返回，证据对象仍然生成（``error`` 非空），由匹配节点
把该条降级为标题证据。每次外发请求都留账户归属的脱敏披露审计。
"""

from __future__ import annotations

import time
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from bridges.observability.service import ObservabilityService
from bridges.resources.contracts import BookReadEvidence
from bridges.resources.sources import (
    OPENLIBRARY_SOURCE,
    log_metadata_lookup,
)

#: 图书作品页读取超时（秒）与最多读取的主题/目录条数（正文只用到少量）。
READ_TIMEOUT_SECONDS = 8.0
MAX_SUBJECTS = 8
MAX_CATALOG_ENTRIES = 20

#: 未能读取时的稳定错误码（与来源适配器的错误码风格一致）。
READ_UNAVAILABLE = "openlibrary_unavailable"
READ_TIMEOUT = "openlibrary_timeout"
READ_OFFLINE = "openlibrary_offline"
READ_ERROR = "openlibrary_read_error"

#: 读取范围的中文说明（正文与证据共用，保证「记录实际读取」可核对）。
WORK_PAGE_SCOPE = "Open Library 作品页：简介、主题标签、目录"
TITLE_ONLY_SCOPE = "来源未提供可读取的目录/简介（仅有书目元数据）"


class BookInsightReader(Protocol):
    """图书内容证据读取端口（真实实现走公开作品页，测试用替身）。"""

    def read(
        self,
        candidate_url: str,
        source: str,
        *,
        account_id: str,
        deadline: float | None = None,
    ) -> BookReadEvidence: ...


class OpenLibraryBookInsightReader:
    """Open Library 作品页读取器：简介、主题标签与目录。"""

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        observability: ObservabilityService | None = None,
        timeout: float = READ_TIMEOUT_SECONDS,
    ) -> None:
        self._client = client
        self._observability = observability
        self._timeout = timeout

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    def read(
        self,
        candidate_url: str,
        source: str,
        *,
        account_id: str,
        deadline: float | None = None,
    ) -> BookReadEvidence:
        if source != OPENLIBRARY_SOURCE or not candidate_url.startswith(
            "https://openlibrary.org/"
        ):
            return BookReadEvidence(url=candidate_url, scope=TITLE_ONLY_SCOPE)
        started = time.monotonic()
        payload, failure = self._get_json(
            self._work_url(candidate_url), deadline=deadline
        )
        log_metadata_lookup(
            self._observability,
            account_id=account_id,
            source=OPENLIBRARY_SOURCE,
            query=candidate_url,
            terminal=failure or "read",
            started=started,
        )
        if failure is not None or payload is None:
            return BookReadEvidence(
                url=candidate_url,
                scope=WORK_PAGE_SCOPE,
                error=failure or READ_ERROR,
            )
        return BookReadEvidence(
            url=candidate_url,
            scope=WORK_PAGE_SCOPE,
            catalog=_catalog_entries(payload.get("table_of_contents")),
            description=_description_text(payload.get("description")),
            subjects=_subject_entries(payload.get("subjects")),
        )

    # -- 内部实现 --------------------------------------------------------

    @staticmethod
    def _work_url(candidate_url: str) -> str:
        parts = urlsplit(candidate_url)
        path = parts.path.rstrip("/")
        if path.endswith(".json"):
            return candidate_url
        return f"https://openlibrary.org{path}.json"

    def _get_json(
        self, url: str, *, deadline: float | None
    ) -> tuple[dict[str, Any] | None, str | None]:
        if self._client is None:
            return None, READ_UNAVAILABLE
        timeout = self._timeout
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None, "openlibrary_deadline"
            timeout = min(timeout, remaining)
        try:
            response = self._client.get(
                url,
                timeout=timeout,
                headers={"User-Agent": "BridGes/1.0 (learning resource evidence read)"},
            )
        except httpx.TimeoutException:
            return None, READ_TIMEOUT
        except httpx.HTTPError:
            return None, READ_OFFLINE
        if response.status_code == 429:
            return None, "openlibrary_rate_limit"
        if response.status_code >= 400:
            return None, f"openlibrary_http_{response.status_code}"
        try:
            payload = response.json()
        except ValueError:
            return None, READ_ERROR
        if not isinstance(payload, dict):
            return None, READ_ERROR
        return payload, None


def _description_text(value: Any) -> str:
    """简介字段可能是字符串或 ``{"value": ...}``；只取可读文本。"""
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, dict):
        inner = value.get("value")
        if isinstance(inner, str):
            return " ".join(inner.split())
    return ""


def _subject_entries(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    subjects = [" ".join(str(item).split()) for item in value if str(item).strip()]
    return subjects[:MAX_SUBJECTS]


def _catalog_entries(value: Any) -> list[str]:
    """目录条目：``{"title": ...}`` / ``{"label": ...}`` 或纯字符串。"""
    if not isinstance(value, list):
        return []
    entries: list[str] = []
    for item in value:
        text = ""
        if isinstance(item, str):
            text = item
        elif isinstance(item, dict):
            text = str(item.get("title") or item.get("label") or "")
        text = " ".join(text.split())
        if text:
            entries.append(text)
        if len(entries) >= MAX_CATALOG_ENTRIES:
            break
    return entries


__all__ = [
    "BookInsightReader",
    "OpenLibraryBookInsightReader",
    "TITLE_ONLY_SCOPE",
    "WORK_PAGE_SCOPE",
]
