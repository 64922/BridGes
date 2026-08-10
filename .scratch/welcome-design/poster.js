/* 从 Demo 页视频抓一帧（带 brightness(1.22) 滤镜）生成 welcome-poster.jpg */
const { chromium } = require("playwright");
const fs = require("fs");

const URL = "http://127.0.0.1:8321/poster.html";
const OUT = "C:/Users/33755/Desktop/try5/apps/web/public/media/welcome-poster.jpg";

(async () => {
  const browser = await chromium.launch({ headless: true, channel: "chrome" });
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  await page.goto(URL, { waitUntil: "networkidle" });
  await page.waitForFunction(() => {
    const v = document.querySelector("video");
    return v && v.readyState >= 2;
  }, null, { timeout: 20000 });
  // 取 2s 处的代表性画面
  await page.evaluate(() => { document.querySelector("video").currentTime = 2; });
  await page.waitForTimeout(800);
  const dataUrl = await page.evaluate(() => {
    const v = document.querySelector("video");
    const canvas = document.createElement("canvas");
    canvas.width = 1600;
    canvas.height = Math.round(1600 * v.videoHeight / v.videoWidth);
    const ctx = canvas.getContext("2d");
    ctx.filter = "brightness(1.22)";
    ctx.drawImage(v, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL("image/jpeg", 0.72);
  });
  fs.writeFileSync(OUT, Buffer.from(dataUrl.split(",")[1], "base64"));
  console.log("poster saved", fs.statSync(OUT).size, "bytes");
  await browser.close();
})();
