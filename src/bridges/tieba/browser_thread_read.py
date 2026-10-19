"""帖子读取：逐页导航、结构化解析、DOM 兜底与分页合并。"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from urllib.parse import urlparse

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as BrowserTimeoutError

from bridges.tieba.browser_auth import (
    COLLECTION_RESTRICTED,
    AuthSession,
    AuthWaitError,
    auth_required,
    request_timeout_ms,
)
from bridges.tieba.browser_capture import StructuredCapture, wait_for_payloads
from bridges.tieba.browser_dom import THREAD_DOM, THREAD_FLOORS
from bridges.tieba.browser_pagination import (
    PAGE_FAILED,
    PAGINATION_UNAVAILABLE,
    PageStop,
    check_page,
    merge_thread_pages,
    page_url,
)
from bridges.tieba.browser_scroll import (
    expand_thread_nested_replies,
    scroll_thread_replies,
)
from bridges.tieba.browser_structured import (
    StructuredResponseError,
    parse_thread_payload,
)


def wait_for_thread_page_or_auth(
    page: Page,
    closed: threading.Event,
    deadline: float | None = None,
) -> str | None:
    """帖子主体和认证标记任一出现后再分类，避免登录页被误报为超时。"""
    page.wait_for_function(
        """() => document.querySelector(
            '.pb-content-wrap, .login-guide-placeholder, #captcha, .pass-sms-wrap'
        ) ||
        /百度安全验证/.test(document.title)""",
        timeout=request_timeout_ms(closed, deadline, 9000),
    )
    return auth_required(page)


def _read_dom(
    page: Page,
    scroll_rounds_limit: int,
    closed: threading.Event,
    deadline: float | None,
) -> dict[str, object]:
    limit = max(1, min(int(scroll_rounds_limit or 1), 8))
    scroll_rounds = scroll_thread_replies(page, limit, closed, deadline)
    expand_thread_nested_replies(page, closed, deadline)
    result = page.evaluate(THREAD_DOM)
    if not isinstance(result, dict):
        raise StructuredResponseError("帖子页面未返回可识别的 DOM 内容")
    forum = str(result.get("forum") or "").strip()
    title = str(result.get("title") or "").strip()
    replies = result.get("replies")
    original = str(result.get("original") or "").strip()
    if not forum or not (title or original or (isinstance(replies, list) and replies)):
        raise StructuredResponseError("帖子页面 DOM 缺少吧名、标题或正文")
    reply_count = len(replies) if isinstance(replies, list) else 0
    top_level_count = int(result.get("loaded_reply_count") or 0)
    return {
        **{str(key): value for key, value in result.items()},
        "scroll_rounds": scroll_rounds,
        "scroll_rounds_limit": limit,
        "pages_read": 1,
        "pages_limit": 1,
        "pages_complete": False,
        "page_evidence": [
            {
                "page": 1,
                "total_pages": None,
                "has_more": None,
                "top_level_count": top_level_count,
                "reply_count": reply_count,
            }
        ],
        "pagination_error_code": PAGINATION_UNAVAILABLE,
        "pagination_error_message": (
            "DOM 只补充有限滚动范围，未声称读取全部页。"
        ),
        "response_valid": True,
        "source": "dom",
    }


def _read_thread_page(
    page: Page,
    target: str,
    page_number: int,
    closed: threading.Event,
    auth: AuthSession,
) -> tuple[dict[str, object], Page]:
    capture = StructuredCapture(page, "/c/f/pb/page_pc").start()
    try:
        page.goto(
            target,
            wait_until="domcontentloaded",
            timeout=request_timeout_ms(closed, auth.deadline, 16000),
        )
        reason = wait_for_thread_page_or_auth(page, closed, auth.deadline)
        if reason:
            capture.stop()
            page = auth.recover(page, reason=reason, target_url=target)
            capture = StructuredCapture(page, "/c/f/pb/page_pc").start()
            page.goto(
                target,
                wait_until="domcontentloaded",
                timeout=request_timeout_ms(closed, auth.deadline, 16000),
            )
            if wait_for_thread_page_or_auth(page, closed, auth.deadline):
                raise AuthWaitError(COLLECTION_RESTRICTED, reason="collection")
        page.wait_for_selector(
            ".pb-content-wrap",
            state="attached",
            timeout=request_timeout_ms(closed, auth.deadline, 9000),
        )
        wait_for_payloads(capture.payloads, page, closed, auth.deadline, 2500)
    finally:
        capture.stop()
    floors = page.evaluate(THREAD_FLOORS)
    if not isinstance(floors, dict):
        floors = {}
    failure: StructuredResponseError | None = None
    for payload in capture.payloads:
        try:
            value = parse_thread_payload(
                payload,
                url=target,
                floors={str(key): int(value) for key, value in floors.items()},
            )
        except (StructuredResponseError, TypeError, ValueError) as error:
            if isinstance(error, StructuredResponseError):
                failure = failure or error
            else:
                failure = failure or StructuredResponseError("帖子响应字段类型不符")
            continue
        current = value.get("current_page")
        if current is not None and current != page_number:
            failure = failure or StructuredResponseError(
                f"请求第 {page_number} 页却返回第 {current} 页"
            )
            continue
        return value, page
    raise failure or StructuredResponseError("帖子页面未返回可识别的结构化内容")


def _page_stop(page_number: int, error: Exception) -> PageStop:
    if isinstance(error, AuthWaitError):
        reason = "认证等待未完成"
    elif isinstance(error, BrowserTimeoutError):
        reason = "页面加载超时"
    elif isinstance(error, InterruptedError):
        reason = "读取已停止"
    elif isinstance(error, StructuredResponseError):
        reason = str(error) or "页面结构无法解析"
    else:
        reason = "页面加载或解析失败"
    return PageStop(
        PAGE_FAILED,
        f"第 {page_number} 页读取失败：{reason}；已保留此前成功页证据。",
    )


def read_thread(
    page: Page,
    url: str,
    scroll_rounds_limit: int,
    closed: threading.Event,
    auth: AuthSession,
    *,
    pages_limit: int = 2,
    progress: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, object]:
    parsed = urlparse(url)
    if parsed.hostname != "tieba.baidu.com" or not parsed.path.startswith("/p/"):
        raise ValueError("只允许读取贴吧帖子")
    limit = max(1, int(pages_limit or 1))
    pages: list[dict[str, object]] = []
    stop: PageStop | None = None
    expected_total_pages: int | None = None
    expected_total_replies: int | None = None
    previous_page: int | None = None
    for page_number in range(1, limit + 1):
        if closed.is_set():
            if not pages:
                raise InterruptedError("tieba_browser_cancelled")
            stop = PageStop(
                PAGE_FAILED,
                f"读取在第 {page_number} 页前停止；已保留此前成功页证据。",
            )
            break
        if auth.deadline is not None and time.monotonic() >= auth.deadline:
            if not pages:
                raise BrowserTimeoutError("贴吧浏览器运行预算已耗尽")
            stop = PageStop(
                PAGE_FAILED,
                f"读取在第 {page_number} 页前超时；已保留此前成功页证据。",
            )
            break
        target = page_url(url, page_number)
        try:
            value, page = _read_thread_page(
                page, target, page_number, closed, auth
            )
        except (StructuredResponseError, TypeError, ValueError) as error:
            if not pages:
                try:
                    return _read_dom(
                        page, scroll_rounds_limit, closed, auth.deadline
                    )
                except StructuredResponseError as dom_error:
                    raise error from dom_error
            stop = _page_stop(page_number, error)
            break
        except Exception as error:  # 后续页失败保留已读页
            if not pages:
                raise
            stop = _page_stop(page_number, error)
            break
        if page_number == 1:
            expected_total_pages = _optional_int(value.get("total_pages"))
            expected_total_replies = _optional_int(value.get("total_replies"))
        check = check_page(
            value,
            requested_page=page_number,
            previous_page=previous_page,
            expected_total_pages=expected_total_pages,
            expected_total_replies=expected_total_replies,
        )
        pages.append(value)
        previous_page = page_number
        if progress is not None:
            progress(
                merge_thread_pages(
                    pages,
                    pages_limit=limit,
                    final=page_number >= limit,
                )
            )
        if check is not None:
            stop = check
            break
        if value.get("has_more") is not True:
            break
    return merge_thread_pages(pages, pages_limit=limit, stop=stop)


def _optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None
