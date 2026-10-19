"""用本机 Chrome 验证真实搜索页的滚动翻页与回复命中合并。"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import Page, Route, sync_playwright

from bridges.tieba.browser_search import SearchTabResult, run_search

URL_101 = "https://tieba.baidu.com/p/101"
URL_102 = "https://tieba.baidu.com/p/102"
URL_103 = "https://tieba.baidu.com/p/103"


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
        raise AssertionError("公开分页测试不应触发认证恢复")


def _payload(
    *,
    page: int,
    has_more: int,
    tid: str,
    title: str,
    content: str,
    cid: str = "0",
) -> dict[str, Any]:
    return {
        "no": 0,
        "error": "success",
        "data": {
            "current_page": page,
            "has_more": has_more,
            "post_list": [
                {
                    "tid": tid,
                    "pid": int(tid) * 100,
                    "cid": cid,
                    "title": title,
                    "content": content,
                    "forum_name": "华东交通大学",
                    "pb_url": f"https://tieba.baidu.com/p/{tid}",
                    "user": {"show_nickname": "吧友甲"},
                }
            ],
        },
    }


def _html(*, next_page: int = 2) -> str:
    return f"""
    <!doctype html>
    <html>
      <head><meta charset="utf-8"><title>搜索</title></head>
      <body>
        <div class="thread-new3" style="display:none"></div>
        <div class="frs-page-wrap" style="height:120px;overflow-y:auto">
          <div style="height:600px"></div>
        </div>
        <script>
          const box = document.querySelector('.frs-page-wrap');
          const tab = new URLSearchParams(location.search).get('tab') || '1';
          function load(page) {{
            fetch('/mo/q/search/thread?pn=' + page + '&tab=' + tab);
          }}
          load(1);
          box.addEventListener('scroll', () => {{
            if (box.dataset.next) return;
            box.dataset.next = '1';
            load({next_page});
          }});
        </script>
      </body>
    </html>
    """


def _serve(route: Route, *, scenario: str = "normal") -> None:
    url = route.request.url
    if "/f/search/res" in url:
        route.fulfill(
            status=200,
            content_type="text/html; charset=utf-8",
            body=_html(),
        )
        return
    if "/mo/q/search/thread" not in url:
        route.fulfill(status=204, body="")
        return
    query = parse_qs(urlparse(url).query)
    tab = query.get("tab", ["1"])[0]
    pn = int(query.get("pn", ["1"])[0])
    if tab == "1" and pn == 2:
        if scenario == "silent":
            route.fulfill(status=500, body="")
            return
        if scenario == "duplicate":
            body = _payload(
                page=1,
                has_more=1,
                tid="101",
                title="第一页的帖子",
                content="第一页提到空调。",
            )
        elif scenario == "gap":
            body = _payload(
                page=3,
                has_more=0,
                tid="103",
                title="跳号页的帖子",
                content="跳号页也提到空调。",
            )
        elif scenario == "empty":
            body = {
                "no": 0,
                "error": "success",
                "data": {"current_page": 2, "has_more": 1, "post_list": []},
            }
        elif scenario == "more":
            body = _payload(
                page=2,
                has_more=1,
                tid="102",
                title="第二页的帖子",
                content="第二页也提到空调。",
            )
        else:
            body = _payload(
                page=2,
                has_more=0,
                tid="102",
                title="第二页的帖子",
                content="第二页也提到空调。",
            )
    elif tab == "1":
        body = _payload(
            page=1,
            has_more=1,
            tid="101",
            title="第一页的帖子",
            content="第一页提到空调。",
        )
    else:
        body = _payload(
            page=1,
            has_more=0,
            tid="101",
            title="回复：第一页的帖子",
            content="回复里提到空调。",
            cid="991",
        )
    route.fulfill(
        status=200,
        content_type="application/json; charset=utf-8",
        body=json.dumps(body, ensure_ascii=False),
    )


def test_real_scroll_loads_second_page_and_records_scope(chrome_page: Page) -> None:
    chrome_page.route("**/*", _serve)

    result, _ = run_search(
        chrome_page,
        "空调",
        threading.Event(),
        None,
        _NoAuth(),  # type: ignore[arg-type]
        pages_limit=2,
    )

    assert [hit["url"] for hit in result["hits"]] == [URL_101, URL_102]
    assert result["partial"] is False
    assert all(
        discovery["query"] == "空调"
        for hit in result["hits"]
        for discovery in hit["discoveries"]
    )
    thread_scope = result["search_scope"][0]
    assert thread_scope["tab"] == "thread"
    assert thread_scope["pages_read"] == 2
    assert thread_scope["visited_pages"] == [1, 2]
    assert thread_scope["has_more"] is False
    assert thread_scope["stop_reason"] is None


def test_real_reply_tab_merges_same_thread_discovery_paths(chrome_page: Page) -> None:
    chrome_page.route("**/*", _serve)

    result, _ = run_search(
        chrome_page,
        "空调",
        threading.Event(),
        None,
        _NoAuth(),  # type: ignore[arg-type]
        pages_limit=2,
    )

    hit = next(item for item in result["hits"] if item["url"] == URL_101)
    assert hit["discovery_type"] == "reply"
    assert hit["matched_reply_id"] == "991"
    assert {item["discovery_type"] for item in hit["discoveries"]} == {
        "thread",
        "reply",
    }
    assert {item["search_page"] for item in hit["discoveries"]} == {1}
    reply_scope = result["search_scope"][1]
    assert reply_scope["tab"] == "reply"
    assert reply_scope["pages_read"] == 1


def test_duplicate_page_stops_without_dropping_first_page(
    chrome_page: Page, monkeypatch: pytest.MonkeyPatch
) -> None:
    import bridges.tieba.browser_search as browser_search

    monkeypatch.setattr(browser_search, "SEARCH_PAGE_WAIT_MS", 300)
    chrome_page.route("**/*", lambda route: _serve(route, scenario="duplicate"))

    result, _ = run_search(
        chrome_page,
        "空调",
        threading.Event(),
        None,
        _NoAuth(),  # type: ignore[arg-type]
        pages_limit=2,
    )

    assert [hit["url"] for hit in result["hits"]] == [URL_101]
    thread_scope = result["search_scope"][0]
    assert thread_scope["pages_read"] == 1
    assert thread_scope["stop_reason"] == "duplicate_page"
    assert result["partial"] is True


@pytest.mark.parametrize(
    ("scenario", "stop_reason"),
    [
        ("gap", "page_gap"),
        ("empty", "abnormal_empty_page"),
        ("silent", "timeout"),
        ("more", "pages_limit"),
    ],
)
def test_abnormal_pagination_stops_and_keeps_first_page(
    chrome_page: Page,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    stop_reason: str,
) -> None:
    import bridges.tieba.browser_search as browser_search

    monkeypatch.setattr(browser_search, "SEARCH_PAGE_WAIT_MS", 300)
    chrome_page.route("**/*", lambda route: _serve(route, scenario=scenario))

    result, _ = run_search(
        chrome_page,
        "空调",
        threading.Event(),
        None,
        _NoAuth(),  # type: ignore[arg-type]
        pages_limit=2,
    )

    expected_urls = [URL_101, URL_102] if scenario == "more" else [URL_101]
    assert [hit["url"] for hit in result["hits"]] == expected_urls
    thread_scope = result["search_scope"][0]
    assert thread_scope["pages_read"] == (2 if scenario in {"more", "empty"} else 1)
    assert thread_scope["stop_reason"] == stop_reason
    assert thread_scope["has_more"] is True
    assert result["partial"] is True


def test_cancelled_tab_keeps_already_found_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    import bridges.tieba.browser_search as browser_search

    class _Page:
        pass

    def fake_search_tab(*_: object, **__: object) -> SearchTabResult:
        return SearchTabResult(
            hits=(
                {
                    "url": URL_101,
                    "title": "第一页的帖子",
                    "snippet": "华东交通大学吧",
                    "discovery_type": "thread",
                    "search_page": 1,
                    "query": "空调",
                },
            ),
            scope={
                "tab": "thread",
                "query": "空调",
                "pages_read": 1,
                "pages_limit": 2,
                "visited_pages": [1],
                "last_page": 1,
                "has_more": True,
                "stop_reason": "cancelled",
                "note": "翻页前任务已停止，已保留已取得的搜索结果。",
            },
            page=_Page(),  # type: ignore[arg-type]
            stop_reason="cancelled",
        )

    monkeypatch.setattr(browser_search, "_search_tab", fake_search_tab)

    result, _ = run_search(
        _Page(),  # type: ignore[arg-type]
        "空调",
        threading.Event(),
        None,
        _NoAuth(),  # type: ignore[arg-type]
        pages_limit=2,
    )

    assert [hit["url"] for hit in result["hits"]] == [URL_101]
    assert result["stop_reason"] == "cancelled"
    assert result["partial"] is True
    assert result["search_scope"][0]["stop_reason"] == "cancelled"
