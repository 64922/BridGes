"""楼中楼预览标注与逐父楼层读取编排。"""

from __future__ import annotations

import threading

from playwright.sync_api import Page

from bridges.tieba.browser_nested import (
    NESTED_COUNT_CONFLICT,
    NESTED_NOT_ATTEMPTED,
    NESTED_PAGINATION_MISSING,
    read_nested_replies,
)
from bridges.tieba.browser_nested_protocol import _count, _positive_int


def _annotate_nested_previews(value: dict[str, object]) -> dict[str, object]:
    """DOM 兜底只标记预览；未知总数不能冒充楼中楼读完。"""
    raw = value.get("replies")
    if not isinstance(raw, list):
        return value
    replies = [dict(item) for item in raw if isinstance(item, dict)]
    parent_ids = {
        str(item.get("reply_id"))
        for item in replies
        if not item.get("nested") and item.get("reply_id")
    }
    preview_by_parent: dict[str, list[dict[str, object]]] = {}
    for item in replies:
        if not item.get("nested"):
            continue
        item["nested_preview"] = True
        parent_id = str(item.get("parent_reply_id") or "")
        if parent_id in parent_ids:
            preview_by_parent.setdefault(parent_id, []).append(item)
    for item in replies:
        if item.get("nested"):
            continue
        parent_id = str(item.get("reply_id") or "")
        if parent_id not in parent_ids:
            continue
        preview = preview_by_parent.get(parent_id, [])
        declared = _count(item.get("sub_post_number"))
        if declared is None and preview:
            item["nested_progress"] = _preview_progress(
                declared=None,
                preview_count=len(preview),
                code=NESTED_PAGINATION_MISSING,
                message=(
                    f"第 {_floor_of(item)} 楼页面未声明楼中楼总数，DOM 只保留预览，不声称读完。"
                ),
            )
        elif declared is None:
            item["nested_progress"] = _preview_progress(
                declared=None,
                preview_count=0,
                code=None,
                message=None,
                complete=True,
            )
        elif declared < len(preview):
            item["nested_progress"] = _preview_progress(
                declared=declared,
                preview_count=len(preview),
                code=NESTED_COUNT_CONFLICT,
                message=(
                    f"第 {_floor_of(item)} 楼楼中楼预览 {len(preview)} 条超过"
                    f"页面声明的 {declared} 条，未声称读完。"
                ),
            )
        elif declared <= len(preview):
            item["nested_progress"] = _preview_progress(
                declared=declared,
                preview_count=len(preview),
                code=None,
                message=None,
                complete=True,
            )
    value["replies"] = replies
    value["page_reply_count"] = len(replies)
    return value


