"""贴吧搜索页签的分页采集。

真实搜索页的首屏由 ``/mo/q/search/thread`` 响应提供，后续页由页面滚动
触发同一接口的 ``pn`` 请求。这里只使用页面真实声明和真实响应：读到哪一页、
是否还有后续页、为什么停止，都写进 ``TiebaSearchScope``，不用 DOM 文字
猜测分页状态。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urlencode, urlparse

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as BrowserTimeoutError

from bridges.tieba.browser_auth import (
    COLLECTION_RESTRICTED,
    AuthSession,
    AuthWaitError,
    auth_required,
    request_timeout_ms,
)
from bridges.tieba.browser_capture import StructuredCapture, wait_for_payloads
from bridges.tieba.browser_dom import SEARCH_DOM
from bridges.tieba.browser_search_scope import empty_scope, scope_dict
from bridges.tieba.browser_structured import (
    SearchPage,
    StructuredResponseError,
    parse_search_page,
)
from bridges.tieba.lexicon import TARGET_FORUM_URL

#: 每个页签最多读取的搜索页数；真实翻页仍逐请求计量。
SEARCH_PAGES_LIMIT = 2

#: 首屏结构化响应的等待上限。
SEARCH_FIRST_PAYLOAD_WAIT_MS = 1500

#: 滚动后等待下一页响应的上限。
SEARCH_PAGE_WAIT_MS = 2500

SEARCH_RESULT_READY = (
    "() => document.querySelector('.tbm-post-thread, .tbm-floor-thread, .thread-new3') ||"
    " document.querySelector('.login-guide-placeholder') ||"
    " document.querySelector('#captcha, .pass-sms-wrap') ||"
    " /没有找到|暂无.*结果|百度安全验证/.test(document.body.innerText) ||"
    " /百度安全验证/.test(document.title)"
)

SEARCH_SCROLL_JS = """
() => {
  const box = document.querySelector('.frs-page-wrap');
  if (box) {
    box.scrollTop = box.scrollHeight;
    box.dispatchEvent(new Event('scroll', {bubbles: true}));
    return 'frs-page-wrap';
  }
  if (!document.body) return 'none';
  window.scrollTo(0, document.body.scrollHeight);
  return 'window';
}
"""


@dataclass(frozen=True)
class SearchTabResult:
    """一个页签的分页结果与停止边界。"""

    hits: tuple[dict[str, object], ...]
    scope: dict[str, object]
    page: Page
    stop_reason: str | None = None
    error_code: str | None = None
    error_message: str | None = None


def run_search(
    page: Page,
    terms: str,
    closed: threading.Event,
    deadline: float | None,
    auth: AuthSession,
    *,
    pages_limit: int = SEARCH_PAGES_LIMIT,
) -> tuple[dict[str, object], Page]:
    """搜索主题与回复两个页签，返回合并候选、页范围和恢复后的页面。"""
    if closed.is_set():
        raise InterruptedError("tieba_browser_cancelled")
    if deadline is not None and time.monotonic() >= deadline:
        raise BrowserTimeoutError("贴吧浏览器运行预算已耗尽。")
    parsed = urlparse(TARGET_FORUM_URL)
    base = f"{parsed.scheme}://{parsed.netloc}/f/search/res?"
    hits: list[dict[str, object]] = []
    scopes: list[dict[str, object]] = []
    errors: list[SearchTabResult] = []
    for tab in ("1", "2"):
        if closed.is_set():
            scopes.append(
                empty_scope(
                    tab, terms, pages_limit, "cancelled", "任务已停止，未开始该页签。"
                )
            )
            break
        if deadline is not None and time.monotonic() >= deadline:
            scopes.append(
                empty_scope(
                    tab, terms, pages_limit, "timeout", "运行预算已耗尽，未开始该页签。"
                )
            )
            break
        target = base + urlencode(
            {"kw": "华东交通大学", "qw": terms[:80], "ie": "utf-8", "tab": tab}
        )
        result = _search_tab(
            page,
            target,
            tab,
            terms,
            closed,
            deadline,
            auth,
            pages_limit,
        )
        page = result.page
        hits.extend(result.hits)
        scopes.append(result.scope)
        if result.stop_reason or result.error_code:
            errors.append(result)
        if result.stop_reason in {"access_restricted", "timeout", "cancelled"}:
            break
    merged = merge_search_hit_dicts(hits)
    error = next((item for item in errors if item.error_code), None)
    stop_reason = next(
        (item.stop_reason for item in reversed(errors) if item.stop_reason), None
    )
    return (
        {
            "hits": merged,
            "search_scope": scopes,
            "partial": any(scope.get("stop_reason") for scope in scopes),
            "stop_reason": stop_reason,
            "error_code": error.error_code if error is not None else None,
            "error_message": error.error_message if error is not None else None,
        },
        page,
    )


def _search_tab(
    page: Page,
    target: str,
    tab: str,
    terms: str,
    closed: threading.Event,
    deadline: float | None,
    auth: AuthSession,
    pages_limit: int,
) -> SearchTabResult:
    discovery_type = "thread" if tab == "1" else "reply"
    current_deadline = auth.deadline if auth.deadline is not None else deadline
    capture = StructuredCapture(page, "/mo/q/search/thread").start()
    try:
        _navigate(page, target, closed, current_deadline)
        reason = auth_required(page)
        if reason:
            capture.stop()
            page = auth.recover(page, reason=reason, target_url=target)
            capture = StructuredCapture(page, "/mo/q/search/thread").start()
            current_deadline = auth.deadline if auth.deadline is not None else deadline
            _navigate(page, target, closed, current_deadline)
            if auth_required(page):
                raise AuthWaitError(COLLECTION_RESTRICTED, reason="collection")
        wait_for_payloads(
            capture.payloads,
            page,
            closed,
            current_deadline,
            SEARCH_FIRST_PAYLOAD_WAIT_MS,
        )
        pages, failure = _pages_from(capture.payloads, discovery_type)
        if not pages:
            dom_hits = page.evaluate(SEARCH_DOM, tab)
            if dom_hits:
                hits = tuple(_dom_hit(dict(item), terms) for item in dom_hits)
                return SearchTabResult(
                    hits=hits,
                    scope=scope_dict(
                        tab,
                        terms,
                        pages_read=1,
                        pages_limit=pages_limit,
                        visited=[1],
                        last_page=1,
                        has_more=None,
                        stop_reason="dom_fallback",
                        note="结构化响应缺失，使用页面卡片兜底；未确认是否还有后续页。",
                    ),
                    page=page,
                    stop_reason="dom_fallback",
                )
            if failure is not None:
                raise failure
            return SearchTabResult(
                hits=(),
                scope=scope_dict(
                    tab,
                    terms,
                    pages_read=0,
                    pages_limit=pages_limit,
                    visited=[],
                    last_page=None,
                    has_more=False,
                    stop_reason=None,
                    note="首屏没有返回搜索结果。",
                ),
                page=page,
            )

        first_number = min(pages)
        first = pages[first_number]
        collected: list[dict[str, object]] = [
            _with_query(hit, terms) for hit in first.hits
        ]
        visited = [first_number]
        known_pages = set(pages)
        current = first_number
        has_more = first.has_more
        stop_reason: str | None = None
        note: str | None = None
        if not first.hits and has_more:
            stop_reason = "abnormal_empty_page"
            note = "首屏空结果却声明还有后续页，已停止翻页。"

        while has_more and len(visited) < pages_limit:
            if closed.is_set():
                stop_reason = "cancelled"
                note = "翻页前任务已停止，已保留已取得的搜索结果。"
                break
            if current_deadline is not None and time.monotonic() >= current_deadline:
                stop_reason = "timeout"
                note = "翻页前运行预算已耗尽，已保留已取得的搜索结果。"
                break
            marker = len(capture.payloads)
            _scroll_search_results(page)
            next_page = _wait_for_page_after(
                capture.payloads,
                marker,
                discovery_type,
                page,
                closed,
                current_deadline,
                known_pages,
            )
            if next_page is None:
                if closed.is_set():
                    stop_reason = "cancelled"
                    note = "翻页等待时任务已停止，已保留已取得的搜索结果。"
                    break
                reason = auth_required(page)
                if reason:
                    stop_reason = "access_restricted"
                    note = "翻页时出现登录或安全验证入口，已保留已取得的搜索结果。"
                    return SearchTabResult(
                        hits=tuple(collected),
                        scope=scope_dict(
                            tab,
                            terms,
                            pages_read=len(visited),
                            pages_limit=pages_limit,
                            visited=visited,
                            last_page=current,
                            has_more=has_more,
                            stop_reason=stop_reason,
                            note=note,
                        ),
                        page=page,
                        stop_reason=stop_reason,
                        error_code=COLLECTION_RESTRICTED,
                        error_message=(
                            "搜索翻页触发登录或安全验证，已保留翻页前取得的候选。"
                        ),
                    )
                stop_reason = "timeout"
                note = "滚动后没有等到下一页搜索响应，已保留已取得的搜索结果。"
                break
            if next_page.page in visited:
                stop_reason = "duplicate_page"
                note = "滚动后返回了已读过的页码，已停止以免重复请求。"
                break
            if next_page.page <= current:
                stop_reason = "abnormal_page"
                note = "滚动后返回的页码没有前进，已停止。"
                break
            if next_page.page > current + 1:
                stop_reason = "page_gap"
                note = "滚动后返回的页码跳号，已停止并保留已读到的一页。"
                break
            visited.append(next_page.page)
            known_pages.add(next_page.page)
            current = next_page.page
            has_more = next_page.has_more
            collected.extend(_with_query(hit, terms) for hit in next_page.hits)
            if not next_page.hits and has_more:
                stop_reason = "abnormal_empty_page"
                note = "空页却声明还有后续页，已停止。"
                break

        if has_more and len(visited) >= pages_limit and stop_reason is None:
            stop_reason = "pages_limit"
            note = f"仅读取前 {pages_limit} 页，页面声明还有后续结果。"
        if stop_reason is None:
            note = f"已读到第 {current} 页，页面声明没有更多结果。"
        return SearchTabResult(
            hits=tuple(collected),
            scope=scope_dict(
                tab,
                terms,
                pages_read=len(visited),
                pages_limit=pages_limit,
                visited=visited,
                last_page=current,
                has_more=has_more,
                stop_reason=stop_reason,
                note=note,
            ),
            page=page,
            stop_reason=stop_reason,
        )
    finally:
        capture.stop()


def _navigate(
    page: Page,
    target: str,
    closed: threading.Event,
    deadline: float | None,
) -> None:
    page.goto(
        target,
        wait_until="domcontentloaded",
        timeout=request_timeout_ms(closed, deadline, 18000),
    )
    page.wait_for_function(
        SEARCH_RESULT_READY,
        timeout=request_timeout_ms(closed, deadline, 9000),
    )


def _pages_from(
    payloads: Iterable[dict[str, object]],
    discovery_type: str,
) -> tuple[dict[int, SearchPage], StructuredResponseError | None]:
    pages: dict[int, SearchPage] = {}
    failure: StructuredResponseError | None = None
    for payload in payloads:
        try:
            parsed = parse_search_page(payload, discovery_type=discovery_type)
        except StructuredResponseError as error:
            failure = failure or error
            continue
        previous = pages.get(parsed.page)
        if previous is None or len(parsed.hits) >= len(previous.hits):
            pages[parsed.page] = parsed
    return pages, failure


def _wait_for_page_after(
    payloads: list[dict[str, object]],
    marker: int,
    discovery_type: str,
    page: Page,
    closed: threading.Event,
    deadline: float | None,
    known_pages: set[int],
) -> SearchPage | None:
    stop = time.monotonic() + SEARCH_PAGE_WAIT_MS / 1000
    if deadline is not None:
        stop = min(stop, deadline)
    duplicate: SearchPage | None = None
    while time.monotonic() < stop:
        if closed.is_set():
            return None
        for payload in payloads[marker:]:
            try:
                parsed = parse_search_page(payload, discovery_type=discovery_type)
            except StructuredResponseError:
                continue
            if parsed.page in known_pages:
                duplicate = duplicate or parsed
                continue
            return parsed
        page.wait_for_timeout(100)
    return duplicate


def _scroll_search_results(page: Page) -> bool:
    return str(page.evaluate(SEARCH_SCROLL_JS)) != "none"


def _with_query(hit: dict[str, object], terms: str) -> dict[str, object]:
    enriched = dict(hit)
    enriched["query"] = terms
    enriched["source"] = "tieba_browser"
    return enriched


def _dom_hit(hit: dict[str, object], terms: str) -> dict[str, object]:
    hit.setdefault("search_page", 1)
    hit.setdefault("matched_reply_id", None)
    hit.setdefault("matched_author", None)
    return _with_query(hit, terms)


def merge_search_hit_dicts(
    hits: Iterable[dict[str, object]],
) -> list[dict[str, object]]:
    """按链接合并候选，并把每次发现写成独立 discovery。"""
    merged: dict[str, dict[str, object]] = {}
    order: list[str] = []
    for raw in hits:
        hit = dict(raw)
        url = str(hit.get("url") or "").strip()
        if not url:
            continue
        discovery = _discovery_dict(hit)
        if url not in merged:
            hit["discoveries"] = [discovery]
            merged[url] = hit
            order.append(url)
            continue
        existing = merged[url]
        discoveries = existing.setdefault("discoveries", [])
        if isinstance(discoveries, list) and not _has_discovery(discoveries, discovery):
            discoveries.append(discovery)
        if hit.get("discovery_type") == "reply" and existing.get("discovery_type") != "reply":
            for key in (
                "discovery_type",
                "matched_reply",
                "matched_reply_id",
                "matched_author",
                "search_page",
            ):
                if hit.get(key) is not None:
                    existing[key] = hit[key]
            if not existing.get("title"):
                existing["title"] = hit.get("title")
    return [merged[url] for url in order]


def _discovery_dict(hit: dict[str, object]) -> dict[str, object]:
    return {
        "discovery_type": hit.get("discovery_type") or "thread",
        "query": hit.get("query"),
        "search_page": hit.get("search_page"),
        "matched_reply": hit.get("matched_reply"),
        "matched_reply_id": hit.get("matched_reply_id"),
        "matched_author": hit.get("matched_author"),
        "source": hit.get("source") or "tieba_browser",
    }


def _has_discovery(
    discoveries: list[dict[str, object]],
    candidate: dict[str, object],
) -> bool:
    key = _discovery_key(candidate)
    return any(_discovery_key(item) == key for item in discoveries)


def _discovery_key(item: dict[str, object]) -> tuple[object, ...]:
    return (
        item.get("discovery_type"),
        item.get("query"),
        item.get("search_page"),
        item.get("matched_reply_id"),
        item.get("matched_reply"),
    )


__all__ = [
    "SEARCH_PAGES_LIMIT",
    "SEARCH_PAGE_WAIT_MS",
    "SearchTabResult",
    "merge_search_hit_dicts",
    "run_search",
]
