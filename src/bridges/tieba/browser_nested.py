"""楼中楼独立分页：按父楼层读取、合并预览与已读页，维护独立游标与进度。"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from typing import Any

from playwright.sync_api import Page

from bridges.tieba.browser_nested_protocol import (
    DEFAULT_NESTED_PAGES_LIMIT,
    MAX_NESTED_PAGES_PER_PARENT,
    MAX_NESTED_PARENTS_PER_PAGE,
    MAX_NESTED_REPLIES_PER_PARENT,
    NESTED_AUTH_REQUIRED,
    NESTED_CANCELLED,
    NESTED_COUNT_CONFLICT,
    NESTED_CURSOR_MISMATCH,
    NESTED_DUPLICATE_CURSOR,
    NESTED_EMPTY_PAGE,
    NESTED_NOT_ATTEMPTED,
    NESTED_PAGE_FAILED,
    NESTED_PAGE_LIMIT,
    NESTED_PAGINATION_MISSING,
    NESTED_PARENT_MISMATCH,
    NESTED_PARENT_MISSING,
    NESTED_REPLY_LIMIT,
    NESTED_UNAVAILABLE,
    NestedPage,
    NestedPageError,
    NestedRead,
    _count,
    _positive_int,
    _text,
    fetch_nested_html,
    nested_comment_url,
    parse_nested_comment_fragment,
)


def read_nested_replies(
    page: Page,
    *,
    thread_id: str,
    forum_id: str | None,
    parent: dict[str, Any],
    preview_replies: Sequence[dict[str, Any]],
    pages_limit: int,
    closed: threading.Event,
    deadline: float | None,
    fetch_html: Callable[[Page, str, threading.Event, float | None], str] = fetch_nested_html,
) -> NestedRead:
    """按一个父楼层独立推进楼中楼页，返回已读发言与独立进度。"""
    parent_id = _text(parent.get("reply_id"))
    floor = _positive_int(parent.get("floor"))
    declared = _count(parent.get("sub_post_number"))
    preview = _preview_items(preview_replies, parent_id, floor)
    preview_count = len(preview)
    limit = max(0, min(int(pages_limit or 0), MAX_NESTED_PAGES_PER_PARENT))
    if not parent_id:
        return NestedRead(
            replies=tuple(preview),
            progress=_progress(
                state="preview" if preview else "none",
                total_replies=declared,
                preview_count=preview_count,
                pages_limit=limit,
                complete=not preview,
                code=NESTED_PARENT_MISSING,
                message="父楼层缺少稳定 ID，无法读取楼中楼，未按展示邻接关系猜测。",
            ),
        )
    if declared is not None and declared < preview_count:
        return NestedRead(
            replies=tuple(preview),
            progress=_progress(
                state="partial",
                total_replies=declared,
                preview_count=preview_count,
                pages_limit=limit,
                complete=False,
                code=NESTED_COUNT_CONFLICT,
                message=(
                    f"第 {_floor(floor)} 楼楼中楼预览 {preview_count} 条超过"
                    f"页面声明的 {declared} 条，未继续读取。"
                ),
            ),
        )
    if declared is not None and declared <= preview_count:
        return NestedRead(
            replies=tuple(preview),
            progress=_progress(
                state="complete",
                total_replies=declared,
                preview_count=preview_count,
                read_count=preview_count,
                pages_limit=limit,
                complete=True,
            ),
        )
    if declared is None and preview:
        return NestedRead(
            replies=tuple(preview),
            progress=_progress(
                state="preview",
                total_replies=None,
                preview_count=preview_count,
                pages_limit=limit,
                complete=False,
                code=NESTED_PAGINATION_MISSING,
                message=(f"第 {_floor(floor)} 楼页面未声明楼中楼总数，只保留预览，不声称读完。"),
            ),
        )
    if declared == 0 and not preview:
        return NestedRead(
            replies=(),
            progress=_progress(
                state="none",
                total_replies=0,
                preview_count=0,
                pages_limit=limit,
                complete=True,
            ),
        )
    if declared is None and not preview:
        return NestedRead(
            replies=(),
            progress=_progress(
                state="none",
                total_replies=None,
                preview_count=0,
                pages_limit=limit,
                complete=True,
            ),
        )
    if limit <= 0:
        return NestedRead(
            replies=tuple(preview),
            progress=_progress(
                state="preview",
                total_replies=declared,
                preview_count=preview_count,
                pages_limit=limit,
                complete=False,
                code=NESTED_NOT_ATTEMPTED,
                message=(
                    f"第 {_floor(floor)} 楼楼中楼只保留页面预览 {preview_count} 条"
                    f"（页面声明 {declared} 条），本轮未读取独立楼中楼页。"
                ),
            ),
        )

    items = list(preview)
    by_id = {str(item["reply_id"]): index for index, item in enumerate(items)}
    pages_read = 0
    expected_total = declared
    expected_pages: int | None = None
    previous_page: int | None = None
    complete = False
    stop_code: str | None = None
    stop_message: str | None = None
    for page_number in range(1, limit + 1):
        if closed.is_set():
            stop_code = NESTED_CANCELLED
            stop_message = _stop_message(floor, "读取已停止，已保留页面预览与已读页。")
            break
        if deadline is not None and time.monotonic() >= deadline:
            stop_code = NESTED_PAGE_FAILED
            stop_message = _stop_message(floor, "读取超时，已保留页面预览与已读页。")
            break
        url = nested_comment_url(thread_id, parent_id, forum_id, page_number)
        try:
            fragment = fetch_html(page, url, closed, deadline)
            value = parse_nested_comment_fragment(fragment, requested_page=page_number)
        except NestedPageError as error:
            stop_code = error.code
            stop_message = _stop_message(floor, error.message)
            break
        except InterruptedError:
            stop_code = NESTED_CANCELLED
            stop_message = _stop_message(floor, "读取已停止，已保留页面预览与已读页。")
            break
        except Exception:  # noqa: BLE001 - 单页失败不丢弃已读页
            stop_code = NESTED_PAGE_FAILED
            stop_message = _stop_message(floor, "读取失败，已保留页面预览与已读页。")
            break
        if not value.replies and value.current_page is None:
            stop_code = NESTED_EMPTY_PAGE
            stop_message = _stop_message(floor, f"第 {page_number} 页为空，未继续读取。")
            break
        if value.current_page is None or value.total_pages is None:
            stop_code = NESTED_PAGINATION_MISSING
            stop_message = _stop_message(floor, "缺少当前页或总页数，未继续读取。")
            break
        if previous_page is not None and value.current_page <= previous_page:
            stop_code = NESTED_DUPLICATE_CURSOR
            stop_message = _stop_message(
                floor,
                f"请求第 {page_number} 页却返回第 {value.current_page} 页，"
                "页码游标重复，未继续读取。",
            )
            break
        if value.current_page != page_number:
            stop_code = NESTED_CURSOR_MISMATCH
            stop_message = _stop_message(
                floor,
                f"请求第 {page_number} 页却返回第 {value.current_page} 页，"
                "页码游标不一致，未继续读取。",
            )
            break
        if page_number == 1:
            page_ids = {str(item["reply_id"]) for item in value.replies if item.get("reply_id")}
            preview_ids = {str(item["reply_id"]) for item in preview if item.get("reply_id")}
            if preview_ids and not preview_ids.intersection(page_ids):
                stop_code = NESTED_PARENT_MISMATCH
                stop_message = _stop_message(
                    floor,
                    "第 1 页与帖子页预览没有交集，无法确认属于该父楼层，已拒绝该响应。",
                )
                break
            expected_pages = value.total_pages
            if declared is not None and value.total_replies != declared:
                stop_code = NESTED_COUNT_CONFLICT
                stop_message = _stop_message(
                    floor,
                    f"页面声明的总数从 {declared} 变为 {value.total_replies}，已停止，不声称读完。",
                )
            expected_total = value.total_replies
        else:
            if expected_pages is not None and value.total_pages != expected_pages:
                stop_code = NESTED_PAGINATION_MISSING
                stop_message = _stop_message(
                    floor,
                    f"总页数从 {expected_pages} 变为 {value.total_pages}，未继续读取。",
                )
                break
            if expected_total is not None and value.total_replies != expected_total:
                stop_code = NESTED_COUNT_CONFLICT
                stop_message = _stop_message(
                    floor,
                    f"楼中楼总数从 {expected_total} 变为 {value.total_replies}，未继续读取。",
                )
        if page_number > value.total_pages:
            stop_code = NESTED_COUNT_CONFLICT
            stop_message = _stop_message(
                floor,
                f"请求第 {page_number} 页超出声明的 {value.total_pages} 页，未继续读取。",
            )
            break
        for raw in value.replies:
            item = _nested_item(raw, parent_id, floor)
            reply_id = str(item["reply_id"])
            index = by_id.get(reply_id)
            if index is None:
                by_id[reply_id] = len(items)
                items.append(item)
            else:
                items[index] = item
        pages_read += 1
        previous_page = value.current_page
        if value.missing_identity:
            stop_code = NESTED_COUNT_CONFLICT
            stop_message = _stop_message(
                floor,
                f"第 {page_number} 页有 {value.missing_identity} 条缺少稳定 ID，未继续读取。",
            )
            break
        if len(items) > MAX_NESTED_REPLIES_PER_PARENT:
            items = items[:MAX_NESTED_REPLIES_PER_PARENT]
            stop_code = NESTED_REPLY_LIMIT
            stop_message = _stop_message(
                floor,
                f"记录数达到上限 {MAX_NESTED_REPLIES_PER_PARENT} 条，未继续读取。",
            )
            break
        if stop_code is not None:
            break
        if value.current_page >= value.total_pages:
            complete = True
            break
    if not complete and stop_code is None:
        stop_code = NESTED_PAGE_LIMIT
        stop_message = _stop_message(
            floor,
            f"已读取 {pages_read} 页（页面声明共 {expected_pages or '未知'} 页），"
            f"本轮每层楼中楼页数上限为 {limit} 页，从第 {pages_read + 1} 页起未读取。",
        )
    if complete and expected_total is not None and len(items) != expected_total:
        complete = False
        stop_code = NESTED_COUNT_CONFLICT
        stop_message = _stop_message(
            floor,
            f"页面声明共 {expected_total} 条，实际记录 {len(items)} 条，不声称读完。",
        )
    if complete:
        state = "complete"
    elif pages_read:
        state = "partial"
    else:
        state = "preview"
    read_count = sum(1 for item in items if not item.get("nested_preview"))
    return NestedRead(
        replies=tuple(items),
        progress=_progress(
            state=state,
            total_replies=expected_total,
            preview_count=preview_count,
            read_count=read_count,
            pages_read=pages_read,
            pages_limit=limit,
            total_pages=expected_pages,
            complete=complete,
            code=stop_code,
            message=stop_message,
        ),
    )


def _preview_items(
    values: Sequence[dict[str, Any]], parent_id: str, floor: int | None
) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    seen: set[str] = set()
    for value in values:
        item = _nested_item(value, parent_id, floor, preview=True)
        reply_id = str(item["reply_id"])
        if reply_id in seen:
            continue
        seen.add(reply_id)
        items.append(item)
    return items


def _nested_item(
    value: dict[str, Any],
    parent_id: str,
    floor: int | None,
    *,
    preview: bool = False,
) -> dict[str, object]:
    return {
        **dict(value),
        "parent_reply_id": parent_id,
        "floor": floor,
        "nested": True,
        "nested_preview": preview,
    }


def _progress(
    *,
    state: str,
    total_replies: int | None,
    preview_count: int,
    read_count: int = 0,
    pages_read: int = 0,
    pages_limit: int = 0,
    total_pages: int | None = None,
    complete: bool,
    code: str | None = None,
    message: str | None = None,
) -> dict[str, object]:
    return {
        "state": state,
        "total_replies": total_replies,
        "preview_count": preview_count,
        "read_count": read_count,
        "pages_read": pages_read,
        "pages_limit": pages_limit,
        "total_pages": total_pages,
        "complete": complete,
        "error_code": code,
        "error_message": message,
    }


def _stop_message(floor: int | None, detail: str) -> str:
    prefix = f"第 {_floor(floor)} 楼楼中楼：" if floor is not None else "楼中楼："
    return prefix + detail


def _floor(floor: int | None) -> str:
    return str(floor) if floor is not None else "未知楼层"


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
    "read_nested_replies",
]
