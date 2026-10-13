"""隔离的贴吧浏览器采集进程：搜索主题与回复，读取有限范围的帖子内容。"""

from __future__ import annotations

import json
import queue
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlencode, urlparse

from playwright.sync_api import BrowserContext, Page, sync_playwright
from playwright.sync_api import TimeoutError as BrowserTimeoutError

from bridges.runtime.lock import DataDirectoryLock, RuntimeLockError
from bridges.tieba.browser_dom import SEARCH_DOM, THREAD_DOM
from bridges.tieba.browser_requests import BrowserRequestTracker
from bridges.tieba.browser_runtime import account_profile_is_fenced
from bridges.tieba.lexicon import TARGET_FORUM_URL


def emit(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False), flush=True)


def auth_required(page: Page) -> str | None:
    if "百度安全验证" in page.title() or page.locator("#captcha, .pass-sms-wrap").count():
        return "verification"
    if page.locator(".login-guide-placeholder").count():
        return "login"
    return None


def _request_timeout_ms(
    closed: threading.Event,
    deadline: float | None,
    maximum_ms: int,
) -> int:
    if closed.is_set():
        raise InterruptedError("tieba_browser_cancelled")
    if deadline is None:
        return maximum_ms
    remaining_ms = int((deadline - time.monotonic()) * 1000)
    if remaining_ms <= 0:
        raise BrowserTimeoutError("贴吧浏览器运行预算已耗尽。")
    return min(maximum_ms, remaining_ms)


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
        timeout=_request_timeout_ms(closed, deadline, 9000),
    )
    return auth_required(page)


def official_login(
    context: BrowserContext,
    return_url: str,
    closed: threading.Event,
    deadline: float | None = None,
) -> Page:
    login = context.new_page()
    login.goto(
        "https://passport.baidu.com/v2/?" + urlencode({"login": "", "tpl": "tb", "u": return_url}),
        wait_until="domcontentloaded",
        timeout=_request_timeout_ms(closed, deadline, 18000),
    )
    emit({"waiting_for_auth": True})
    until = time.monotonic() + 600
    while not closed.is_set() and time.monotonic() < until:
        if login.is_closed():
            raise PermissionError("tieba_browser_auth_interrupted")
        login.wait_for_timeout(400)
        if urlparse(login.url).hostname == "tieba.baidu.com":
            reason = auth_required(login)
            has_content = login.locator(
                ".main-content .forum-name, .tbm-post-thread, .tbm-floor-thread"
            ).count()
            if reason is None and (has_content or "没有找到" in login.locator("body").inner_text()):
                raise PermissionError("tieba_browser_auth_unverified")
    if closed.is_set():
        raise InterruptedError("tieba_browser_cancelled")
    raise PermissionError("tieba_browser_auth_interrupted")


def search(
    page: Page,
    terms: str,
    closed: threading.Event,
    deadline: float | None = None,
) -> list[dict[str, object]]:
    parsed = urlparse(TARGET_FORUM_URL)
    base = f"{parsed.scheme}://{parsed.netloc}/f/search/res?"
    found: dict[str, dict[str, object]] = {}
    for tab in ("1", "2"):
        params = {"kw": "华东交通大学", "qw": terms[:80], "ie": "utf-8", "tab": tab}
        target = base + urlencode(params)
        page.goto(
            target,
            wait_until="domcontentloaded",
            timeout=_request_timeout_ms(closed, deadline, 18000),
        )
        page.wait_for_function(
            """() => document.querySelector('.tbm-post-thread, .tbm-floor-thread, .thread-new3') ||
            document.querySelector('.login-guide-placeholder') ||
            document.querySelector('#captcha, .pass-sms-wrap') ||
            /没有找到|暂无.*结果|百度安全验证/.test(document.body.innerText) ||
            /百度安全验证/.test(document.title)""",
            timeout=_request_timeout_ms(closed, deadline, 9000),
        )
        reason = auth_required(page)
        if reason:
            auth_started = time.monotonic()
            page = official_login(page.context, target, closed, deadline)
            if deadline is not None:
                deadline += time.monotonic() - auth_started
            page.goto(
                target,
                wait_until="domcontentloaded",
                timeout=_request_timeout_ms(closed, deadline, 18000),
            )
            page.wait_for_timeout(min(700, _request_timeout_ms(closed, deadline, 700)))
        for hit in page.evaluate(SEARCH_DOM, tab):
            found.setdefault(str(hit["url"]), hit)
    return list(found.values())


def scroll_thread_replies(
    page: Page,
    limit: int,
    closed: threading.Event,
    deadline: float | None = None,
) -> int:
    container = page.locator(".pc-pb-box").first
    has_container = container.count() > 0
    prior_count = -1
    stable_rounds = 0
    rounds = 0
    for _ in range(limit):
        if closed.is_set() or (deadline is not None and time.monotonic() >= deadline):
            break
        rounds += 1
        count = page.locator(".pb-comment-item, .pb-lzl-item").count()
        if has_container:
            before = container.evaluate(
                "element => ({top: element.scrollTop, height: element.scrollHeight})"
            )
            container.hover()
        else:
            before = page.evaluate(
                "() => ({top: window.scrollY, height: document.documentElement.scrollHeight})"
            )
        page.mouse.wheel(0, 800)
        wait_ms = 350
        if deadline is not None:
            wait_ms = min(wait_ms, max(0, int((deadline - time.monotonic()) * 1000)))
        if wait_ms:
            page.wait_for_timeout(wait_ms)
        if has_container:
            after = container.evaluate(
                "element => ({top: element.scrollTop, height: element.scrollHeight})"
            )
        else:
            after = page.evaluate(
                "() => ({top: window.scrollY, height: document.documentElement.scrollHeight})"
            )
        next_count = page.locator(".pb-comment-item, .pb-lzl-item").count()
        progressed = before != after or count != next_count
        if progressed or next_count != prior_count:
            stable_rounds = 0
        else:
            stable_rounds += 1
            if stable_rounds >= 2:
                break
        prior_count = next_count
    return rounds