def _enrich_nested_replies(
    value: dict[str, object],
    page: Page,
    *,
    thread_id: str,
    closed: threading.Event,
    deadline: float | None,
    pages_limit: int,
    parents_limit: int,
) -> dict[str, object]:
    """为每个父楼层补独立楼中楼进度，并只替换对应父楼层的预览。"""
    raw = value.get("replies")
    if not isinstance(raw, list):
        return value
    replies = [dict(item) for item in raw if isinstance(item, dict)]
    parents = [item for item in replies if not item.get("nested") and item.get("reply_id")]
    parent_ids = {str(item["reply_id"]) for item in parents}
    preview_by_parent: dict[str, list[dict[str, object]]] = {}
    for item in replies:
        if not item.get("nested"):
            continue
        parent_id = str(item.get("parent_reply_id") or "")
        if parent_id in parent_ids:
            preview_by_parent.setdefault(parent_id, []).append(item)
    thread_id = str(value.get("thread_id") or thread_id)
    forum_id = str(value.get("forum_id") or "") or None
    limit = max(0, min(int(parents_limit or 0), 20))
    candidates: list[tuple[int, dict[str, object], list[dict[str, object]]]] = []
    for index, parent in enumerate(parents):
        parent_id = str(parent["reply_id"])
        preview = preview_by_parent.get(parent_id, [])
        declared = _count(parent.get("sub_post_number"))
        if declared is None and preview:
            parent["nested_progress"] = _preview_progress(
                declared=None,
                preview_count=len(preview),
                code=NESTED_PAGINATION_MISSING,
                message=(
                    f"第 {_floor_of(parent)} 楼页面未声明楼中楼总数，只保留预览，不声称读完。"
                ),
            )
        elif declared is None:
            parent["nested_progress"] = _preview_progress(
                declared=None,
                preview_count=0,
                code=None,
                message=None,
                complete=True,
            )
        elif declared < len(preview):
            parent["nested_progress"] = _preview_progress(
                declared=declared,
                preview_count=len(preview),
                code="tieba_browser_nested_count_conflict",
                message=(
                    f"第 {_floor_of(parent)} 楼楼中楼预览 {len(preview)} 条超过"
                    f"页面声明的 {declared} 条，未继续读取。"
                ),
            )
        elif declared <= len(preview):
            parent["nested_progress"] = _preview_progress(
                declared=declared,
                preview_count=len(preview),
                code=None,
                message=None,
                complete=True,
            )
        else:
            candidates.append((index, parent, preview))
    ordered = sorted(
        candidates,
        key=lambda item: (
            -((_count(item[1].get("sub_post_number")) or 0) - len(item[2])),
            _floor_of(item[1]),
            item[0],
        ),
    )
    attempted: list[tuple[dict[str, object], list[dict[str, object]]]] = []
    for _index, parent, preview in ordered:
        if len(attempted) >= limit:
            parent["nested_progress"] = _preview_progress(
                declared=_count(parent.get("sub_post_number")),
                preview_count=len(preview),
                code=NESTED_NOT_ATTEMPTED,
                message=(
                    f"第 {_floor_of(parent)} 楼楼中楼仅保留页面预览"
                    f" {len(preview)} 条（页面声明"
                    f" {_count(parent.get('sub_post_number'))} 条）；"
                    "本页父楼层较多，按差异优先读取其余父楼层。"
                ),
            )
            continue
        if not thread_id:
            parent["nested_progress"] = _preview_progress(
                declared=_count(parent.get("sub_post_number")),
                preview_count=len(preview),
                code="tieba_browser_nested_parent_missing",
                message=(f"第 {_floor_of(parent)} 楼缺少帖子 ID，无法读取楼中楼，只保留页面预览。"),
            )
            continue
        read = read_nested_replies(
            page,
            thread_id=thread_id,
            forum_id=forum_id,
            parent=parent,
            preview_replies=preview,
            pages_limit=pages_limit,
            closed=closed,
            deadline=deadline,
        )
        parent["nested_progress"] = read.progress
        preview_by_parent[str(parent["reply_id"])] = list(read.replies)
        attempted.append((parent, preview))
    output: list[dict[str, object]] = []
    for item in replies:
        if item.get("nested"):
            parent_id = str(item.get("parent_reply_id") or "")
            if parent_id not in parent_ids:
                output.append(item)
            continue
        output.append(item)
        parent_id = str(item.get("reply_id") or "")
        output.extend(preview_by_parent.get(parent_id, []))
    value["replies"] = output
    value["page_reply_count"] = len(output)
    return value


def _preview_progress(
    *,
    declared: int | None,
    preview_count: int,
    code: str | None,
    message: str | None,
    complete: bool = False,
) -> dict[str, object]:
    state = "complete" if complete else "preview"
    return {
        "state": state,
        "total_replies": declared,
        "preview_count": preview_count,
        "read_count": preview_count if complete else 0,
        "pages_read": 0,
        "pages_limit": 0,
        "total_pages": None,
        "complete": complete,
        "error_code": code,
        "error_message": message,
    }


def _floor_of(item: dict[str, object]) -> str:
    value = _positive_int(item.get("floor"))
    return str(value) if value is not None else "未知楼层"
