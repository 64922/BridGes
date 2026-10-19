"""楼中楼片段协议：解析、请求、稳定身份与分页元数据。

现代帖子页只给楼中楼首批预览。旧式 ``/p/comment`` 片段仍由同一登录会话
返回稳定的 ``spid``、正文、时间与分页器；本模块把它作为唯一的楼中楼
分页来源。每个父楼层维护自己的页码与完成状态，预览与已读页按上游 ID 合并，
绝不按正文或展示邻接关系推算父楼层与作者关系。
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlencode

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as BrowserTimeoutError

from bridges.tieba.browser_auth import request_timeout_ms

NESTED_PAGE_LIMIT = "tieba_browser_nested_page_limit"
NESTED_PAGE_FAILED = "tieba_browser_nested_page_failed"
NESTED_PAGINATION_MISSING = "tieba_browser_nested_pagination_missing"
NESTED_DUPLICATE_CURSOR = "tieba_browser_nested_duplicate_cursor"
NESTED_CURSOR_MISMATCH = "tieba_browser_nested_cursor_mismatch"
NESTED_COUNT_CONFLICT = "tieba_browser_nested_count_conflict"
NESTED_EMPTY_PAGE = "tieba_browser_nested_empty_page"
NESTED_PARENT_MISMATCH = "tieba_browser_nested_parent_mismatch"
NESTED_PARENT_MISSING = "tieba_browser_nested_parent_missing"
NESTED_AUTH_REQUIRED = "tieba_browser_nested_auth_required"
NESTED_CANCELLED = "tieba_browser_nested_cancelled"
NESTED_NOT_ATTEMPTED = "tieba_browser_nested_not_attempted"
NESTED_REPLY_LIMIT = "tieba_browser_nested_reply_limit"
NESTED_UNAVAILABLE = "tieba_browser_nested_unavailable"

DEFAULT_NESTED_PAGES_LIMIT = 2
MAX_NESTED_PAGES_PER_PARENT = 3
MAX_NESTED_PARENTS_PER_PAGE = 6
MAX_NESTED_REPLIES_PER_PARENT = 60
MAX_NESTED_CONTENT_CHARS = 600

_AUTH_MARKERS = (
    "百度安全验证",
    "安全验证",
    "验证码",
    "passport.baidu.com",
    "请登录",
)


@dataclass(frozen=True)
class NestedPage:
    """一页楼中楼片段的真实分页元数据与发言。"""

    replies: tuple[dict[str, object], ...]
    current_page: int | None
    total_pages: int | None
    total_replies: int | None
    missing_identity: int = 0


@dataclass(frozen=True)
class NestedRead:
    """一个父楼层的楼中楼读取结果与独立进度。"""

    replies: tuple[dict[str, object], ...]
    progress: dict[str, object]


class NestedPageError(ValueError):
    """楼中楼页无法采信时的稳定分类错误。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _NestedFragmentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.replies: list[dict[str, object]] = []
        self.current_page: int | None = None
        self.total_pages: int | None = None
        self.total_replies: int | None = None
        self.missing_identity = 0
        self._current: dict[str, object] | None = None
        self._content_level: int | None = None
        self._time_level: int | None = None
        self._span_depth = 0
        self._content_parts: list[str] = []
        self._time_parts: list[str] = []
        self._pager_active = False
        self._pager_level: int | None = None
        self._pager_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        classes = set((values.get("class") or "").split())
        if tag == "li":
            if "lzl_single_post" in classes:
                self._finish_reply()
                self._start_reply(values.get("data-field"))
            elif "lzl_li_pager" in classes:
                self._finish_reply()
                self._start_pager(values.get("data-field"))
        if tag == "span":
            self._span_depth += 1
            if self._current is not None and "lzl_content_main" in classes:
                self._content_level = self._span_depth
                self._content_parts = []
            elif self._current is not None and "lzl_time" in classes:
                self._time_level = self._span_depth
                self._time_parts = []
            if self._pager_active and "tP" in classes:
                self._pager_level = self._span_depth
                self._pager_parts = []
        elif tag == "br" and self._content_level is not None:
            self._content_parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag == "span":
            current = self._current
            if (
                current is not None
                and self._content_level is not None
                and self._span_depth <= self._content_level
            ):
                current["content"] = _clean("".join(self._content_parts))
                self._content_level = None
                self._content_parts = []
            if (
                current is not None
                and self._time_level is not None
                and self._span_depth <= self._time_level
            ):
                current["posted_at"] = _clean("".join(self._time_parts)) or None
                self._time_level = None
                self._time_parts = []
            if self._pager_level is not None and self._span_depth <= self._pager_level:
                self.current_page = _positive_int("".join(self._pager_parts))
                self._pager_level = None
                self._pager_parts = []
            self._span_depth = max(0, self._span_depth - 1)
        elif tag == "li":
            if self._current is not None:
                self._finish_reply()
            if self._pager_active:
                self._pager_active = False

    def handle_data(self, data: str) -> None:
        if self._content_level is not None:
            self._content_parts.append(data)
        if self._time_level is not None:
            self._time_parts.append(data)
        if self._pager_level is not None:
            self._pager_parts.append(data)

    def close(self) -> None:
        super().close()
        self._finish_reply()

    def _start_reply(self, raw: str | None) -> None:
        field = _data_field(raw)
        reply_id = _text(field.get("spid"))
        if not reply_id:
            self.missing_identity += 1
        self._current = {
            "reply_id": reply_id or None,
            "author": _text(field.get("showname")) or _text(field.get("user_name")) or None,
            "content": "",
            "posted_at": None,
        }

    def _start_pager(self, raw: str | None) -> None:
        field = _data_field(raw)
        self.total_replies = _count(field.get("total_num"))
        self.total_pages = _positive_int(field.get("total_page"))
        self._pager_active = True

    def _finish_reply(self) -> None:
        current = self._current
        self._current = None
        self._content_level = None
        self._time_level = None
        self._content_parts = []
        self._time_parts = []
        if current is None:
            return
        content = _text(current.get("content"))
        if not content:
            self.missing_identity += 1
            return
        if not current.get("reply_id"):
            return
        self.replies.append(
            {
                "reply_id": current["reply_id"],
                "author": current.get("author"),
                "content": content[:MAX_NESTED_CONTENT_CHARS],
                "posted_at": current.get("posted_at"),
            }
        )


