"""专用会话只执行固定贴吧入口、吧内搜索与公开帖子 DOM 读取。"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlencode, urlparse

from playwright.sync_api import Page, sync_playwright
from playwright.sync_api import TimeoutError as BrowserTimeoutError

from bridges.runtime.lock import DataDirectoryLock, RuntimeLockError
from bridges.tieba.lexicon import TARGET_FORUM_URL

SEARCH_DOM = """() => [...document.querySelectorAll('.threadcardclass')].slice(0,12).map(e => ({
    url: e.querySelector('a[href*="/p/"]')?.href || '',
    title: e.querySelector('.title-content-wrap')?.innerText.split('\\n')[0].slice(0,160) || '',
    snippet: (e.querySelector('.thread-forum-name')?.innerText || '') + '\\n' +
        (e.querySelector('.title-content-wrap')?.innerText || '').slice(0,900)
}))"""

THREAD_DOM = """() => {
    const text = (e) => e?.innerText?.trim() || '';
    const forum = document.querySelector('.main-content .forum-name');
    const comments = [...document.querySelectorAll('.center-content .comment-content')];
    return {
        forum: text(forum).replace(/\\s/g,''),
        title: document.title.replace(/-百度贴吧$/, ''),
        original: text(document.querySelector('.center-content .pb-content-wrap')).slice(0,2000),
        posted_at: text(document.querySelector('.center-content .post-num')),
        replies: comments.slice(0,40).map(e=>({
            content: [...e.querySelectorAll('.pb-content-item')]
                .filter(n=>n.closest('.comment-content') === e)
                .map(n=>text(n)).join('').slice(0,500),
            posted_at: text(e.querySelector('.comment-desc-left')).split('\\n')[0]
        })).filter(e=>e.content)
    };
}"""


def emit(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False), flush=True)


def ensure_page(page: Page) -> None:
    if urlparse(page.url).hostname != "tieba.baidu.com":
        raise ValueError("访问离开固定贴吧域名")
    if "百度安全验证" in page.title():
        raise PermissionError("请在专用浏览器中手动完成安全验证。")
    if page.locator(".login-guide-placeholder").count():
        raise PermissionError("请在专用浏览器中登录贴吧后再检索。")


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
                str(profile),
                channel="chrome",
                headless=False,
                chromium_sandbox=True,
            ) as context,
        ):
            page = context.pages[0] if context.pages else context.new_page()
            page.set_default_timeout(8000)
            action = payload["action"]
            if action == "setup":
                page.goto(TARGET_FORUM_URL, wait_until="domcontentloaded", timeout=18000)
                emit({"opened": True})
                until = time.monotonic() + 600
                while not closed.is_set() and time.monotonic() < until and context.pages:
                    page.wait_for_timeout(200)
                return
            if action == "search":
                target = TARGET_FORUM_URL.replace("/f?", "/f/search/res?")
                target += "&" + urlencode({"qw": str(payload["terms"])[:80]})
                page.goto(target, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_function(
                    """() => document.querySelector('.threadcardclass') ||
                    document.querySelector('.login-guide-placeholder') ||
                    /没有找到|暂无.*结果|百度安全验证/.test(document.body.innerText)""",
                    timeout=8000,
                )
                ensure_page(page)
                emit({"hits": page.evaluate(SEARCH_DOM)})
            elif action == "read":
                target = str(payload["url"])
                parsed = urlparse(target)
                if parsed.hostname != "tieba.baidu.com" or not parsed.path.startswith("/p/"):
                    raise ValueError("只允许读取贴吧帖子")
                page.goto(target, wait_until="domcontentloaded", timeout=12000)
                page.wait_for_selector(".pb-content-wrap", timeout=7000)
                ensure_page(page)
                # 新版是滚动加载；只记录有限次滚动实际取得的文本，不推测总页数。
                page.locator(".pc-pb-reply-top").first.wait_for(timeout=3000)
                for _ in range(2):
                    if closed.is_set():
                        break
                    page.mouse.wheel(0, 700)
                    page.wait_for_timeout(250)
                emit(page.evaluate(THREAD_DOM))
            else:
                raise ValueError("不支持的动作")
    except RuntimeLockError:
        emit(
            {
                "error": "tieba_browser_busy",
                "message": "专用贴吧窗口正在使用，请完成登录后关闭窗口再重试。",
            }
        )
    except PermissionError:
        emit(
            {
                "error": "tieba_browser_setup_required",
                "message": "贴吧要求安全验证或登录，请打开专用浏览器手动完成后重试。",
            }
        )
    except BrowserTimeoutError:
        emit(
            {
                "error": "tieba_browser_timeout",
                "message": (
                    "贴吧页面加载超时或结构已变化，请重试；"
                    "若要求验证，请打开专用浏览器手动完成。"
                ),
            }
        )
    except Exception:
        # 原始 Playwright 异常可能包含会话 URL，不能进入日志或响应。
        emit(
            {
                "error": "tieba_browser_unavailable",
                "message": "专用 Chrome 未取得可识别页面，请确认 Chrome 已安装并重试。",
            }
        )


if __name__ == "__main__":
    main()
