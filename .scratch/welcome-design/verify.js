/* 验收真实 Next 页面：未登录访问 / 应渲染欢迎页（视频播放、按钮可点、跳转正确） */
const { chromium } = require("playwright");

const BASE = "http://127.0.0.1:3100";
const OUT = "C:/Users/33755/Desktop/try5/.scratch/welcome-design";

(async () => {
  const browser = await chromium.launch({ headless: true, channel: "chrome" });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.goto(BASE + "/", { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForSelector("text=BridGes", { timeout: 30000 });
  await page.waitForFunction(() => {
    const v = document.querySelector("video");
    return v && v.readyState >= 2 && v.currentTime > 0.2;
  }, null, { timeout: 20000 }).catch(() => console.error("video not playing"));
  await page.waitForTimeout(2500);
  await page.screenshot({ path: `${OUT}/shot-integrated.png` });
  const state = await page.evaluate(() => {
    const v = document.querySelector("video");
    return { t: v.currentTime, paused: v.paused, src: v.querySelector("source")?.src };
  });
  console.log("video:", JSON.stringify(state));

  // 点击「登录」应跳到 /login
  await page.getByRole("link", { name: "登录" }).click();
  await page.waitForURL("**/login", { timeout: 15000 });
  console.log("login navigation ok:", page.url());
  await page.screenshot({ path: `${OUT}/shot-integrated-login.png` });

  await browser.close();
  console.log("done");
})();
