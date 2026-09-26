"""``tieba.read``：只读取实际可访问的公开主帖与回复。

读取是普通的公开 HTTP GET：不做任何登录、Cookie 伪造或签名绕过，被访问
限制挡住就如实记成受限，并按「仅帖链」降级。页数、响应体大小与时间都有
上限，实际读到的页数、楼层范围与发帖时间逐条记录，取不到就如实说取不到。

页面解析按贴吧帖子页公开的 ``PageData`` 结构做防御式解析：找不到可用结构
时返回不可识别，绝不用猜测内容填补。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from html import unescape
from typing import Any, Protocol
from urllib.parse import unquote

import httpx

from bridges.tieba.contracts import ReadStatus, TiebaReadResult, TiebaReply
from bridges.tieba.lexicon import TARGET_FORUM_NAME
from bridges.tieba.searching import canonical_url, thread_id_of

#: 一轮里单帖最多读取的页数（分页受限时如实记录实际页数）。
READ_PAGES_LIMIT = 2

#: 单个帖子的读取墙钟上限（含分页）。
READ_DEADLINE_SECONDS = 8.0

#: 单次响应体上限，避免异常页面拖垮进程。
READ_MAX_BYTES = 800_000

#: 每个帖子最多记录的楼层数。
MAX_REPLIES = 40

#: 单层正文最大字符数。
MAX_REPLY_CHARS = 500

#: 公开页面读取使用的普通浏览器标识（不携带任何账户信息）。
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

#: 访问限制页面的特征（命中即按受限处理，不尝试绕过）。只收整句的墙页文案：
#: 「验证码」这类泛词也会出现在正常讨论里，会把可读页面误判成登录墙。
_RESTRICTED_MARKERS: tuple[str, ...] = (
    "百度安全验证",
    "请先登录",
    "登录后查看",
    "登录后才能查看",
)

_PAGE_DATA = re.compile(r"PageData\s*=\s*")
_KW = re.compile(r"kw=([^\"'&\s]+)")
_TAG = re.compile(r"<[^>]+>")
_SCRIPT_STYLE = re.compile(
    r"<(script|style)\b.*?</\1>", re.IGNORECASE | re.DOTALL
)
_WHITESPACE = re.compile(r"[ \t\r\f\v]+")


class TiebaReadError(Exception):
    """读取层的稳定错误（分类码 + 中文说明）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class TiebaThreadReader(Protocol):
    """帖子读取边界（测试可注入替身，不发起真实请求）。"""

    def read(
        self,
        url: str,
        *,
        pages_limit: int = READ_PAGES_LIMIT,
        stop_event: object | None = None,
        deadline: float | None = None,
    ) -> TiebaReadResult: ...


@dataclass(frozen=True)
class ParsedThread:
    """一次页面解析出来的真实内容。"""

    forum_name: str | None
    title: str | None
    total_pages: int | None
    replies: tuple[TiebaReply, ...]
    structure_found: bool