def expand_thread_nested_replies(
    page: Page,
    closed: threading.Event,
    deadline: float | None = None,
) -> int:
    stop_at = time.monotonic() + 4
    if deadline is not None:
        stop_at = min(stop_at, deadline)
    expanded = 0
    for _ in range(120):
        remaining = stop_at - time.monotonic()
        if closed.is_set() or remaining <= 0.1:
            break
        control = page.locator(".show-more-lzl").first
        if control.count() == 0:
            break
        prior_count = page.locator(".pb-lzl-item").count()
        try:
            timeout_ms = _request_timeout_ms(
                closed, deadline, min(800, max(1, int((remaining - 0.1) * 1000)))
            )
            control.click(timeout=timeout_ms)
        except BrowserTimeoutError:
            break
        remaining = stop_at - time.monotonic()
        if remaining <= 0:
            break
        try:
            page.wait_for_function(
                "count => document.querySelectorAll('.pb-lzl-item').length > count "
                "|| !document.querySelector('.show-more-lzl')",
                arg=prior_count,
                timeout=min(1200, max(1, int(remaining * 1000))),
            )
        except BrowserTimeoutError:
            break
        next_count = page.locator(".pb-lzl-item").count()
        if next_count <= prior_count:
            break
        expanded += next_count - prior_count
    return expanded


def read_thread(
    page: Page,
    url: str,
    scroll_rounds_limit: int,
    closed: threading.Event,
    deadline: float | None = None,
) -> dict[str, object]:
    parsed = urlparse(url)
    if parsed.hostname != "tieba.baidu.com" or not parsed.path.startswith("/p/"):
        raise ValueError("只允许读取贴吧帖子")
    page.goto(
        url,
        wait_until="domcontentloaded",
        timeout=_request_timeout_ms(closed, deadline, 16000),
    )
    if wait_for_thread_page_or_auth(page, closed, deadline):
        auth_started = time.monotonic()
        page = official_login(page.context, url, closed, deadline)
        if deadline is not None:
            deadline += time.monotonic() - auth_started
        page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=_request_timeout_ms(closed, deadline, 16000),
        )
    page.wait_for_selector(
        ".pb-content-wrap", timeout=_request_timeout_ms(closed, deadline, 9000)
    )
    limit = max(1, min(int(scroll_rounds_limit or 1), 8))
    scroll_rounds = scroll_thread_replies(page, limit, closed, deadline)
    expand_thread_nested_replies(page, closed, deadline)
    result = page.evaluate(THREAD_DOM)
    if not isinstance(result, dict):
        raise RuntimeError("贴吧帖子页面未返回结构化内容")
    return {
        **{str(key): value for key, value in result.items()},
        "scroll_rounds": scroll_rounds,
        "scroll_rounds_limit": limit,
    }


def _run_action(
    context: BrowserContext,
    page: Page,
    payload: dict[str, object],
    closed: threading.Event,
) -> tuple[dict[str, object], Page]:
    page.set_default_timeout(8000)
    action = payload.get("action")
    raw_deadline = payload.get("deadline_monotonic")
    deadline = float(raw_deadline) if isinstance(raw_deadline, (float, int)) else None
    if action == "search":
        result: dict[str, object] = {
            "hits": search(page, str(payload["terms"]), closed, deadline)
        }
    elif action == "read":
        raw_scroll_limit = payload.get("scroll_rounds_limit")
        scroll_limit = int(str(raw_scroll_limit or 8))
        result = read_thread(
            page,
            str(payload["url"]),
            scroll_limit,
            closed,
            deadline,
        )
    else:
        raise ValueError("不支持的动作")
    return result, (context.pages[-1] if context.pages else context.new_page())


def _error_payload(error: Exception) -> dict[str, str]:
    if isinstance(error, InterruptedError):
        return {
            "error": "tieba_browser_cancelled",
            "message": "已停止贴吧浏览器读取。",
        }
    if isinstance(error, PermissionError):
        code = str(error)
        if code not in {
            "tieba_browser_auth_interrupted",
            "tieba_browser_auth_unverified",
        }:
            code = "tieba_browser_auth_required"
        message = (
            "贴吧页面可读，但无法独立确认专用会话的认证状态；本轮未继续采集。"
            if code == "tieba_browser_auth_unverified"
            else "专用贴吧登录或验证未完成，请在打开的官方页面完成后说“继续”。"
        )
        return {
            "error": code,
            "message": message,
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
                    try:
                        result, page = _run_action(context, page, payload, closed)
                        raw_deadline = payload.get("deadline_monotonic")
                        deadline = (
                            float(raw_deadline)
                            if isinstance(raw_deadline, (float, int))
                            else None
                        )
                        request_tracker.wait_until_idle(context, deadline)
                        emit(result)
                    except Exception as error:  # 每个请求独立失败，保留本账户浏览器上下文
                        emit(_error_payload(error))
                        page = context.pages[-1] if context.pages else context.new_page()
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
