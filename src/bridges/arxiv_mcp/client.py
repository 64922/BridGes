"""受限 arXiv API 客户端：只读、固定端点、只解析真实 Atom 响应。"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from urllib.parse import unquote, urlparse
from xml.etree import ElementTree

import httpx

from bridges.arxiv_mcp.contracts import ArxivPaper
from bridges.arxiv_mcp.manifest import assert_registered_arxiv_url

ARXIV_API_ENDPOINT = "https://export.arxiv.org/api/query"
_ATOM_NS = "http://www.w3.org/2005/Atom"
_ALLOWED_HOSTS = {"arxiv.org", "export.arxiv.org"}
_ARXIV_ID = re.compile(r"^[^\s?#]+$")


class ArxivMcpError(Exception):
    """论文 MCP 的稳定错误码与用户可见中文提示。"""

    def __init__(self, code: str, message: str, *, permission: bool = False) -> None:
        self.code = code
        self.message = message
        self.permission = permission
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

    def search(self, query: str, *, max_results: int = 5) -> list[ArxivPaper]:
        if not query.strip():
            raise ArxivMcpError("arxiv_request", "论文搜索主题不能为空，请补充领域或约束。")
        if not 1 <= max_results <= 10:
            raise ArxivMcpError("arxiv_request", "论文搜索结果数量不在允许范围内。")
        try:
            assert_registered_arxiv_url(ARXIV_API_ENDPOINT)
            response = self._client.get(
                ARXIV_API_ENDPOINT,
                params={
                    "search_query": f"all:{query}",
                    "start": "0",
                    "max_results": str(max_results),
                    "sortBy": "submittedDate",
                    "sortOrder": "descending",
                },
            )
        except httpx.TimeoutException as exc:
            raise ArxivMcpError("arxiv_timeout", "arXiv 搜索超时，请重试。") from exc
        except httpx.HTTPError as exc:
            raise ArxivMcpError("arxiv_offline", "当前无法连接 arXiv，请检查网络后重试。") from exc
        except PermissionError as exc:
            raise ArxivMcpError(
                "arxiv_permission", str(exc), permission=True
            ) from exc

        if response.status_code == 429:
            raise ArxivMcpError("arxiv_rate_limit", "arXiv 请求过于频繁，请稍后重试。")
        if response.status_code in {401, 403}:
            raise ArxivMcpError(
                "arxiv_permission",
                "当前网络未允许访问 arXiv，请检查网络权限后重试。",
                permission=True,
            )
        if response.status_code >= 500:
            raise ArxivMcpError("arxiv_offline", "arXiv 暂时不可用，请稍后重试。")
        if response.status_code >= 400:
            raise ArxivMcpError("arxiv_request", "arXiv 搜索请求未完成，请重试。")
        try:
            return _parse_atom(response.text)
        except (ElementTree.ParseError, ValueError, TypeError, KeyError) as exc:
            raise ArxivMcpError("arxiv_parse", "arXiv 返回内容损坏，无法解析，请重试。") from exc

    def close(self) -> None:
        self._client.close()


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
            )
        )
    return papers


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