def nested_comment_url(
    thread_id: str, parent_reply_id: str, forum_id: str | None, page: int
) -> str:
    """构造只属于一个父楼层的楼中楼分页地址。"""
    query = {
        "tid": thread_id,
        "pid": parent_reply_id,
    }
    if forum_id:
        query["fid"] = forum_id
    query["pn"] = str(max(1, int(page)))
    return "https://tieba.baidu.com/p/comment?" + urlencode(query)


def parse_nested_comment_fragment(fragment: str, *, requested_page: int) -> NestedPage:
    """解析楼中楼片段；认证页与不可信结构给出稳定错误。"""
    parser = _NestedFragmentParser()
    try:
        parser.feed(fragment)
        parser.close()
    except (ValueError, TypeError) as error:
        raise NestedPageError(
            NESTED_PAGE_FAILED,
            f"楼中楼第 {requested_page} 页结构无法解析。",
        ) from error
    if not parser.replies and not parser._pager_active:
        lowered = fragment.casefold()
        if any(marker.casefold() in lowered for marker in _AUTH_MARKERS):
            raise NestedPageError(
                NESTED_AUTH_REQUIRED,
                "楼中楼读取需要登录或安全验证，已保留帖子页预览。",
            )
        return NestedPage(
            replies=(),
            current_page=None,
            total_pages=None,
            total_replies=None,
            missing_identity=parser.missing_identity,
        )
    current_page = parser.current_page
    if current_page is None and parser.total_pages == 1:
        current_page = 1
    return NestedPage(
        replies=tuple(parser.replies),
        current_page=current_page,
        total_pages=parser.total_pages,
        total_replies=parser.total_replies,
        missing_identity=parser.missing_identity,
    )


