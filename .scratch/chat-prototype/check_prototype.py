"""在 agent 环境中检查独立原型的真实用户路径，不调用生产服务。"""
from pathlib import Path
import json
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).parent
URL = "http://127.0.0.1:8766/"
checks = []
errors = []


def passed(name):
    checks.append(name)
    print(f"PASS: {name}", flush=True)


with sync_playwright() as p:
    browser = p.chromium.launch(channel="msedge", headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.on("pageerror", lambda error: errors.append(str(error)))
    response = page.goto(URL)
    assert response.status == 200
    page.wait_for_load_state("networkidle")
    expect(page.locator("#welcome")).to_be_visible()
    expect(page.locator("#viewer")).to_be_hidden()
    expect(page.locator("#send")).to_be_disabled()
    page.screenshot(path=str(ROOT / "home.png"))
    passed("首页加载、资料面板默认关闭、空输入不可发送")

    page.locator('[data-session="paper"]').click()
    expect(page.locator(".activity")).not_to_have_attribute("open", "")
    expect(page.locator("#mode-button")).to_be_disabled()
    page.screenshot(path=str(ROOT / "conversation.png"))
    page.locator(".activity summary").click()
    expect(page.locator(".activity-steps")).to_be_visible()
    page.locator(".activity summary").click()
    passed("历史对话可读、模式固定、过程按需展开")

    page.locator('[data-source="rag"]').first.click()
    expect(page.locator("#viewer")).to_be_visible()
    expect(page.locator("#input")).to_be_visible()
    page.screenshot(path=str(ROOT / "panel.png"))
    page.locator('[data-tab="1"]').click()
    page.locator("#reading-note").fill("检索质量与回答质量要分开看")
    page.locator('[data-tab="0"]').click()
    page.locator('[data-tab="1"]').click()
    expect(page.locator("#reading-note")).to_have_value("检索质量与回答质量要分开看")
    page.locator('[data-tab="0"]').click()
    page.locator(".followup").click()
    expect(page.locator(".turn")).to_have_count(2)
    expect(page.locator(".streaming")).to_have_count(0, timeout=10000)
    expect(page.locator("#viewer")).to_be_visible()
    expect(page.locator(".answer-body").last).to_contain_text("开卷回答")
    page.screenshot(path=str(ROOT / "followup.png"))
    passed("引用预览、笔记切换保留、资料打开期间继续追问")
    page.keyboard.press("Escape")
    expect(page.locator("#viewer")).to_be_hidden()

    page.locator('[data-session="partial"]').click()
    expect(page.locator(".activity summary")).to_contain_text("部分完成")
    before = page.locator(".answer-body p").first.inner_text()
    page.locator('[data-action="retry-partial"]').click()
    expect(page.locator('[data-action="retry-partial"]')).to_be_disabled()
    expect(page.locator(".answer-body")).to_contain_text("补充回复已读取", timeout=5000)
    assert before == page.locator(".answer-body p").first.inner_text()
    expect(page.locator(".activity summary")).to_contain_text("已完成")
    passed("失败提示内联、重试保留已有内容、完成状态更新")

    page.locator('[data-action="guide"]').first.click()
    page.locator('[data-action="demo-stream"]').click()
    expect(page.locator(".streaming")).to_have_count(1)
    expect(page.locator(".answer-body")).to_contain_text("可以先沿着一条主线读", timeout=7000)
    page.get_by_role("button", name="停止生成", exact=True).click()
    stopped = page.locator(".answer-body").inner_text()
    expect(page.locator(".inline-stopped")).to_be_visible()
    page.locator('[data-action="continue"]').click()
    assert stopped == page.locator(".answer-body").inner_text()
    expect(page.locator(".streaming")).to_have_count(0, timeout=10000)
    expect(page.locator(".answer-body")).to_contain_text("第一遍")
    passed("生成进度、停止、保留内容继续生成")

    page.get_by_role("button", name="开始新对话", exact=True).click()
    page.locator("#mode-button").click()
    page.locator('[data-mode="学习模式"]').click()
    page.locator("#input").fill("我的学习问题")
    page.locator("#input").press("Shift+Enter")
    assert page.locator("#input").input_value().endswith("\n")
    page.locator("#input").fill("<img src=x onerror=alert(1)>我的学习问题")
    page.locator("#input").press("Enter")
    expect(page.locator(".streaming")).to_have_count(0, timeout=10000)
    expect(page.locator("#header-mode")).to_have_text("学习模式")
    assert page.locator(".user-message img").count() == 0
    passed("新对话选模式、换行与发送、用户输入按纯文本呈现")

    page.locator('[data-action="search"]').click()
    page.locator("#search-input").fill("不存在的关键词")
    expect(page.locator("#search-results")).to_contain_text("没有找到")
    page.locator("#search-input").fill("天空")
    page.locator('#search-results [data-session="learn"]').click()
    expect(page.locator("#title")).to_have_text("为什么天空是蓝色的？")
    page.locator(".followup").click()
    expect(page.locator(".streaming")).to_have_count(0, timeout=10000)
    expect(page.locator(".answer-body").last).to_contain_text("你的解释抓住了关键")
    page.screenshot(path=str(ROOT / "learning.png"))
    passed("搜索结果与空态、学习多轮交流")

    page.get_by_role("button", name="开始新对话", exact=True).click()
    page.locator("#file-input").set_input_files({"name":"示例资料.txt","mimeType":"text/plain","buffer":b"local prototype"})
    expect(page.locator("#attachments")).to_contain_text("示例资料.txt")
    page.locator('[data-remove-file="0"]').click()
    expect(page.locator("#attachments")).to_be_hidden()
    passed("本地附件选择与移除")

    for width, height in [(1920,1080),(1280,720),(1024,768),(768,1024),(375,812)]:
        page.set_viewport_size({"width":width,"height":height})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        expect(page.locator("#input")).to_be_in_viewport()
        page.screenshot(path=str(ROOT / f"home-{width}.png"))
        page.locator('#starters [data-scenario="paper"]').click()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.locator('[data-source="rag"]').first.click()
        expect(page.locator("#viewer")).to_be_in_viewport()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.screenshot(path=str(ROOT / f"panel-{width}.png"))
        page.get_by_role("button", name="关闭资料面板", exact=True).click()
        page.get_by_role("button", name="开始新对话", exact=True).click()
    passed("1920、1280、1024、768、375 宽度无横向溢出，预览与输入可用")
    page.emulate_media(reduced_motion="reduce")
    assert page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches")
    assert not errors, errors
    passed("减少动态效果设置、无浏览器脚本错误")
    browser.close()

(ROOT / "verification.json").write_text(json.dumps({"checks":checks,"errors":errors}, ensure_ascii=False, indent=2), encoding="utf-8")
