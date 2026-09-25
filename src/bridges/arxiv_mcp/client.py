"""受限 arXiv API 客户端：只读、固定端点、只解析真实 Atom 响应。"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from threading import Event, Lock
from time import monotonic
from typing import Any
from urllib.parse import unquote, urlparse
from xml.etree import ElementTree

import httpx

from bridges.arxiv_mcp.contracts import ArxivPaper
from bridges.arxiv_mcp.manifest import assert_registered_arxiv_url

ARXIV_API_ENDPOINT = "https://export.arxiv.org/api/query"
_ATOM_NS = "http://www.w3.org/2005/Atom"
_ARXIV_NS = "http://arxiv.org/schemas/atom"
_ALLOWED_HOSTS = {"arxiv.org", "export.arxiv.org"}
_ARXIV_ID = re.compile(r"^[^\s?#]+$")
_QUERY_ARXIV_ID = re.compile(
    r"^(?:\d{4}\.\d{4,5}(?:v\d+)?|[A-Za-z][A-Za-z0-9-]*(?:\.[A-Za-z0-9-]+)?/\d{7})$"
)
_STRUCTURED_FIELD = re.compile(r"(author|title|id|year)\s*:", re.I)
_YEAR_VALUE = re.compile(r"^(\d{4})(?:\s*[-–—]\s*(\d{4}))?$")
_QUOTE_CHARS = "\"'“”‘’"
_QUOTE_PAIRS = {'"': '"', "'": "'", "“": "”", "‘": "’"}
_QUOTE_TRANSLATION = dict.fromkeys(
    (ord(character) for character in _QUOTE_CHARS), ord(" ")
)
_DEFAULT_UPSTREAM_STATUS = {
    "arxiv_timeout": "timeout",
    "arxiv_offline": "network",
    "arxiv_rate_limit": "http_429",
    "arxiv_permission": "permission",
    "arxiv_request": "http_4xx",
    "arxiv_parse": "parse",
    "arxiv_cancelled": "cancelled",
    "arxiv_startup": "startup",
    "arxiv_handshake": "handshake",
    "arxiv_worker_exit": "worker_exit",
    "arxiv_internal": "internal",
    "arxiv_backpressure": "backpressure",
}


class ArxivMcpError(Exception):
    """论文 MCP 的稳定错误码与用户可见中文提示。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        permission: bool = False,
        upstream_status: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.permission = permission
        self.upstream_status = upstream_status or _DEFAULT_UPSTREAM_STATUS.get(code)
        self.retryable = (
            retryable if retryable is not None else code != "arxiv_cancelled"
        )
        super().__init__(message)


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
                params = _build_search_params(query, max_results)
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
                "arXiv 请求过于频繁，请稍后重试。",
                upstream_status="http_429",
            )
        if response.status_code in {401, 403}:
            raise ArxivMcpError(
                "arxiv_permission",
                "当前网络未允许访问 arXiv，请检查网络权限后重试。",
                permission=True,
                upstream_status="http_4xx_permission",
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
                "arXiv 暂时不可用，请稍后重试。",
                upstream_status="http_5xx",
            )
        if response.status_code >= 400:
            raise ArxivMcpError(
                "arxiv_request",
                "arXiv 搜索请求未完成，请检查查询条件后重试。",
                upstream_status="http_4xx",
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


def _build_search_params(query: str, max_results: int) -> dict[str, str]:
    """把路由器的约束快照转换为 arXiv API 的结构化查询。"""
    normalized = " ".join(query.split())
    matches = _structured_field_matches(normalized)
    fields: dict[str, str] = {}
    topic_parts: list[str] = []
    cursor = 0
    for index, match in enumerate(matches):
        topic_parts.append(_strip_quotes(normalized[cursor : match[1]]))
        limit = matches[index + 1][1] if index + 1 < len(matches) else len(normalized)
        value_end = _field_value_end(match[0], normalized, match[2], limit)
        value = _clean_field_value(normalized[match[2] : value_end])
        if value:
            fields[match[0]] = value
        cursor = value_end
    if matches:
        topic_parts.append(_strip_quotes(normalized[cursor:]))
    else:
        # 普通关键词没有结构化字段时仍然是有效的 arXiv all: 查询。
        topic_parts.append(_strip_quotes(normalized))
    topic = " ".join(part for part in topic_parts if part).strip()

    params = {
        "start": "0",
        "max_results": str(max_results),
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    if fields.get("id"):
        id_list = _normalize_id_list(fields["id"])
        params["id_list"] = id_list

    clauses = [f"all:{token}" for token in topic.split() if token]
    if fields.get("author"):
        clauses.append(f'au:"{_quote_value(fields["author"])}"')
    if fields.get("title"):
        clauses.append(f'ti:"{_quote_value(fields["title"])}"')
    if fields.get("year"):
        year_from, year_to = _parse_year_value(fields["year"])
        clauses.append(f"submittedDate:[{year_from}01010000 TO {year_to}12312359]")
    if clauses and any(clause for clause in clauses):
        params["search_query"] = " AND ".join(clauses)
    if "search_query" not in params and "id_list" not in params:
        raise ArxivMcpError(
            "arxiv_request",
            "论文搜索主题不能为空，请补充领域或约束。",
            upstream_status="local_invariant",
            retryable=False,
        )
    return params


def _structured_field_matches(
    normalized: str,
) -> list[tuple[str, int, int]]:
    """查找不在引号内的结构化字段，返回字段名、起点和取值起点。"""
    matches: list[tuple[str, int, int]] = []
    quote: str | None = None
    index = 0
    while index < len(normalized):
        character = normalized[index]
        if quote is not None:
            if character == quote:
                quote = None
            index += 1
            continue
        if (
            character in _QUOTE_PAIRS
            and _can_start_quote(normalized, index)
            and not (character in "'’" and index > 0 and normalized[index - 1].isalnum())
        ):
            quote = _QUOTE_PAIRS[character]
            index += 1
            continue
        if index == 0 or normalized[index - 1].isspace():
            match = _STRUCTURED_FIELD.match(normalized, index)
            if match is not None:
                matches.append((match.group(1).lower(), match.start(), match.end()))
                index = match.end()
                continue
        index += 1
    return matches


def _can_start_quote(value: str, index: int) -> bool:
    return index == 0 or value[index - 1].isspace() or value[index - 1] in ":(["


def _strip_quotes(value: str) -> str:
    return " ".join(value.translate(_QUOTE_TRANSLATION).split())


def _clean_field_value(value: str) -> str:
    cleaned = _strip_quotes(value)
    return cleaned.strip(" ,;，。；：:")


def _field_value_end(field: str, value: str, value_start: int, limit: int) -> int:
    remainder = value[value_start:limit]
    leading = len(remainder) - len(remainder.lstrip())
    if leading == len(remainder):
        return limit
    quote_char = remainder[leading]
    closing = {'"': '"', "'": "'", "“": "”", "‘": "’"}.get(quote_char)
    if closing is None:
        if field == "id":
            return limit
        if field == "year":
            token = re.match(r"\S+", remainder[leading:])
            if token is not None:
                return value_start + leading + token.end()
        return limit
    closing_offset = remainder.find(closing, leading + 1)
    if closing_offset != -1:
        return value_start + closing_offset + 1
    if field in {"id", "year"}:
        token = re.match(r"\S+", remainder[leading:])
        if token is not None:
            return value_start + leading + token.end()
    return limit


def _quote_value(value: str) -> str:
    return " ".join(value.replace('"', " ").split()).strip()


def _normalize_id_list(value: str) -> str:
    identifiers = [item.strip() for item in value.split(",")]
    if not identifiers or any(
        not item or not _QUERY_ARXIV_ID.fullmatch(item) for item in identifiers
    ):
        raise ArxivMcpError(
            "arxiv_request",
            "arXiv 标识符无效，请检查 id 约束后重试。",
            upstream_status="local_invariant",
            retryable=False,
        )
    return ",".join(identifiers)


def _parse_year_value(value: str) -> tuple[str, str]:
    match = _YEAR_VALUE.fullmatch(value)
    if match is None:
        raise ArxivMcpError(
            "arxiv_request",
            "年份约束无效，请使用 YYYY 或 YYYY-YYYY。",
            upstream_status="local_invariant",
            retryable=False,
        )
    year_from = int(match.group(1))
    year_to = int(match.group(2) or match.group(1))
    if not 1900 <= year_from <= year_to <= 2100:
        raise ArxivMcpError(
            "arxiv_request",
            "年份约束无效，请使用 1900 年以后的有效范围。",
            upstream_status="local_invariant",
            retryable=False,
        )
    return str(year_from), str(year_to)


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
