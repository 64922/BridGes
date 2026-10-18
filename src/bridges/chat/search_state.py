"""聊天搜索卡片与消息终态的共同收敛规则。

搜索投影在生成中会携带 ``can_cancel``；消息一旦失败或停止，读取与
SSE 都必须看到同一份终态，不能继续把卡片投影成正在检索。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from bridges.arxiv_mcp.contracts import ArxivSearchStatus
from bridges.state_copy import WEB_SEARCH_CANCELLED_TEXT, render_state_copy
from bridges.web_search.contracts import WebSearchStatus

_ARXIV_IN_FLIGHT = {
    ArxivSearchStatus.LOADING.value,
    ArxivSearchStatus.RECOVERY.value,
}
_WEB_IN_FLIGHT = {
    WebSearchStatus.LOADING.value,
    WebSearchStatus.RECOVERY.value,
}


def cancelled_web_search(
    web_search: dict[str, Any] | None, now: datetime
) -> dict[str, Any] | None:
    """把已触发的公网搜索与停止终态一起收敛为取消。"""
    if web_search is None:
        return None
    cancelled = dict(web_search)
    cancelled.update(
        {
            "status": WebSearchStatus.CANCELLED.value,
            "results": [],
            "searched_at": now.isoformat(),
            "error_code": "web_search_cancelled",
            "error_message": WEB_SEARCH_CANCELLED_TEXT,
            "can_retry": False,
            "can_cancel": False,
        }
    )
    return cancelled


def cancelled_arxiv_search(
    arxiv_search: dict[str, Any] | None, now: datetime
) -> dict[str, Any] | None:
    """把 arXiv 搜索与停止终态一起收敛为取消。"""
    if arxiv_search is None:
        return None
    cancelled = dict(arxiv_search)
    cancelled.update(
        {
            "status": ArxivSearchStatus.CANCELLED.value,
            "papers": [],
            "searched_at": now.isoformat(),
            "error_code": None,
            "error_message": render_state_copy("error.arxiv_cancelled"),
            "upstream_status": "cancelled",
            "can_retry": False,
            "can_cancel": False,
        }
    )
    return cancelled


def interrupted_web_search(
    web_search: dict[str, Any] | None, now: datetime
) -> dict[str, Any] | None:
    """失败终态下，把仍在检索的公网搜索收敛为已中断。"""
    if web_search is None:
        return None
    if str(web_search.get("status")) not in _WEB_IN_FLIGHT:
        return web_search
    interrupted = dict(web_search)
    interrupted.update(
        {
            "status": WebSearchStatus.ERROR.value,
            "results": [],
            "searched_at": now.isoformat(),
            "error_code": "web_search_interrupted",
            "error_message": render_state_copy("error.web_search_interrupted"),
            "can_retry": False,
            "can_cancel": False,
        }
    )
    return interrupted


def interrupted_arxiv_search(
    arxiv_search: dict[str, Any] | None, now: datetime
) -> dict[str, Any] | None:
    """失败终态下，把仍在检索的 arXiv 搜索收敛为已中断。"""
    if arxiv_search is None:
        return None
    if str(arxiv_search.get("status")) not in _ARXIV_IN_FLIGHT:
        return arxiv_search
    interrupted = dict(arxiv_search)
    interrupted.update(
        {
            "status": ArxivSearchStatus.ERROR.value,
            "papers": [],
            "searched_at": now.isoformat(),
            "error_code": "arxiv_search_interrupted",
            "error_message": render_state_copy("error.arxiv_search_interrupted"),
            "upstream_status": "interrupted",
            "can_retry": False,
            "can_cancel": False,
        }
    )
    return interrupted


def converge_terminal_searches(
    web_search: dict[str, Any] | None,
    arxiv_search: dict[str, Any] | None,
    now: datetime,
    *,
    cancelled: bool,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """按终态一次性收敛两种搜索卡片，读取与 SSE 共用同一规则。"""
    if cancelled:
        return (
            cancelled_web_search(web_search, now),
            cancelled_arxiv_search(arxiv_search, now),
        )
    return (
        interrupted_web_search(web_search, now),
        interrupted_arxiv_search(arxiv_search, now),
    )


__all__ = [
    "cancelled_arxiv_search",
    "cancelled_web_search",
    "converge_terminal_searches",
    "interrupted_arxiv_search",
    "interrupted_web_search",
]
