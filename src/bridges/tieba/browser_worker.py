"""隔离的贴吧浏览器采集进程：搜索主题与回复，读取有限范围的帖子内容。"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlencode, urlparse

from playwright.sync_api import BrowserContext, Page, sync_playwright
from playwright.sync_api import TimeoutError as BrowserTimeoutError

from bridges.runtime.lock import DataDirectoryLock, RuntimeLockError
from bridges.tieba.lexicon import TARGET_FORUM_URL

SEARCH_DOM = r"""(tab) => {
    const text = e => e?.innerText?.trim() || '';
    const thread = tab === '1';
    const selector = thread ? '.tbm-post-thread' : '.tbm-floor-thread';
    return [...document.querySelectorAll(selector)].slice(0, 12).map(e => {
        const linkSelector = thread
            ? 'a[href*="/p/"]'
            : '.comment-link-zone[href*="/p/"], a[href*="/p/"]';
        const titleSelector = thread
            ? '.thread-title, .tbm-post-thread-title'
            : '.tbm-floor-thread-post-thread';
        const link = e.querySelector(linkSelector);
        const title = text(e.querySelector(titleSelector));
        const reply = text(e.querySelector('.tbm-floor-thread-rich-text'));
        const forum = text(e.querySelector('.forum-name, .tbm-floor-thread-forum'));
        const body = reply || text(e);
        return {
            url: link?.href || '',
            title: (title || text(e.querySelector('a[href*="/p/"]'))).slice(0, 160),
            snippet: [forum, body].join(String.fromCharCode(10)).slice(0, 900),
            discovery_type: thread ? 'thread' : 'reply',
            matched_reply: reply || null
        };
    }).filter(item => item.url);
}"""

THREAD_DOM = r"""() => {
    const text = e => e?.innerText?.trim() || '';
    const forum = document.querySelector('.main-content .forum-name');
    const rows = [...document.querySelectorAll('.pb-comment-item')];
    const replies = [];
    for (const row of rows) {
        const parentId = row.getAttribute('data-id') || null;
        const floorText = text(row.querySelector('.comment-desc-left'));
        const floorMatch = floorText.match(/第\s*(\d+)\s*楼/);
        const direct = [...row.querySelectorAll('.pb-rich-text .pb-content-item')].filter(
            node => node.closest('.pb-comment-item') === row && !node.closest('.pb-lzl-item')
        );
        const content = direct.map(text).join('').slice(0, 600);
        if (content) replies.push({
            content, reply_id: parentId,
            floor: floorMatch ? Number(floorMatch[1]) : null,
            posted_at: floorText || null,
            parent_reply_id: null, nested: false
        });
        for (const nested of row.querySelectorAll('.pb-lzl-item')) {
            const nestedText = text(nested);
            const body = [...nested.querySelectorAll('.pb-content-item')]
                .map(text).join('').slice(0, 600);
            if (!body) continue;
            const id = nested.getAttribute('data-id') || nested.getAttribute('data-spid') || null;
            const datePattern = new RegExp(
                String.raw`(?:20\d{2}[-/.年])?\d{1,2}[-/.月]\d{1,2}` +
                String.raw`(?:日)?(?:\s+\d{1,2}:\d{2})?`
            );
            replies.push({
                content: body, reply_id: id, floor: floorMatch ? Number(floorMatch[1]) : null,
                posted_at: nestedText.match(datePattern)?.[0] || null,
                parent_reply_id: parentId, nested: true
            });
        }
    }
    const countText = [...document.querySelectorAll('button, a, span')]
        .map(text).find(value => /全部回复\s*[（(]\s*\d+\s*[)）]/.test(value)) || '';
    const count = countText.match(/全部回复\s*[（(]\s*(\d+)/);
    return {
        forum: text(forum).replace(/\s/g, ''),
        title: document.title.replace(/-百度贴吧$/, ''),
        original: text(document.querySelector('.center-content .pb-content-wrap')).slice(0, 2000),
        posted_at: text(document.querySelector('.center-content .post-num')) || null,
        loaded_reply_count: rows.length,
        total_replies: count ? Number(count[1]) : null,
        replies
    };
}"""


def emit(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False), flush=True)


def auth_required(page: Page) -> str | None:
    if "百度安全验证" in page.title() or page.locator("#captcha, .pass-sms-wrap").count():
        return "verification"
    if page.locator(".login-guide-placeholder").count():
        return "login"
    return None


def official_login(context: BrowserContext, return_url: str, closed: threading.Event) -> Page:
    login = context.new_page()
    login.goto(
        "https://passport.baidu.com/v2/?" + urlencode({"login": "", "tpl": "tb", "u": return_url}),
        wait_until="domcontentloaded",
        timeout=18000,
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
            if reason is None and (
                has_content
                or "没有找到" in login.locator("body").inner_text()
            ):
                return login
    raise PermissionError("tieba_browser_auth_interrupted")


def search(page: Page, terms: str, closed: threading.Event) -> list[dict[str, object]]:
    parsed = urlparse(TARGET_FORUM_URL)
    base = f"{parsed.scheme}://{parsed.netloc}/f/search/res?"
    found: dict[str, dict[str, object]] = {}
    for tab in ("1", "2"):
        params = {"kw": "华东交通大学", "qw": terms[:80], "ie": "utf-8", "tab": tab}
        target = base + urlencode(params)
        page.goto(target, wait_until="domcontentloaded", timeout=18000)
        page.wait_for_function(
            """() => document.querySelector('.tbm-post-thread, .tbm-floor-thread') ||
            document.querySelector('.login-guide-placeholder') ||
            /没有找到|暂无.*结果|百度安全验证/.test(document.body.innerText)""",
            timeout=9000,
        )
        reason = auth_required(page)
        if reason:
            page = official_login(page.context, target, closed)
            page.goto(target, wait_until="domcontentloaded", timeout=18000)
            page.wait_for_timeout(700)
        for hit in page.evaluate(SEARCH_DOM, tab):
            found.setdefault(str(hit["url"]), hit)
    return list(found.values())


def read_thread(
    page: Page, url: str, pages_limit: int, closed: threading.Event
) -> dict[str, object]:
    parsed = urlparse(url)
    if parsed.hostname != "tieba.baidu.com" or not parsed.path.startswith("/p/"):
        raise ValueError("只允许读取贴吧帖子")
    page.goto(url, wait_until="domcontentloaded", timeout=16000)
    page.wait_for_selector(".pb-content-wrap", timeout=9000)
    if auth_required(page):
        page = official_login(page.context, url, closed)
        page.goto(url, wait_until="domcontentloaded", timeout=16000)
        page.wait_for_selector(".pb-content-wrap", timeout=9000)
    limit = max(1, min(int(pages_limit or 1), 8))
    prior_count = -1
    stable = 0
    for _ in range(limit):
        if closed.is_set():
            break
        count = page.locator(".pb-comment-item, .pb-lzl-item").count()
        if count == prior_count:
            stable += 1
            if stable >= 2:
                break
        else:
            stable = 0
        prior_count = count
        page.mouse.wheel(0, 800)
        page.wait_for_timeout(350)
    result = page.evaluate(THREAD_DOM)
    if not isinstance(result, dict):
        raise RuntimeError("贴吧帖子页面未返回结构化内容")
    return {str(key): value for key, value in result.items()}


def main() -> None:
    profile = Path(sys.argv[1])
    payload = json.loads(sys.stdin.readline())
    closed = threading.Event()

    def watch_close() -> None:
        sys.stdin.readline()
        closed.set()

    threading.Thread(target=watch_close, daemon=True).start()
    try:
        with (
            DataDirectoryLock(profile),
            sync_playwright() as playwright,
            playwright.chromium.launch_persistent_context(
                str(profile), channel="chrome", headless=False, chromium_sandbox=True
            ) as context,
        ):
            page = context.pages[0] if context.pages else context.new_page()
            page.set_default_timeout(8000)
            action = payload["action"]
            if action == "setup":
                login = context.new_page()
                login.goto(
                    "https://passport.baidu.com/v2/?"
                    + urlencode({"login": "", "tpl": "tb", "u": TARGET_FORUM_URL}),
                    wait_until="domcontentloaded",
                    timeout=18000,
                )
                emit({"opened": True})
                while not closed.is_set() and not login.is_closed():
                    login.wait_for_timeout(250)
            elif action == "search":
                hits = search(page, str(payload["terms"]), closed)
                emit({"hits": hits})
            elif action == "read":
                thread = read_thread(
                    page,
                    str(payload["url"]),
                    int(payload.get("pages_limit", 2)),
                    closed,
                )
                emit(thread)
            else:
                raise ValueError("不支持的动作")
    except RuntimeLockError:
        emit(
            {
                "error": "tieba_browser_busy",
                "message": "该账户的专用贴吧会话正在处理另一项采集，请稍后重试。",
            }
        )
    except PermissionError as error:
        code = (
            "tieba_browser_auth_interrupted"
            if "interrupted" in str(error)
            else "tieba_browser_auth_required"
        )
        emit(
            {
                "error": code,
                "message": "专用贴吧登录或验证未完成，请在打开的官方页面完成后说“继续”。",
            }
        )
    except BrowserTimeoutError:
        emit(
            {
                "error": "tieba_browser_timeout",
                "message": "贴吧页面加载超时或结构已变化，请稍后重试。",
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
