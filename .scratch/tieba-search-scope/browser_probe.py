"""人工登录与公开 DOM 核验；只有用户授权后才运行。"""

import json
import queue
import sys
import threading
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from playwright.sync_api import sync_playwright

from bridges.tieba.lexicon import TARGET_FORUM_URL


def emit(value):
    def clean(item):
        if isinstance(item, dict):
            return {key: clean(content) for key, content in item.items()}
        if isinstance(item, list):
            return [clean(content) for content in item]
        if isinstance(item, str) and item.startswith("http"):
            url = urlparse(item)
            query = urlencode([(key, val) for key, val in parse_qsl(url.query)
                               if key in {"kw", "qw", "word", "q", "pn", "un"}])
            return urlunparse(url._replace(query=query, fragment=""))
        return item
    print(json.dumps(clean(value), ensure_ascii=False), flush=True)


commands = queue.Queue()


def receive():
    for line in sys.stdin:
        commands.put(json.loads(line))
    commands.put({"action": "close"})


threading.Thread(target=receive, daemon=True).start()
profile = Path(sys.argv[1]).resolve()
with sync_playwright() as playwright:
    context = playwright.chromium.launch_persistent_context(
        str(profile), channel="chrome", headless=False, chromium_sandbox=True,
    )
    page = context.pages[0] if context.pages else context.new_page()
    page.set_default_timeout(8000)
    try:
        page.goto(TARGET_FORUM_URL, wait_until="domcontentloaded", timeout=20000)
    except Exception as error:
        emit({"navigation_error": type(error).__name__, "message": str(error).splitlines()[0]})
    emit({"opened": True, "url": page.url, "title": page.title()})
    try:
        while True:
            try:
                command = commands.get_nowait()
            except queue.Empty:
                page.wait_for_timeout(300)
                continue
            action = command.get("action")
            if action == "close":
                break
            if action == "inspect":
                emit(page.evaluate("""() => ({
                    url: location.href, title: document.title,
                    text: document.body.innerText.slice(0,9000),
                    inputs: [...document.querySelectorAll('input[type=search]')].map(e=>({
                        type:e.type,placeholder:e.placeholder,name:e.name,id:e.id,
                        value:e.value,parent:e.parentElement.outerHTML.slice(0,3000)})),
                    links: [...document.querySelectorAll('a[href]')].map(e=>({
                        text:e.innerText,href:e.href})).filter(e=>e.text).slice(0,100)
                })"""))
            elif action == "pages":
                emit([{"index": i, "url": p.url, "title": p.title()}
                      for i, p in enumerate(context.pages)])
            elif action == "dom":
                emit(page.evaluate("""() => ({
                    headings: [...document.querySelectorAll('h1,h2,h3')].map(e=>({text:e.innerText,html:e.outerHTML})),
                    posts: [...document.querySelectorAll('a[href*="/p/"]')].slice(0,8).map(e=>({text:e.innerText,html:e.parentElement.outerHTML.slice(0,6000)})),
                    classes: [...document.querySelectorAll('[class]')].filter(e=>e.className.toString().match(/reply|comment|thread|post|floor|content|forum-name/)).slice(0,100).map(e=>({tag:e.tagName,cls:e.className,text:e.innerText.slice(0,600)}))
                })"""))
            elif action == "choose":
                page = context.pages[int(command["index"])]
                emit({"selected": True, "url": page.url})
            elif action == "fill":
                page.locator(command["selector"]).fill(command["text"])
                emit({"filled": True})
            elif action == "press":
                page.locator(command["selector"]).press(command["key"])
                emit({"pressed": True, "url": page.url})
            elif action == "click":
                page.get_by_text(command["text"], exact=True).click()
                emit({"clicked": True, "url": page.url})
            elif action == "goto":
                target = command["url"]
                if urlparse(target).hostname != "tieba.baidu.com":
                    raise ValueError("诊断读取只能导航百度贴吧")
                page.goto(target, wait_until="domcontentloaded", timeout=20000)
                emit({"navigated": True, "url": page.url})
            elif action == "save":
                if urlparse(page.url).hostname != "tieba.baidu.com":
                    raise ValueError("只能保存公开贴吧页面")
                target = Path(command["path"]).resolve()
                if not target.is_relative_to(Path(__file__).parent.resolve()):
                    raise ValueError("诊断文件只能写入本次任务目录")
                target.write_text(page.content(), encoding="utf-8")
                emit({"saved": True})
    finally:
        context.close()
