/* 欢迎页设计 Demo 截图：桌面 1440x900 + 移动 390x844 */
const { chromium } = require("playwright");

const URL = "http://127.0.0.1:8321/index.html";
const OUT = "C:/Users/33755/Desktop/try5/.scratch/welcome-design";

(async () => {
  let browser;
  for (const channel of ["chrome", "msedge"]) {
    try {
      browser = await chromium.launch({ headless: true, channel });
      console.log(`launched with channel=${channel}`);
      break;
    } catch {
      /* try next */
    }
  }
  if (!browser) browser = await chromium.launch({ headless: true });
  const shots = [
    ["desktop", { width: 1440, height: 900 }],
  ];
  for (const [name, viewport] of shots) {
    const page = await browser.newPage({ viewport });
    await page.goto(URL, { waitUntil: "networkidle" });
    try {
      await page.waitForFunction(() => {
        const v = document.querySelector("video");
        return v && v.readyState >= 2 && v.currentTime > 0.2;
      }, null, { timeout: 20000 });
    } catch (e) {
      console.error(`[${name}] video not playing in time`);
    }
    await page.waitForTimeout(2500);
    await page.screenshot({ path: `${OUT}/shot-${name}.png` });
    const state = await page.evaluate(() => {
      const v = document.querySelector("video");
      return { t: v.currentTime, rs: v.readyState, paused: v.paused, vw: v.videoWidth, vh: v.videoHeight };
    });
    console.log(`[${name}] video state:`, JSON.stringify(state));
    await page.close();
  }
  // 桌面端补拍按钮 hover 态
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.goto(URL, { waitUntil: "networkidle" });
  await page.waitForTimeout(2000);
  await page.hover(".btn-primary");
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${OUT}/shot-hover.png`, clip: { x: 1050, y: 0, width: 390, height: 120 } });
  await page.close();
  await browser.close();
  console.log("done");
})();
