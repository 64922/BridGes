"""贴吧帖子页的滚动加载与嵌套回复展开。"""

from __future__ import annotations

import threading
import time

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as BrowserTimeoutError

from bridges.tieba.browser_auth import request_timeout_ms


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
            timeout_ms = request_timeout_ms(
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
