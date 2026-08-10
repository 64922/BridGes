"""欢迎页设计 Demo 截图：桌面 1440x900 + 移动 390x844。"""
import sys
from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8321/index.html"

SHOTS = [
    ("desktop", {"width": 1440, "height": 900}, ".scratch/welcome-design/shot-desktop.png"),
    ("mobile", {"width": 390, "height": 844}, ".scratch/welcome-design/shot-mobile.png"),
]

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    for name, viewport, out in SHOTS:
        page = browser.new_page(viewport=viewport)
        page.goto(URL, wait_until="networkidle")
        # 等视频真正起播（最多 15s），再稳定 2.5s 取一帧有内容的画面
        try:
            page.wait_for_function(
                "() => { const v = document.querySelector('video');"
                " return v && v.readyState >= 2 && v.currentTime > 0.2; }",
                timeout=15000,
            )
        except Exception as e:
            print(f"[{name}] video not playing in time: {e}", file=sys.stderr)
        page.wait_for_timeout(2500)
        page.screenshot(path=out)
        state = page.evaluate(
            "() => { const v = document.querySelector('video');"
            " return {t: v.currentTime, rs: v.readyState, paused: v.paused,"
            "        vw: v.videoWidth, vh: v.videoHeight}; }"
        )
        print(f"[{name}] video state: {state}")
        page.close()
    browser.close()
print("done")
