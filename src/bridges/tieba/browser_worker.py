"""隔离的贴吧浏览器采集进程：搜索主题与回复，读取有限范围的帖子内容。"""

from __future__ import annotations

import json
import queue
import sys
import threading
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, sync_playwright
from playwright.sync_api import TimeoutError as BrowserTimeoutError

from bridges.runtime.lock import DataDirectoryLock, RuntimeLockError
from bridges.tieba.browser_auth import (
    AUTH_INTERRUPTED,
    AUTH_REQUIRED,
    AUTH_STATUS_UNKNOWN,
    COLLECTION_RESTRICTED,
    AuthSession,
)
from bridges.tieba.browser_dom import SEARCH_DOM
from bridges.tieba.browser_nested import (
    DEFAULT_NESTED_PAGES_LIMIT,
    MAX_NESTED_PARENTS_PER_PAGE,
)
from bridges.tieba.browser_requests import BrowserRequestTracker
from bridges.tieba.browser_runtime import account_profile_is_fenced
from bridges.tieba.browser_search import run_search
from bridges.tieba.browser_structured import StructuredResponseError
from bridges.tieba.browser_thread_read import (
    read_thread,
)
from bridges.tieba.browser_thread_read import (
    wait_for_thread_page_or_auth as wait_for_thread_page_or_auth,
)

__all__ = ["SEARCH_DOM", "main", "search"]


def emit(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False), flush=True)




def search(
    page: Page,
    terms: str,
    closed: threading.Event,
    deadline: float | None,
    auth: AuthSession,
) -> list[dict[str, object]]:
    """兼容入口：只返回候选；分页边界由 ``run_search`` 返回。"""
    result, _ = run_search(page, terms, closed, deadline, auth)
    if result.get("stop_reason") == "cancelled":
        raise InterruptedError("tieba_browser_cancelled")
    raw_hits = result.get("hits")
    if not isinstance(raw_hits, list):
        return []
    return [item for item in raw_hits if isinstance(item, dict)]


def _run_action(
    context: BrowserContext,
    page: Page,
    payload: dict[str, object],
    closed: threading.Event,
    request_tracker: BrowserRequestTracker,
) -> tuple[dict[str, object], Page, float | None]:
    page.set_default_timeout(8000)
    action = payload.get("action")
    raw_deadline = payload.get("deadline_monotonic")
    deadline = float(raw_deadline) if isinstance(raw_deadline, (float, int)) else None
    auth = AuthSession(
        closed=closed,
        deadline=deadline,
        emit=emit,
        deadline_changed=request_tracker.set_deadline,
    )
    page = auth.ensure(page)
    if action == "search":
        result, page = run_search(
            page, str(payload["terms"]), closed, auth.deadline, auth
        )
    elif action == "read":
        raw_scroll_limit = payload.get("scroll_rounds_limit")
        scroll_limit = int(str(raw_scroll_limit or 8))
        raw_pages_limit = payload.get("pages_limit")
        pages_limit = int(str(raw_pages_limit or 2))
        raw_nested_pages = payload.get("nested_pages_limit")
        nested_pages_limit = (
            int(str(raw_nested_pages))
            if raw_nested_pages is not None
            else DEFAULT_NESTED_PAGES_LIMIT
        )
        raw_nested_parents = payload.get("nested_parents_limit")
        nested_parents_limit = (
            int(str(raw_nested_parents))
            if raw_nested_parents is not None
            else MAX_NESTED_PARENTS_PER_PAGE
        )
        result = read_thread(
            page,
            str(payload["url"]),
            scroll_limit,
            closed,
            auth,
            pages_limit=pages_limit,
            nested_pages_limit=nested_pages_limit,
            nested_parents_limit=nested_parents_limit,
            progress=lambda value: emit({"progress": value}),
        )
    else:
        raise ValueError("不支持的动作")
    return result, page, auth.deadline


