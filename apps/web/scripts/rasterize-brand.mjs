/**
 * Rasterize the BridGes SVG brand assets to PNG at common desktop sizes.
 *
 * Usage (from apps/web):  node scripts/rasterize-brand.mjs
 * Uses the project's Playwright Chromium so output is reproducible from the
 * locked dependency set.
 */
import { chromium } from "@playwright/test";
import { fileURLToPath } from "node:url";
import { readFileSync } from "node:fs";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const brandDir = path.join(here, "..", "public", "brand");

/** [sourceSvg, outputPng, pixelWidth] */
const targets = [
  ["bridges-logo-icon.svg", "bridges-logo-icon-16.png", 16],
  ["bridges-logo-icon.svg", "bridges-logo-icon-24.png", 24],
  ["bridges-logo-icon.svg", "bridges-logo-icon-32.png", 32],
  ["bridges-logo-icon.svg", "bridges-logo-icon-48.png", 48],
  ["bridges-logo-icon.svg", "bridges-logo-icon-64.png", 64],
  ["bridges-logo-icon.svg", "bridges-logo-icon-128.png", 128],
  ["bridges-logo-icon.svg", "bridges-logo-icon-256.png", 256],
  ["bridges-logo-icon.svg", "bridges-logo-icon-512.png", 512],
  ["bridges-logo-icon-dark.svg", "bridges-logo-icon-dark-32.png", 32],
  ["bridges-logo-icon-dark.svg", "bridges-logo-icon-dark-256.png", 256],
  ["bridges-logo-icon-mono.svg", "bridges-logo-icon-mono-32.png", 32],
  ["bridges-logo-icon-mono.svg", "bridges-logo-icon-mono-256.png", 256],
];

const browser = await chromium.launch();
try {
  const page = await browser.newPage({ deviceScaleFactor: 2 });
  for (const [svgName, pngName, width] of targets) {
    let svg = readFileSync(path.join(brandDir, svgName), "utf8");
    // 固定渲染尺寸，保证不同导出尺寸一致缩放
    svg = svg.replace(/width="48" height="48"/, `width="${width}" height="${width}"`);
    await page.setViewportSize({ width, height: width });
    await page.setContent(
      `<!doctype html><html><body style="margin:0">${svg}</body></html>`,
    );
    const mark = page.locator("svg");
    const outPath = path.join(brandDir, pngName);
    await mark.screenshot({ path: outPath, omitBackground: true });
    console.log(`wrote ${pngName} (${width}px)`);
  }
} finally {
  await browser.close();
}
