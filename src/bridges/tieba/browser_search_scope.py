"""浏览器搜索页范围的状态映射与面向用户的说明。"""

from __future__ import annotations

from typing import Any

from bridges.contracts.modules import ModuleQueryStatus
from bridges.tieba.contracts import TiebaSearchScope

SCOPE_STOP_LABELS: dict[str, str] = {
    "pages_limit": "达到翻页上限",
    "duplicate_page": "重复页，已停止",
    "abnormal_page": "页码未前进，已停止",
    "page_gap": "页码跳号，已停止",
    "abnormal_empty_page": "空页但声明还有后续页，已停止",
    "no_scroll_container": "未找到翻页容器",
    "timeout": "翻页等待超时",
    "cancelled": "已停止",
    "access_restricted": "翻页触发访问限制",
    "dom_fallback": "使用页面卡片兜底，未确认分页",
}


def scope_dict(
    tab: str,
    terms: str,
    *,
    pages_read: int,
    pages_limit: int,
    visited: list[int],
    last_page: int | None,
    has_more: bool | None,
    stop_reason: str | None,
    note: str | None,
) -> dict[str, object]:
    return {
        "tab": "thread" if tab == "1" else "reply",
        "query": terms,
        "pages_read": pages_read,
        "pages_limit": pages_limit,
        "visited_pages": visited,
        "last_page": last_page,
        "has_more": has_more,
        "stop_reason": stop_reason,
        "note": note,
    }


def empty_scope(
    tab: str,
    terms: str,
    pages_limit: int,
    stop_reason: str,
    note: str,
) -> dict[str, object]:
    return scope_dict(
        tab,
        terms,
        pages_read=0,
        pages_limit=pages_limit,
        visited=[],
        last_page=None,
        has_more=None,
        stop_reason=stop_reason,
        note=note,
    )


def search_status(
    value: dict[str, Any],
) -> tuple[ModuleQueryStatus, str | None, str | None, bool]:
    code = str(value.get("error_code") or "")
    stop_reason = str(value.get("stop_reason") or "")
    if code:
        cancelled = code == "tieba_browser_cancelled"
        retryable = not cancelled and code not in {
            "tieba_browser_auth_required",
            "tieba_browser_auth_interrupted",
            "tieba_browser_auth_unverified",
            "tieba_browser_auth_status_unknown",
            "tieba_browser_collection_restricted",
            "tieba_browser_task_changed",
            "tieba_browser_run_invalidated",
            "tieba_browser_request_guard_failed",
        }
        return (
            ModuleQueryStatus.CANCELLED if cancelled else ModuleQueryStatus.ERROR,
            code,
            str(value.get("error_message") or "贴吧检索未完成。"),
            retryable,
        )
    if stop_reason == "cancelled":
        return (
            ModuleQueryStatus.CANCELLED,
            "tieba_browser_cancelled",
            "已停止贴吧搜索，已保留翻页前取得的候选。",
            False,
        )
    if stop_reason == "timeout":
        return (
            ModuleQueryStatus.TIMEOUT,
            "tieba_browser_timeout",
            "贴吧搜索翻页超时，已保留翻页前取得的候选。",
            True,
        )
    if stop_reason == "access_restricted":
        return (
            ModuleQueryStatus.ERROR,
            "tieba_browser_collection_restricted",
            "搜索翻页触发登录或安全验证，已保留翻页前取得的候选。",
            False,
        )
    return ModuleQueryStatus.SUCCESS, None, None, False


def scope_detail(scope: tuple[TiebaSearchScope, ...]) -> str:
    if not scope:
        return ""
    parts: list[str] = []
    for item in scope:
        label = "主题" if item.tab == "thread" else "回复"
        detail = f"{label}已读 {item.pages_read}/{item.pages_limit} 页"
        if item.has_more is True:
            detail += "，页面声明还有后续结果"
        elif item.has_more is False:
            detail += "，页面声明没有更多结果"
        if item.stop_reason:
            detail += f"（{SCOPE_STOP_LABELS.get(item.stop_reason, item.stop_reason)}）"
        parts.append(detail)
    return "；".join(parts) + "。"
