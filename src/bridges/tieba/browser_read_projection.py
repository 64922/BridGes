"""浏览器读取结果到公开读合同、嵌套范围与未读原因投影。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bridges.tieba.browser_nested import NESTED_PARENT_MISSING
from bridges.tieba.browser_pagination import MAX_MERGED_REPLIES
from bridges.tieba.contracts import (
    ReadStatus,
    TiebaPageEvidence,
    TiebaReadResult,
    TiebaReply,
)
from bridges.tieba.searching import canonical_url, thread_id_of


def read_projection(url: str, value: dict[str, Any]) -> TiebaReadResult:
    retrieved_at = datetime.now(UTC)
    if value.get("response_valid") is False:
        return _unrecognized_read(url, value, retrieved_at)
    replies: list[TiebaReply] = []
    original = str(value.get("original", "")).strip()
    if original:
        replies.append(
            TiebaReply(
                content=original[:2000],
                floor=_optional_int(value.get("original_floor")),
                reply_id=_optional_str(value.get("origin_reply_id")),
                posted_at=value.get("posted_at") or None,
                author=_optional_str(value.get("author")),
                is_original_poster=True,
            )
        )
    for item in value.get("replies", [])[:MAX_MERGED_REPLIES]:
        replies.append(
            TiebaReply(
                content=str(item.get("content") or "")[:600],
                floor=_optional_int(item.get("floor")),
                reply_id=_optional_str(item.get("reply_id")),
                parent_reply_id=_optional_str(item.get("parent_reply_id")),
                posted_at=item.get("posted_at"),
                author=_optional_str(item.get("author")),
                is_original_poster=False,
                nested=item.get("nested") is True,
                nested_preview=item.get("nested_preview") is True,
                nested_progress=item.get("nested_progress"),
            )
        )
    replies = [reply for reply in replies if reply.content.strip()]
    total_replies = _optional_count(value.get("total_replies"))
    loaded_reply_count = int(
        value.get("loaded_reply_count")
        or sum(not item.get("nested", False) for item in value.get("replies", []))
    )
    pages_read = int(value.get("pages_read") or 1)
    pages_limit = int(value.get("pages_limit") or 1)
    total_pages = _optional_int(value.get("total_pages"))
    page_evidence = _page_evidence(value.get("page_evidence"))
    pages_complete = _pages_complete(value, pages_read, total_pages, loaded_reply_count)
    pagination_error_code = _optional_str(value.get("pagination_error_code"))
    pagination_error_message = _optional_str(value.get("pagination_error_message"))
    scroll_rounds = int(value.get("scroll_rounds") or 0)
    scroll_rounds_limit = int(value.get("scroll_rounds_limit") or 0)
    (
        nested_complete,
        nested_incomplete_count,
        nested_error_code,
        nested_error_message,
    ) = _nested_scope(replies)
    has_more_pages = total_pages is not None and pages_read < total_pages
    partial = has_more_pages or not pages_complete or not nested_complete
    if total_replies is not None and total_replies > 0 and not replies:
        partial = True
    return TiebaReadResult(
        url=canonical_url(url),
        thread_id=thread_id_of(url),
        status=ReadStatus.PARTIAL if partial else ReadStatus.READ,
        forum_name=value.get("forum"),
        title=value.get("title"),
        replies=replies,
        pages_read=pages_read,
        pages_limit=pages_limit,
        pages_complete=pages_complete,
        page_evidence=page_evidence,
        scroll_rounds=scroll_rounds,
        scroll_rounds_limit=scroll_rounds_limit,
        total_pages=total_pages,
        total_replies=total_replies,
        floor_min=min((reply.floor for reply in replies if reply.floor is not None), default=None),
        floor_max=max((reply.floor for reply in replies if reply.floor is not None), default=None),
        retrieved_at=retrieved_at,
        excluded_wait_ms=int(value.get("excluded_wait_ms") or 0),
        error_code=(
            None
            if not partial
            else pagination_error_code or nested_error_code or "tieba_browser_partial_replies"
        ),
        error_message=(
            None
            if not partial
            else _partial_message(
                value,
                loaded_reply_count=loaded_reply_count,
                total_replies=total_replies,
                pages_read=pages_read,
                total_pages=total_pages,
                page_evidence=page_evidence,
                pages_complete=pages_complete,
                pagination_error_message=pagination_error_message,
                scroll_rounds=scroll_rounds,
                scroll_rounds_limit=scroll_rounds_limit,
                nested_error_message=nested_error_message,
            )
        ),
        pagination_error_code=None if not partial else pagination_error_code,
        pagination_error_message=None if not partial else pagination_error_message,
        nested_complete=nested_complete,
        nested_incomplete_count=nested_incomplete_count,
        nested_error_code=nested_error_code,
        nested_error_message=nested_error_message,
    )


def _nested_scope(
    replies: list[TiebaReply],
) -> tuple[bool, int, str | None, str | None]:
    by_id = {reply.reply_id: reply for reply in replies if reply.reply_id}
    incomplete: set[str] = set()
    codes: list[str] = []
    messages: list[str] = []
    for reply in replies:
        if not reply.nested:
            continue
        parent_id = reply.parent_reply_id
        if not parent_id:
            incomplete.add("unknown-parent")
            messages.append("有楼中楼没有父楼层标识，未按展示邻接关系猜测。")
            codes.append(NESTED_PARENT_MISSING)
            continue
        parent = by_id.get(parent_id)
        if parent is None:
            incomplete.add(parent_id)
            messages.append(f"父楼层 {parent_id} 不在本轮已读原文中。")
            codes.append(NESTED_PARENT_MISSING)
            continue
        progress = parent.nested_progress
        if progress is None:
            incomplete.add(parent_id)
            floor = parent.floor if parent.floor is not None else "?"
            messages.append(f"第 {floor} 楼楼中楼状态未知。")
            codes.append(NESTED_PARENT_MISSING)
            continue
        if progress.complete:
            continue
        incomplete.add(parent_id)
        if progress.error_code:
            codes.append(progress.error_code)
        if progress.error_message:
            messages.append(progress.error_message)
        else:
            messages.append(
                f"第 {parent.floor if parent.floor is not None else '?'} 楼楼中楼未读完。"
            )
    code = next((item for item in codes if item), None)
    message = "；".join(dict.fromkeys(messages)) or None
    return not incomplete, len(incomplete), code, message


def _partial_message(
    value: dict[str, Any],
    *,
    loaded_reply_count: int,
    total_replies: int | None,
    pages_read: int,
    total_pages: int | None,
    page_evidence: list[TiebaPageEvidence],
    pages_complete: bool,
    pagination_error_message: str | None,
    scroll_rounds: int,
    scroll_rounds_limit: int,
    nested_error_message: str | None,
) -> str:
    prefix = f"{pagination_error_message}。" if pagination_error_message else ""
    if value.get("source") == "structured":
        declared = total_pages if total_pages is not None else "未知"
        pages = (
            f"已成功读取 {pages_read} 页（页面声明共 {declared} 页）"
            if total_pages is not None
            else f"已成功读取 {pages_read} 页"
        )
        if page_evidence:
            read_pages = "、".join(str(item.page) for item in page_evidence)
            pages += f"；成功页：第 {read_pages} 页"
        state = "普通楼层页已全部读取" if pages_complete else "普通楼层页未读完"
        message = (
            f"{prefix}{state}；{pages}；"
            f"结构化响应记录 {loaded_reply_count} 条普通楼层，"
            f"页面声明 {total_replies if total_replies is not None else '未知'} 条；"
            "未读取的后续页、楼中楼或图片内容不作推断。"
        )
        if nested_error_message and nested_error_message not in message:
            message += f"楼中楼：{nested_error_message}"
        return message
    message = (
        f"{prefix}已读取 {loaded_reply_count} 条普通楼层，"
        f"页面声明 {total_replies if total_replies is not None else '未知'} 条；"
        f"向下加载 {scroll_rounds}/{scroll_rounds_limit} 轮；"
        "DOM 有限滚动不能替代分页完成判定，页面路径不提供楼中楼全量状态，"
        "未穷尽全部回复、楼中楼或图片内容，不作未读取内容的推断。"
    )
    if nested_error_message and nested_error_message not in message:
        message += f"楼中楼：{nested_error_message}"
    return message


def _page_evidence(value: Any) -> list[TiebaPageEvidence]:
    evidence: list[TiebaPageEvidence] = []
    if not isinstance(value, list):
        return evidence
    for raw in value:
        try:
            evidence.append(TiebaPageEvidence.model_validate(raw))
        except ValueError:
            continue
    return evidence


def _pages_complete(
    value: dict[str, Any],
    pages_read: int,
    total_pages: int | None,
    loaded_reply_count: int,
) -> bool:
    if "pages_complete" in value:
        return value.get("pages_complete") is True
    if total_pages is not None:
        return pages_read >= total_pages
    return int(value.get("total_replies") or 0) == 0 and loaded_reply_count == 0


def _unrecognized_read(url: str, value: dict[str, Any], retrieved_at: datetime) -> TiebaReadResult:
    return TiebaReadResult(
        url=canonical_url(url),
        thread_id=thread_id_of(url),
        status=ReadStatus.UNRECOGNIZED,
        forum_name=value.get("forum"),
        title=value.get("title"),
        error_code="tieba_read_unrecognized",
        error_message="页面响应结构校验失败，未取得可核实的帖子内容。",
        retrieved_at=retrieved_at,
    )


def _optional_int(value: Any) -> int | None:
    number = _optional_count(value)
    return number if number is not None and number > 0 else None


def _optional_count(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _optional_str(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None