def fetch_nested_html(
    page: Page,
    url: str,
    closed: threading.Event,
    deadline: float | None,
) -> str:
    """在已登录帖子会话内取回楼中楼片段；失败时只返回稳定分类。"""
    try:
        timeout_ms = request_timeout_ms(closed, deadline, 9000)
    except InterruptedError as error:
        raise NestedPageError(
            NESTED_CANCELLED,
            "楼中楼读取已停止，已保留页面预览与已读页。",
        ) from error
    except BrowserTimeoutError as error:
        raise NestedPageError(
            NESTED_PAGE_FAILED,
            "楼中楼读取超时，已保留页面预览与已读页。",
        ) from error
    try:
        value = page.evaluate(
            """async ({url, timeout_ms}) => {
                const controller = new AbortController();
                const timer = setTimeout(() => controller.abort(), timeout_ms);
                try {
                    const response = await fetch(url, {
                        credentials: 'include',
                        headers: {'X-Requested-With': 'XMLHttpRequest'},
                        signal: controller.signal
                    });
                    return {status: response.status, text: await response.text()};
                } catch (error) {
                    return {status: 0, text: String(error)};
                } finally {
                    clearTimeout(timer);
                }
            }""",
            {"url": url, "timeout_ms": timeout_ms},
        )
    except InterruptedError as error:
        raise NestedPageError(
            NESTED_CANCELLED,
            "楼中楼读取已停止，已保留页面预览与已读页。",
        ) from error
    except Exception as error:  # noqa: BLE001 - 浏览器错误只转为稳定分类
        if closed.is_set():
            raise NestedPageError(
                NESTED_CANCELLED,
                "楼中楼读取已停止，已保留页面预览与已读页。",
            ) from error
        raise NestedPageError(
            NESTED_PAGE_FAILED,
            "楼中楼页请求失败或超时，已保留页面预览与已读页。",
        ) from error
    if not isinstance(value, dict):
        raise NestedPageError(
            NESTED_PAGE_FAILED,
            "楼中楼页没有返回可识别内容，已保留页面预览与已读页。",
        )
    status = _count(value.get("status"))
    text = value.get("text")
    if status in {401, 403}:
        raise NestedPageError(
            NESTED_AUTH_REQUIRED,
            "楼中楼读取需要登录或安全验证，已保留帖子页预览。",
        )
    if status != 200 or not isinstance(text, str):
        raise NestedPageError(
            NESTED_PAGE_FAILED,
            "楼中楼页请求失败或超时，已保留页面预览与已读页。",
        )
    return text


def _data_field(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _clean(value: str) -> str:
    return " ".join(value.replace("\u3000", " ").split()).strip()


def _text(value: Any) -> str:
    return _clean(str(value)) if value is not None else ""


def _count(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _positive_int(value: Any) -> int | None:
    number = _count(value)
    return number if number is not None and number > 0 else None


__all__ = [
    "DEFAULT_NESTED_PAGES_LIMIT",
    "MAX_NESTED_PAGES_PER_PARENT",
    "MAX_NESTED_PARENTS_PER_PAGE",
    "MAX_NESTED_REPLIES_PER_PARENT",
    "NESTED_AUTH_REQUIRED",
    "NESTED_CANCELLED",
    "NESTED_COUNT_CONFLICT",
    "NESTED_CURSOR_MISMATCH",
    "NESTED_DUPLICATE_CURSOR",
    "NESTED_EMPTY_PAGE",
    "NESTED_NOT_ATTEMPTED",
    "NESTED_PAGE_FAILED",
    "NESTED_PAGE_LIMIT",
    "NESTED_PAGINATION_MISSING",
    "NESTED_PARENT_MISMATCH",
    "NESTED_PARENT_MISSING",
    "NESTED_REPLY_LIMIT",
    "NESTED_UNAVAILABLE",
    "NestedPage",
    "NestedPageError",
    "NestedRead",
    "fetch_nested_html",
    "nested_comment_url",
    "parse_nested_comment_fragment",
]
