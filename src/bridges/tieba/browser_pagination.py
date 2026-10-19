"""贴吧普通楼层分页的纯逻辑：页码推进、异常停止与跨页合并。

页面响应里的稳定身份（``reply_id``）是唯一的跨页去重依据；没有上游 ID 的
发言保持独立，绝不按相同正文合并。分页完整只由页面自身声明的当前页、
总页数与 ``has_more`` 决定，有限滚动或页数上限都不能冒充完整读取。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

PAGE_LIMIT = "tieba_browser_page_limit"
PAGINATION_MISSING = "tieba_browser_pagination_missing"
DUPLICATE_CURSOR = "tieba_browser_pagination_duplicate_cursor"
CURSOR_MISMATCH = "tieba_browser_pagination_cursor_mismatch"
EMPTY_PAGE = "tieba_browser_pagination_empty_page"
COUNT_CONFLICT = "tieba_browser_pagination_count_conflict"
PAGE_FAILED = "tieba_browser_page_failed"
WORKER_EXITED = "tieba_browser_worker_exited"
PAGINATION_UNAVAILABLE = "tieba_browser_pagination_unavailable"
REPLY_LIMIT = "tieba_browser_reply_limit"

MAX_MERGED_REPLIES = 480


@dataclass(frozen=True)
class PageStop:
    """一个有界的停止原因：稳定分类码与可展示的中文原因。"""

    code: str
    message: str


def page_url(url: str, page: int) -> str:
    """在帖子链接上设置 ``pn``，保留其他查询参数。"""
    parsed = urlparse(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key != "pn"
    ]
    query.append(("pn", str(page)))
    return urlunparse(parsed._replace(query=urlencode(query)))


def check_page(
    page: dict[str, Any],
    *,
    requested_page: int,
    previous_page: int | None,
    expected_total_pages: int | None,
    expected_total_replies: int | None,
) -> PageStop | None:
    """校验一页的游标、后继语义与计数；返回 None 表示可以继续。"""
    current = _positive_int(page.get("current_page"))
    has_more = _as_bool(page.get("has_more"))
    if current is None or has_more is None:
        return PageStop(
            PAGINATION_MISSING,
            f"第 {requested_page} 页缺少当前页或下一页标记，未继续读取。",
        )
    if previous_page is not None and current <= previous_page:
        return PageStop(
            DUPLICATE_CURSOR,
            f"请求第 {requested_page} 页却返回第 {current} 页，页码游标重复，未继续读取。",
        )
    if current != requested_page:
        return PageStop(
            CURSOR_MISMATCH,
            f"请求第 {requested_page} 页却返回第 {current} 页，页码游标不一致，未继续读取。",
        )
    total = _positive_int(page.get("total_pages"))
    if (
        expected_total_pages is not None
        and total is not None
        and total != expected_total_pages
    ):
        return PageStop(
            COUNT_CONFLICT,
            f"页面总页数从 {expected_total_pages} 变为 {total}，未继续读取。",
        )
    replies = _optional_int(page.get("total_replies"))
    if (
        expected_total_replies is not None
        and replies is not None
        and replies != expected_total_replies
    ):
        return PageStop(
            COUNT_CONFLICT,
            f"页面回复数从 {expected_total_replies} 变为 {replies}，未继续读取。",
        )
    if total is not None:
        if requested_page > total:
            return PageStop(
                COUNT_CONFLICT,
                f"请求第 {requested_page} 页超出页面声明的 {total} 页，未继续读取。",
            )
        if has_more and requested_page >= total:
            return PageStop(
                COUNT_CONFLICT,
                f"第 {requested_page} 页已到声明末页，却仍声明有后继页，未继续读取。",
            )
        if not has_more and requested_page < total:
            return PageStop(
                COUNT_CONFLICT,
                f"第 {requested_page} 页声明无后继页，但页面声明共 {total} 页，未继续读取。",
            )
    top_level = _optional_int(page.get("top_level_count")) or 0
    reply_count = _optional_int(page.get("page_reply_count")) or 0
    if requested_page > 1 and has_more and top_level == 0 and reply_count == 0:
        return PageStop(
            EMPTY_PAGE,
            f"第 {requested_page} 页没有内容却声明有后继页，未继续读取。",
        )
    return None


def merge_thread_pages(
    pages: list[dict[str, Any]],
    *,
    pages_limit: int,
    stop: PageStop | None = None,
    final: bool = True,
) -> dict[str, Any]:
    """合并已成功读取的页面，按上游 ID 去重并给出真实完成状态。"""
    if not pages:
        raise ValueError("至少需要一页成功读取的页面")
    limit = max(1, int(pages_limit or 1))
    first = pages[0]
    unique_replies: list[dict[str, Any]] = []
    index_by_id: dict[str, int] = {}
    for page in pages:
        for reply in page.get("replies", []):
            if not isinstance(reply, dict):
                continue
            reply_id = str(reply.get("reply_id") or "").strip()
            if reply_id and reply_id in index_by_id:
                index = index_by_id[reply_id]
                existing = unique_replies[index]
                if existing.get("nested_preview") and not reply.get("nested_preview"):
                    upgraded = dict(reply)
                    upgraded["nested_progress"] = _merge_nested_progress(
                        existing.get("nested_progress"),
                        reply.get("nested_progress"),
                    )
                    unique_replies[index] = upgraded
                else:
                    existing["nested_progress"] = _merge_nested_progress(
                        existing.get("nested_progress"),
                        reply.get("nested_progress"),
                    )
                continue
            if reply_id:
                index_by_id[reply_id] = len(unique_replies)
            unique_replies.append(dict(reply))
    if len(unique_replies) > MAX_MERGED_REPLIES:
        unique_replies = unique_replies[:MAX_MERGED_REPLIES]
        if stop is None:
            stop = PageStop(
                REPLY_LIMIT,
                f"本轮记录达到 {MAX_MERGED_REPLIES} 条回复上限，"
                "其余已读回复未纳入，不声称完整。",
            )
    loaded_reply_count = sum(
        1 for reply in unique_replies if not reply.get("nested")
    )
    total_pages = _positive_int(first.get("total_pages"))
    last_more = _as_bool(pages[-1].get("has_more"))
    if stop is None and final:
        stop = _final_stop(
            pages=pages,
            limit=limit,
            last_more=last_more,
            total_pages=total_pages,
        )
    complete = stop is None and last_more is False
    return {
        **first,
        "replies": unique_replies[:MAX_MERGED_REPLIES],
        "loaded_reply_count": loaded_reply_count,
        "pages_read": len(pages),
        "pages_limit": limit,
        "pages_complete": complete,
        "page_evidence": [
            {
                "page": index,
                "total_pages": _positive_int(page.get("total_pages")),
                "has_more": _as_bool(page.get("has_more")),
                "top_level_count": _optional_int(page.get("top_level_count")) or 0,
                "reply_count": _optional_int(page.get("page_reply_count")) or 0,
            }
            for index, page in enumerate(pages, start=1)
        ],
        "pagination_error_code": stop.code if stop is not None else None,
        "pagination_error_message": stop.message if stop is not None else None,
    }


def _merge_nested_progress(
    existing: Any, incoming: Any
) -> dict[str, Any] | None:
    if not isinstance(existing, dict):
        return incoming if isinstance(incoming, dict) else None
    if not isinstance(incoming, dict):
        return existing
    rank = {"none": 0, "preview": 1, "partial": 2, "complete": 3}
    existing_rank = rank.get(str(existing.get("state") or "none"), 0)
    incoming_rank = rank.get(str(incoming.get("state") or "none"), 0)
    if existing.get("complete") and not incoming.get("complete"):
        return existing
    if incoming.get("complete") or incoming_rank > existing_rank:
        return incoming
    if incoming_rank == existing_rank:
        if int(incoming.get("pages_read") or 0) > int(existing.get("pages_read") or 0):
            return incoming
        if int(incoming.get("read_count") or 0) > int(existing.get("read_count") or 0):
            return incoming
    return existing


def _final_stop(
    *,
    pages: list[dict[str, Any]],
    limit: int,
    last_more: bool | None,
    total_pages: int | None,
) -> PageStop | None:
    if last_more is True:
        if total_pages is not None and len(pages) >= total_pages:
            return PageStop(
                COUNT_CONFLICT,
                f"第 {len(pages)} 页已到声明末页，却仍声明有后继页，未继续读取。",
            )
        if len(pages) >= limit:
            declared = total_pages if total_pages is not None else "未知"
            return PageStop(
                PAGE_LIMIT,
                f"已读取第 1–{len(pages)} 页，页面声明共 {declared} 页；"
                f"本轮页数上限为 {limit} 页，从第 {len(pages) + 1} 页起未读取。",
            )
        return PageStop(
            PAGINATION_MISSING,
            f"分页推进在第 {len(pages) + 1} 页前停止，未声称全部读取。",
        )
    if last_more is False and total_pages is not None and len(pages) < total_pages:
        return PageStop(
            COUNT_CONFLICT,
            f"最后一页声明无后继页，但页面共声明 {total_pages} 页，未继续读取。",
        )
    return None


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value in (0, "0"):
        return False
    if value in (1, "1"):
        return True
    return None


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _positive_int(value: Any) -> int | None:
    number = _optional_int(value)
    return number if number is not None and number > 0 else None


__all__ = [
    "COUNT_CONFLICT",
    "CURSOR_MISMATCH",
    "DUPLICATE_CURSOR",
    "EMPTY_PAGE",
    "MAX_MERGED_REPLIES",
    "PAGE_FAILED",
    "PAGE_LIMIT",
    "PAGINATION_MISSING",
    "PAGINATION_UNAVAILABLE",
    "REPLY_LIMIT",
    "PageStop",
    "WORKER_EXITED",
    "check_page",
    "merge_thread_pages",
    "page_url",
]