def _error_payload(error: Exception) -> dict[str, str]:
    if isinstance(error, InterruptedError):
        return {
            "error": "tieba_browser_cancelled",
            "message": "已停止贴吧浏览器读取。",
        }
    if isinstance(error, PermissionError):
        code = str(error)
        reason = getattr(error, "reason", None)
        if code not in {
            AUTH_REQUIRED,
            AUTH_INTERRUPTED,
            AUTH_STATUS_UNKNOWN,
            COLLECTION_RESTRICTED,
            "tieba_browser_auth_unverified",
        }:
            code = AUTH_STATUS_UNKNOWN
        if code == "tieba_browser_auth_unverified":
            message = "贴吧页面可读，但无法独立确认专用会话的认证状态；本轮未继续采集。"
        elif code == AUTH_STATUS_UNKNOWN:
            message = (
                "无法确认专用贴吧会话的登录状态（网络或接口异常）；"
                "本轮没有打开登录页，请稍后重试。"
            )
        elif code == COLLECTION_RESTRICTED:
            message = (
                "专用贴吧会话已登录，但当前采集入口仍被限制；"
                "本轮未取得新内容，请稍后重试。"
            )
        elif code == AUTH_INTERRUPTED and reason == "verification":
            message = (
                "贴吧安全验证等待已中断；任务已保留，"
                "请在聊天中说“继续”重试。"
            )
        else:
            message = (
                "贴吧登录等待已中断；任务已保留，"
                "请在聊天中说“继续”重试。"
            )
        return {
            "error": code,
            "message": message,
        }
    if isinstance(error, StructuredResponseError):
        return {
            "error": "tieba_browser_response_invalid",
            "message": "贴吧页面响应结构校验失败，未取得可核实的帖子内容。",
        }
    if isinstance(error, BrowserTimeoutError):
        return {
            "error": "tieba_browser_timeout",
            "message": "贴吧页面加载超时或结构已变化，请稍后重试。",
        }
    return {
        "error": "tieba_browser_unavailable",
        "message": "专用浏览器未取得可识别页面，请确认 Chrome 已安装并重试。",
    }


def main() -> None:
    profile = Path(sys.argv[1])
    closed = threading.Event()
    requests: queue.Queue[dict[str, object] | None] = queue.Queue()
    request_tracker = BrowserRequestTracker(closed, emit)

    def read_requests() -> None:
        try:
            for line in sys.stdin:
                if line.strip() == "close":
                    closed.set()
                    break
                try:
                    payload = json.loads(line)
                    if isinstance(payload, dict) and payload.get("type") == "request_decision":
                        request_tracker.decide(
                            int(payload.get("request_id", -1)),
                            payload.get("allowed") is True,
                        )
                    else:
                        requests.put(payload if isinstance(payload, dict) else {})
                except (ValueError, TypeError):
                    requests.put({})
        finally:
            closed.set()
            requests.put(None)

    threading.Thread(target=read_requests, daemon=True).start()
    try:
        if account_profile_is_fenced(profile):
            emit(
                {
                    "error": "tieba_browser_account_deleting",
                    "message": "账户正在删除，贴吧专用会话不可继续使用。",
                }
            )
            return
        with (
            DataDirectoryLock(profile),
        ):
            if account_profile_is_fenced(profile):
                emit(
                    {
                        "error": "tieba_browser_account_deleting",
                        "message": "账户正在删除，贴吧专用会话不可继续使用。",
                    }
                )
                return
            with (
                sync_playwright() as playwright,
                playwright.chromium.launch_persistent_context(
                    str(profile), channel="chrome", headless=False,
                    chromium_sandbox=True
                ) as context,
            ):
                page = context.pages[0] if context.pages else context.new_page()
                request_tracker.install(context)
                while not closed.is_set():
                    if account_profile_is_fenced(profile):
                        break
                    try:
                        payload = requests.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    if payload is None:
                        break
                    raw_deadline = payload.get("deadline_monotonic")
                    deadline = (
                        float(raw_deadline)
                        if isinstance(raw_deadline, (float, int))
                        else None
                    )
                    request_tracker.set_deadline(deadline)
                    try:
                        result, page, effective_deadline = _run_action(
                            context, page, payload, closed, request_tracker
                        )
                        request_tracker.wait_until_idle(context, effective_deadline)
                        emit(result)
                    except Exception as error:  # 每个请求独立失败，保留本账户浏览器上下文
                        emit(_error_payload(error))
                        page = context.pages[-1] if context.pages else context.new_page()
                    finally:
                        request_tracker.set_deadline(None)
    except RuntimeLockError:
        emit(
            {
                "error": "tieba_browser_busy",
                "message": "该账户的专用贴吧会话正在处理另一项采集，请稍后重试。",
            }
        )
    except Exception:
        emit(
            {
                "error": "tieba_browser_unavailable",
                "message": "专用浏览器未取得可识别页面，请确认 Chrome 已安装并重试。",
            }
        )


if __name__ == "__main__":
    main()
