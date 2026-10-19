"""用本机 Chrome 实际执行搜索解析，覆盖已实测的新帖子卡片结构。"""

import threading
import time
from collections.abc import Iterator

import pytest
from playwright.sync_api import Page, Route, sync_playwright

from bridges.tieba.browser_scroll import (
    expand_thread_nested_replies,
    scroll_thread_replies,
)
from bridges.tieba.browser_worker import (
    SEARCH_DOM,
    THREAD_DOM,
    BrowserTimeoutError,
    search,
)


@pytest.fixture
def chrome_page() -> Iterator[Page]:
    with (
        sync_playwright() as playwright,
        playwright.chromium.launch(channel="chrome", headless=True) as browser,
    ):
        page = browser.new_page()
        yield page


class _NoAuth:
    deadline = None

    def recover(self, *_: object, **__: object) -> None:
        raise AssertionError("公开 DOM 测试不应触发认证恢复")


def test_new_thread_cards_preserve_title_abstract_forum_and_background_link(
    chrome_page: Page,
) -> None:
    chrome_page.set_content("""
        <base href="https://tieba.baidu.com/">
        <div class="thread-new3">
          <a href="/p/101"></a>
          <div class="title-wrap">宿舍热水讨论</div>
          <div class="abstract-wrap">有回复提到晚上热水供应。</div>
          <div class="forum-name"><span class="forum-name-text">华东交通大学吧</span></div>
        </div>
    """)
    assert chrome_page.evaluate(SEARCH_DOM, "1") == [
        {
            "url": "https://tieba.baidu.com/p/101",
            "title": "宿舍热水讨论",
            "snippet": "华东交通大学吧\n有回复提到晚上热水供应。",
            "discovery_type": "thread",
            "matched_reply": None,
        }
    ]


def test_search_waits_for_new_cards_and_retains_old_reply_layout(chrome_page: Page) -> None:
    def serve(route: Route) -> None:
        # 由 Playwright 路由本地响应，禁止测试请求真实贴吧。
        if "tab=1" in route.request.url:
            body = """
                <div class="thread-new3"><a href="https://tieba.baidu.com/p/102"></a>
                <div class="title-wrap">新卡片</div><div class="abstract-wrap">新摘要</div>
                <span class="forum-name-text">华东交通大学吧</span></div>
            """
        else:
            body = """
                <div class="tbm-floor-thread">
                <a class="comment-link-zone" href="https://tieba.baidu.com/p/103"></a>
                <div class="tbm-floor-thread-post-thread">旧回复标题</div>
                <div class="tbm-floor-thread-rich-text">只在回复中出现的宿舍说法</div>
                <div class="tbm-floor-thread-forum">华东交通大学吧</div></div>
            """
        route.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)

    chrome_page.route("**/*", serve)
    hits = search(chrome_page, "宿舍", threading.Event(), None, _NoAuth())  # type: ignore[arg-type]
    assert [hit["title"] for hit in hits] == ["新卡片", "旧回复标题"]
    assert hits[1]["matched_reply"] == "只在回复中出现的宿舍说法"
    assert hits[1]["discovery_type"] == "reply"


def test_search_stops_before_next_page_navigation_after_cancellation(chrome_page: Page) -> None:
    stopped = threading.Event()
    navigations: list[str] = []

    def serve(route: Route) -> None:
        navigations.append(route.request.url)
        stopped.set()
        route.fulfill(
            status=200,
            content_type="text/html; charset=utf-8",
            body='<div class="thread-new3"><a href="/p/105"></a></div>',
        )

    chrome_page.route("**/*", serve)
    with pytest.raises(InterruptedError, match="tieba_browser_cancelled"):
        search(chrome_page, "宿舍", stopped, None, _NoAuth())  # type: ignore[arg-type]

    assert len(navigations) == 1
    assert "tab=1" in navigations[0]


def test_search_does_not_navigate_after_deadline(chrome_page: Page) -> None:
    deadline = time.monotonic() - 1

    with pytest.raises(BrowserTimeoutError):
        search(
            chrome_page,
            "宿舍",
            threading.Event(),
            deadline,
            _NoAuth(),  # type: ignore[arg-type]
        )

    assert chrome_page.url == "about:blank"