class HttpTiebaThreadReader:
    """公开页面读取实现（httpx，固定 UA，有界页数与体积）。"""

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        pages_limit: int = READ_PAGES_LIMIT,
        max_bytes: int = READ_MAX_BYTES,
        timeout: float = READ_DEADLINE_SECONDS,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"},
        )
        self._pages_limit = pages_limit
        self._max_bytes = max_bytes

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def read(
        self,
        url: str,
        *,
        pages_limit: int | None = None,
        stop_event: object | None = None,
        deadline: float | None = None,
    ) -> TiebaReadResult:
        limit = self._pages_limit if pages_limit is None else max(1, pages_limit)
        canonical = canonical_url(url)
        thread_id = thread_id_of(url)
        retrieved_at = datetime.now(UTC)
        replies: list[TiebaReply] = []
        forum_name: str | None = None
        title: str | None = None
        total_pages: int | None = None
        pages_read = 0
        failure: tuple[str, str] | None = None

        for page in range(1, limit + 1):
            if _stopped(stop_event):
                return _result(
                    url=canonical,
                    thread_id=thread_id,
                    forum_name=forum_name,
                    title=title,
                    total_pages=total_pages,
                    replies=replies,
                    pages_read=pages_read,
                    pages_limit=limit,
                    status=ReadStatus.CANCELLED,
                    error_code="tieba_read_cancelled",
                    error_message="已停止读取，未继续读取后续页面。",
                    retrieved_at=retrieved_at,
                )
            if deadline is not None and time.monotonic() >= deadline:
                failure = (
                    "tieba_read_timeout",
                    "读取超时，未取得后续页面的回复内容。",
                )
                break
            try:
                response = self._get(canonical, page=page, deadline=deadline)
            except TiebaReadError as exc:
                failure = (exc.code, exc.message)
                break
            status_code = response.status_code
            if status_code in {401, 403}:
                failure = (
                    "tieba_read_access_restricted",
                    "页面要求登录或触发了访问验证，未取得回复内容。",
                )
                break
            if status_code == 404:
                failure = ("tieba_read_not_found", "该帖子不存在或已删除。")
                break
            if status_code >= 400:
                failure = (
                    "tieba_read_unavailable",
                    f"页面返回 HTTP {status_code}，未取得回复内容。",
                )
                break
            body = _bounded_text(response, self._max_bytes)
            if _restricted(body):
                failure = (
                    "tieba_read_access_restricted",
                    "页面是访问验证／登录提示，未取得回复内容。",
                )
                break
            parsed = parse_thread_page(body)
            if not parsed.structure_found:
                failure = (
                    "tieba_read_unrecognized",
                    "页面结构与预期不符，未取得可核实的回复内容。",
                )
                break
            pages_read += 1
            forum_name = parsed.forum_name or forum_name
            title = parsed.title or title
            total_pages = parsed.total_pages or total_pages
            for reply in parsed.replies:
                if len(replies) >= MAX_REPLIES:
                    break
                replies.append(reply)
            if len(replies) >= MAX_REPLIES:
                break
            if total_pages is not None and page >= total_pages:
                break

        status = ReadStatus.READ
        error_code: str | None = None
        error_message: str | None = None
        if failure is not None:
            error_code, error_message = failure
            if pages_read:
                status = ReadStatus.PARTIAL
            elif error_code == "tieba_read_access_restricted":
                status = ReadStatus.ACCESS_RESTRICTED
            elif error_code == "tieba_read_unrecognized":
                status = ReadStatus.UNRECOGNIZED
            elif error_code == "tieba_read_not_found":
                status = ReadStatus.NOT_FOUND
            elif error_code == "tieba_read_timeout":
                status = ReadStatus.TIMEOUT
            else:
                status = ReadStatus.ERROR
        return _result(
            url=canonical,
            thread_id=thread_id,
            forum_name=forum_name,
            title=title,
            total_pages=total_pages,
            replies=replies,
            pages_read=pages_read,
            pages_limit=limit,
            status=status,
            error_code=error_code,
            error_message=error_message,
            retrieved_at=retrieved_at,
        )

    def _get(self, url: str, *, page: int, deadline: float | None) -> httpx.Response:
        timeout = READ_DEADLINE_SECONDS
        if deadline is not None:
            timeout = max(0.5, min(timeout, deadline - time.monotonic()))
        try:
            return self._client.get(url, params={"pn": page}, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise TiebaReadError(
                "tieba_read_timeout", "读取超时，未取得回复内容。"
            ) from exc
        except httpx.HTTPError as exc:
            raise TiebaReadError(
                "tieba_read_unavailable", "当前无法连接贴吧页面，未取得回复内容。"
            ) from exc


def _result(
    *,
    url: str,
    thread_id: str | None,
    forum_name: str | None,
    title: str | None,
    total_pages: int | None,
    replies: list[TiebaReply],
    pages_read: int,
    pages_limit: int,
    status: ReadStatus,
    error_code: str | None,
    error_message: str | None,
    retrieved_at: datetime,
) -> TiebaReadResult:
    floors = [reply.floor for reply in replies if reply.floor is not None]
    return TiebaReadResult(
        url=url,
        thread_id=thread_id,
        status=status,
        forum_name=forum_name,
        title=title,
        pages_read=pages_read,
        pages_limit=pages_limit,
        total_pages=total_pages,
        floor_min=min(floors) if floors else None,
        floor_max=max(floors) if floors else None,
        replies=replies,
        error_code=error_code,
        error_message=error_message,
        retrieved_at=retrieved_at,
    )


def _stopped(stop_event: object | None) -> bool:
    checker = getattr(stop_event, "is_set", None)
    return bool(checker()) if callable(checker) else False


def _bounded_text(response: httpx.Response, max_bytes: int) -> str:
    raw = response.content[:max_bytes]
    for encoding in ("utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _restricted(body: str) -> bool:
    head = body[:4000]
    return any(marker in head for marker in _RESTRICTED_MARKERS)


def _mapping(value: object) -> dict[str, Any]:
    """只接受真实的对象形态，其他（缺失/列表/标量）一律当空对象处理。"""
    return value if isinstance(value, dict) else {}


def parse_thread_page(html: str) -> ParsedThread:
    """按页面公开的 ``PageData`` 结构解析；找不到可用结构时如实返回未识别。"""
    data = _page_data(html)
    if data is None:
        return ParsedThread(None, None, None, (), structure_found=False)
    forum = _forum_name(data, html)
    thread = _mapping(data.get("thread"))
    title = _clean_text(str(thread.get("title") or "")) or None
    page_info = _mapping(data.get("page"))
    total_pages = _as_int(page_info.get("total_page"))
    posts = data.get("post_list")
    if not isinstance(posts, list):
        return ParsedThread(forum, title, total_pages, (), structure_found=True)
    author_name = _author_name(thread.get("author"))
    replies: list[TiebaReply] = []
    for item in posts:
        if not isinstance(item, dict):
            continue
        content = _content_text(item.get("content"))
        if not content:
            continue
        name = _author_name(item.get("author"))
        replies.append(
            TiebaReply(
                floor=_as_int(item.get("floor")),
                posted_at=_clean_text(str(item.get("time") or "")) or None,
                is_original_poster=bool(author_name and name == author_name),
                content=content[:MAX_REPLY_CHARS],
            )
        )
    return ParsedThread(forum, title, total_pages, tuple(replies), structure_found=True)


def _page_data(html: str) -> dict[str, Any] | None:
    for match in _PAGE_DATA.finditer(html):
        raw = _balanced_json(html, match.end())
        if raw is None:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def _balanced_json(text: str, start: int) -> str | None:
    """从 ``start`` 起提取一个平衡的 JSON 对象（跳过字符串与转义）。"""
    while start < len(text) and text[start] != "{":
        if text[start] in "\r\n;":
            return None
        start += 1
    if start >= len(text):
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _forum_name(data: dict[str, Any], html: str) -> str | None:
    """页面自身声明的吧名；只在明确指向目标吧时才采用 ``kw=`` 兜底。

    ``kw=`` 是弱信号（可能命中页面里的其他链接），所以它只有恰好是目标吧
    才被采信：确认归属是危险方向，宁可不确认，也不把别的吧当成目标吧。
    """
    forum = _mapping(data.get("forum"))
    name = str(forum.get("name") or "").strip()
    if name:
        return name if name.endswith("吧") else f"{name}吧"
    match = _KW.search(html)
    kw = _with_suffix(unquote(match.group(1)).strip()) if match is not None else None
    return kw if kw == TARGET_FORUM_NAME else None


def _with_suffix(name: str) -> str:
    return name if name.endswith("吧") else f"{name}吧"


def _author_name(author: Any) -> str | None:
    if isinstance(author, dict):
        name = str(author.get("name") or author.get("user_name") or "").strip()
        return name or None
    if isinstance(author, str):
        return author.strip() or None
    return None


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return _clean_text(content)
    if isinstance(content, list):
        parts = [
            _content_text(item.get("text")) if isinstance(item, dict) else _clean_text(str(item))
            for item in content
        ]
        return _clean_text(" ".join(part for part in parts if part))
    if isinstance(content, dict):
        return _content_text(content.get("text"))
    return ""


def _clean_text(value: str) -> str:
    if not value:
        return ""
    text = _SCRIPT_STYLE.sub(" ", value)
    text = _TAG.sub(" ", text)
    text = unescape(text)
    text = _WHITESPACE.sub(" ", text)
    return "\n".join(line.strip() for line in text.splitlines()).strip()


def _as_int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None
