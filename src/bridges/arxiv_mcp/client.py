"""受限 arXiv API 客户端：只读、固定端点、只解析真实 Atom 响应。"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from threading import Event, Lock
from time import monotonic
from typing import Any
from urllib.parse import unquote, urlparse
from xml.etree import ElementTree

import httpx

from bridges.arxiv_mcp.contracts import ArxivPaper
from bridges.arxiv_mcp.errors import ArxivMcpError
from bridges.arxiv_mcp.limits import ARXIV_RETRY_AFTER_MAX_SECONDS
from bridges.arxiv_mcp.manifest import assert_registered_arxiv_url
from bridges.arxiv_mcp.query import build_search_params

ARXIV_API_ENDPOINT = "https://export.arxiv.org/api/query"
_ATOM_NS = "http://www.w3.org/2005/Atom"
_ARXIV_NS = "http://arxiv.org/schemas/atom"
_ALLOWED_HOSTS = {"arxiv.org", "export.arxiv.org"}
_ARXIV_ID = re.compile(r"^[^\s?#]+$")


class ArxivMcpClient:
    """固定版本 MCP worker 使用的同步 HTTP 客户端。"""

    def __init__(
        self,
        *,
        http_client: httpx.Client | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._client = http_client or httpx.Client(timeout=timeout)
        self._query_invariant_failures = 0
        self._query_invariant_failures_lock = Lock()

    @property
    def query_invariant_failures(self) -> int:
        """返回本地阻止的无主题请求次数，不携带查询正文。"""
        with self._query_invariant_failures_lock:
            return self._query_invariant_failures

    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        stop_event: Event | None = None,
        deadline: float | None = None,
    ) -> list[ArxivPaper]:
        if not query.strip():
            self._record_query_invariant_failure()
            raise ArxivMcpError(
                "arxiv_request",
                "论文搜索主题不能为空，请补充领域或约束。",
                upstream_status="local_invariant",
                retryable=False,
            )
        if not 1 <= max_results <= 10:
            raise ArxivMcpError(
                "arxiv_request",
                "论文搜索结果数量不在允许范围内。",
                upstream_status="local_invariant",
                retryable=False,
            )
        if _user_cancelled(stop_event):
            raise ArxivMcpError(
                "arxiv_cancelled",
                "已取消本轮论文搜索。",
                upstream_status="cancelled",
                retryable=False,
            )
        if deadline is not None and deadline <= monotonic():
            raise ArxivMcpError(
                "arxiv_timeout",
                "arXiv 搜索超时，请重试。",
                upstream_status="timeout",
            )
        try:
            assert_registered_arxiv_url(ARXIV_API_ENDPOINT)
            try:
                params = build_search_params(query, max_results)
            except ArxivMcpError as exc:
                if exc.upstream_status == "local_invariant":
                    self._record_query_invariant_failure()
                raise
            request_kwargs: dict[str, Any] = {
                "params": params
            }
            if deadline is not None:
                request_kwargs["timeout"] = max(0.001, deadline - monotonic())
            response = self._client.get(
                ARXIV_API_ENDPOINT,
                **request_kwargs,
            )
        except httpx.TimeoutException as exc:
            raise ArxivMcpError(
                "arxiv_timeout",
                "arXiv 搜索超时，请重试。",
                upstream_status="timeout",
            ) from exc
        except httpx.HTTPError as exc:
            raise ArxivMcpError(
                "arxiv_offline",
                "当前无法连接 arXiv，请检查网络后重试。",
                upstream_status="network",
            ) from exc
        except PermissionError as exc:
            raise ArxivMcpError(
                "arxiv_permission",
                "当前网络未允许访问 arXiv，请检查网络权限后重试。",
                permission=True,
                upstream_status="permission",
                retryable=True,
            ) from exc

        if response.status_code == 429:
            raise ArxivMcpError(
                "arxiv_rate_limit",
                "arXiv 请求过于频繁（HTTP 429），请稍后重试。",
                upstream_status="http_429",
                retry_after_seconds=_retry_after_seconds(response),
            )
        if response.status_code in {401, 403}:
            raise ArxivMcpError(
                "arxiv_permission",
                f"arXiv 拒绝访问（HTTP {response.status_code}），"
                "请检查网络代理或访问权限后重试。",
                permission=True,
                upstream_status=f"http_{response.status_code}",
                retryable=True,
            )

        if _user_cancelled(stop_event):
            raise ArxivMcpError(
                "arxiv_cancelled",
                "已取消本轮论文搜索。",
                upstream_status="cancelled",
                retryable=False,
            )
        if deadline is not None and deadline <= monotonic():
            raise ArxivMcpError(
                "arxiv_timeout",
                "arXiv 搜索超时，请重试。",
                upstream_status="timeout",
            )
        if response.status_code >= 500:
            raise ArxivMcpError(
                "arxiv_offline",
                f"arXiv 上游暂时不可用（HTTP {response.status_code}），请稍后重试。",
                upstream_status=f"http_{response.status_code}",
                retry_after_seconds=_retry_after_seconds(response),
            )
        if response.status_code >= 400:
            if response.status_code == 400:
                message = (
                    "arXiv 未接受本次查询参数（HTTP 400）；"
                    "本轮未使用该来源，其他来源结果不受影响。"
                )
            else:
                message = (
                    f"arXiv 返回错误响应（HTTP {response.status_code}）；"
                    "本轮未使用该来源，其他来源结果不受影响。"
                )
            raise ArxivMcpError(
                "arxiv_request",
                message,
                upstream_status=f"http_{response.status_code}",
                retryable=False,
            )
        try:
            return _parse_atom(response.text)
        except (ElementTree.ParseError, ValueError, TypeError, KeyError) as exc:
            raise ArxivMcpError(
                "arxiv_parse",
                "arXiv 返回内容损坏，无法解析，请重试。",
                upstream_status="parse",
            ) from exc

    def close(self) -> None:
        self._client.close()

    def _record_query_invariant_failure(self) -> None:
        with self._query_invariant_failures_lock:
            self._query_invariant_failures += 1


def _retry_after_seconds(response: httpx.Response) -> int | None:
    """解析有界的 Retry-After（秒数或 HTTP 日期），不信任无上限值。"""
    raw = (response.headers.get("retry-after") or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        seconds = int(raw)
    else:
        try:
            when = parsedate_to_datetime(raw)
        except (TypeError, ValueError, OverflowError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = int((when - datetime.now(UTC)).total_seconds())
    return max(0, min(seconds, ARXIV_RETRY_AFTER_MAX_SECONDS))


def _parse_atom(body: str) -> list[ArxivPaper]:
    root = ElementTree.fromstring(body)
    papers: list[ArxivPaper] = []
    for entry in root.findall(f"{{{_ATOM_NS}}}entry"):
        identifier_url = _required_text(entry, "id")
        arxiv_id = _identifier_from_url(identifier_url)
        title = _clean_text(_required_text(entry, "title"))
        abstract = _clean_text(_required_text(entry, "summary"))
        published = datetime.fromisoformat(
            _required_text(entry, "published").replace("Z", "+00:00")
        ).astimezone(UTC)
        authors = [
            _clean_text(name.text or "")
            for name in entry.findall(f"{{{_ATOM_NS}}}author/{{{_ATOM_NS}}}name")
            if (name.text or "").strip()
        ]
        if not authors:
            raise ValueError("arXiv entry 缺少作者")
        links = entry.findall(f"{{{_ATOM_NS}}}link")
        abs_url = _matching_link(links, arxiv_id, "abs")
        pdf_url = _matching_link(links, arxiv_id, "pdf")
        papers.append(
            ArxivPaper(
                arxiv_id=arxiv_id,
                title=title,
                authors=authors,
                published_at=published,
                abs_url=abs_url,
                pdf_url=pdf_url,
                abstract=abstract,
                primary_category=_primary_category(entry),
            )
        )
    return papers


def _primary_category(entry: ElementTree.Element) -> str | None:
    """arXiv 主类别（``arxiv:primary_category@term``）；缺失时返回 None。"""
    element = entry.find(f"{{{_ARXIV_NS}}}primary_category")
    if element is None:
        return None
    term = (element.attrib.get("term") or "").strip()
    return term or None


def _required_text(entry: ElementTree.Element, name: str) -> str:
    value = entry.findtext(f"{{{_ATOM_NS}}}{name}")
    if value is None or not value.strip():
        raise ValueError(f"arXiv entry 缺少 {name}")
    return value


def _identifier_from_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.hostname not in _ALLOWED_HOSTS or not parsed.path.startswith("/abs/"):
        raise ValueError("arXiv 标识符链接不在允许范围内")
    identifier = unquote(parsed.path.removeprefix("/abs/")).strip("/")
    if not identifier or not _ARXIV_ID.fullmatch(identifier):
        raise ValueError("arXiv 标识符无效")
    return identifier


def _matching_link(links: list[ElementTree.Element], arxiv_id: str, kind: str) -> str:
    expected_prefix = f"/{kind}/{arxiv_id}"
    for link in links:
        href = link.attrib.get("href", "").strip()
        parsed = urlparse(href)
        if (
            parsed.hostname in _ALLOWED_HOSTS
            and parsed.path == expected_prefix
            and kind == ("pdf" if link.attrib.get("title") == "pdf" else "abs")
        ):
            return f"https://arxiv.org{parsed.path}"
    # 规范链接仅由服务端返回的 identifier 推导，不接受不一致的外部链接。
    return f"https://arxiv.org{expected_prefix}"


def _clean_text(value: str) -> str:
    return " ".join(value.split())


def _user_cancelled(stop_event: Event | None) -> bool:
    """区分用户取消与来源截止信号。"""
    if stop_event is None:
        return False
    marker = getattr(stop_event, "user_is_set", None)
    return bool(marker()) if callable(marker) else stop_event.is_set()