def test_old_thread_layout_still_extracts_original_fields(chrome_page: Page) -> None:
    chrome_page.set_content("""
        <div class="tbm-post-thread"><a href="https://tieba.baidu.com/p/104"></a>
        <div class="thread-title">旧标题</div><span class="forum-name">华东交通大学吧</span>
        <div>旧卡片原文</div></div>
    """)
    hit = chrome_page.evaluate(SEARCH_DOM, "1")[0]
    assert hit["title"] == "旧标题"
    assert "旧卡片原文" in hit["snippet"]
    assert hit["url"] == "https://tieba.baidu.com/p/104"


def test_thread_scrolling_targets_the_inner_reply_container(chrome_page: Page) -> None:
    chrome_page.set_content("""
        <div class="pc-pb-box" style="height: 180px; overflow-y: auto">
          <div style="height: 1800px">回复内容</div>
        </div>
    """)

    rounds = scroll_thread_replies(chrome_page, 8, threading.Event())
    state = chrome_page.locator(".pc-pb-box").evaluate(
        "element => ({top: element.scrollTop, end: element.scrollHeight - element.clientHeight})"
    )

    assert state["top"] == state["end"]
    assert 1 <= rounds <= 8


def test_thread_expands_collapsed_nested_replies(chrome_page: Page) -> None:
    chrome_page.set_content("""
        <div class="pb-comment-item" data-id="parent-1">
          <div class="pb-lzl-item"><div class="pb-content-item">已显示</div></div>
          <button class="show-more-lzl">展开 1 条回复</button>
        </div>
    """)
    chrome_page.evaluate("""() => {
        const button = document.querySelector('.show-more-lzl');
        button.addEventListener('click', () => {
          setTimeout(() => {
            button.insertAdjacentHTML('beforebegin',
              '<div class="pb-lzl-item"><div class="pb-content-item">展开的回复</div></div>');
            button.remove();
          }, 650);
        });
    }""")

    expanded = expand_thread_nested_replies(chrome_page, threading.Event())

    assert expanded == 1
    assert chrome_page.locator(".pb-comment-item .pb-lzl-item").count() == 2
    assert chrome_page.locator(".show-more-lzl").count() == 0


def test_thread_nested_expansion_stops_at_deadline(chrome_page: Page) -> None:
    chrome_page.set_content("<button class='show-more-lzl'>展开回复</button>")

    expanded = expand_thread_nested_replies(
        chrome_page, threading.Event(), deadline=time.monotonic() - 1
    )

    assert expanded == 0
    assert chrome_page.locator(".show-more-lzl").count() == 1


def test_nested_posted_at_comes_only_from_time_metadata(chrome_page: Page) -> None:
    chrome_page.set_content("""
        <div class="pb-comment-item" data-id="parent-1">
          <div class="comment-desc-left">第 1 楼</div>
          <div class="pb-lzl-item">
            <span class="lzl_time">2026-10-02 12:30</span>
            <div class="pb-content-item">我在 2024-09-01 看到过同样的问题。</div>
          </div>
          <div class="pb-lzl-item">
            <div class="pb-content-item">正文日期 2025-03-04 不代表发言时间。</div>
          </div>
        </div>
    """)

    replies = chrome_page.evaluate(THREAD_DOM)["replies"]

    assert replies[0]["posted_at"] == "2026-10-02 12:30"
    assert replies[1]["posted_at"] is None


def test_current_reply_metadata_keeps_missing_year_unknown(chrome_page: Page) -> None:
    chrome_page.set_content("""
        <div class="pb-comment-item" data-id="parent-1">
          <div class="comment-content">
            <div class="pb-rich-text"><div class="pb-content-item">父楼内容。</div></div>
            <div class="pc-pb-comments-desc">
              <span class="comment-desc-left">10-07 江西</span>
            </div>
          </div>
          <div class="pb-lzl-item">
            <div class="comment-content">
              <div class="pb-rich-text"><div class="pb-content-item">楼中楼内容。</div></div>
              <div class="pc-pb-comments-desc">
                <span class="comment-desc-left">10-08 江西</span>
              </div>
            </div>
          </div>
        </div>
    """)

    replies = chrome_page.evaluate(THREAD_DOM)["replies"]

    assert replies[0]["reply_id"] == "parent-1"
    assert replies[0]["posted_at"] == "10-07"
    assert replies[1]["reply_id"] is None
    assert replies[1]["posted_at"] == "10-08"
