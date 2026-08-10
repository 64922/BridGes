import { expect, test, type Locator, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

/**
 * Issue 38 — AC4 / V2：1280×720、1440×900、1920×1080 在 100%、125%、
 * 150%、200% 浏览器缩放下的桌面路径。
 *
 * 浏览器 zoom 等价于 CSS 布局视口按缩放比缩小：125% → 1280/1.25 =
 * 1024 CSS 像素宽，以此类推（与 WCAG 1.4.10 reflow 的检查方法一致）。
 * 每个组合断言：
 *   1. 页面无水平滚动（documentElement.scrollWidth <= clientWidth）；
 *   2. 关键操作未被遮挡/丢失（可见、宽度在视口内，可纵向滚动到达）；
 *   3. 焦点目标满足物理 44px 目标尺寸（zoom 下 CSS 下限 = 44 / zoom）。
 */

const VIEWPORTS = [
  { width: 1280, height: 720, name: "1280x720" },
  { width: 1440, height: 900, name: "1440x900" },
  { width: 1920, height: 1080, name: "1920x1080" },
] as const;

const ZOOMS = [1, 1.25, 1.5, 2] as const;

interface PageChecks {
  /** 每个视口 × 缩放下都要存在并可达的关键操作 */
  controls: Locator[];
  /** 至少要测的目标尺寸控件（从 controls 中挑，避免全部量尺寸拖慢） */
  targetControls: Locator[];
}

async function setZoom(page: Page, viewport: { width: number; height: number }, zoom: number) {
  await page.setViewportSize({
    width: Math.round(viewport.width / zoom),
    height: Math.round(viewport.height / zoom),
  });
}

async function checkPage(
  page: Page,
  viewport: { width: number; height: number; name: string },
  zoom: number,
  checks: PageChecks
) {
  await setZoom(page, viewport, zoom);
  // 等待布局稳定（缩放后渲染队列消化）
  await page.waitForTimeout(120);

  const overflow = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  expect(
    overflow.scrollWidth,
    `${viewport.name} @${zoom * 100}% 出现水平滚动（scrollWidth=${overflow.scrollWidth} > clientWidth=${overflow.clientWidth}）`
  ).toBeLessThanOrEqual(overflow.clientWidth + 1);

  const cssViewportWidth = viewport.width / zoom;
  for (const control of checks.controls) {
    await expect(
      control,
      `${viewport.name} @${zoom * 100}% 关键操作不可见（被遮挡或丢失）`
    ).toBeVisible();
    const box = await control.boundingBox();
    expect(box, `${viewport.name} @${zoom * 100}% 关键操作无布局盒`).not.toBeNull();
    expect(
      box!.x + box!.width,
      `${viewport.name} @${zoom * 100}% 关键操作右缘越出视口`
    ).toBeLessThanOrEqual(cssViewportWidth + 1);
  }

  for (const control of checks.targetControls) {
    const box = await control.boundingBox();
    expect(box, `${viewport.name} @${zoom * 100}% 目标控件无布局盒`).not.toBeNull();
    // 物理 44px（--target-size）在 zoom 下的 CSS 尺寸下限（WCAG 2.5.8
    // 目标尺寸要求两维都不小于 44px）
    const minCss = 44 / zoom - 1;
    expect(
      box!.height,
      `${viewport.name} @${zoom * 100}% 目标控件高度 ${box!.height}px 小于物理 44px 下限 ${minCss}px`
    ).toBeGreaterThanOrEqual(minCss);
    expect(
      box!.width,
      `${viewport.name} @${zoom * 100}% 目标控件宽度 ${box!.width}px 小于物理 44px 下限 ${minCss}px`
    ).toBeGreaterThanOrEqual(minCss);
  }
}

async function fullMatrix(page: Page, checks: PageChecks) {
  for (const viewport of VIEWPORTS) {
    for (const zoom of ZOOMS) {
      await checkPage(page, viewport, zoom, checks);
    }
  }
}

test.describe("Issue 38 — 浏览器缩放 100–200% 桌面路径", () => {
  test("登录页：3 视口 × 4 缩放，无水平滚动、登录按钮可达且满足目标尺寸", async ({ page }) => {
    await page.goto("/login");
    const checks: PageChecks = {
      controls: [
        page.getByRole("button", { name: "登录" }),
        page.getByRole("link", { name: "注册 BridGes" }),
      ],
      targetControls: [page.getByRole("button", { name: "登录" })],
    };
    await fullMatrix(page, checks);
  });

  test("注册页：3 视口 × 4 缩放，无水平滚动、注册按钮可达", async ({ page }) => {
    await page.goto("/register");
    const checks: PageChecks = {
      controls: [
        page.getByRole("button", { name: "注册" }),
        page.getByRole("link", { name: "直接登录" }),
      ],
      targetControls: [page.getByRole("button", { name: "注册" })],
    };
    await fullMatrix(page, checks);
  });

  test("新聊天首页：3 视口 × 4 缩放，无水平滚动、输入区与发送按钮可达", async ({ page }) => {
    const creds = uniqueCredentials("i38z-home");
    await signUp(page, creds.username, creds.qqEmail, "correct-horse-12");
    const composer = page.getByTestId("composer");
    const checks: PageChecks = {
      controls: [
        page.getByTestId("main-content"),
        composer,
        page.getByRole("button", { name: "发送消息" }),
        page.getByRole("link", { name: "新聊天", exact: true }),
      ],
      targetControls: [
        page.getByRole("button", { name: "发送消息" }),
        page.getByRole("link", { name: "新聊天", exact: true }),
      ],
    };
    await fullMatrix(page, checks);
  });

  test("统一搜索页：3 视口 × 4 缩放，无水平滚动、搜索输入框可达", async ({ page }) => {
    const creds = uniqueCredentials("i38z-search");
    await signUp(page, creds.username, creds.qqEmail, "correct-horse-12");
    await page.goto("/search");
    const checks: PageChecks = {
      controls: [page.getByTestId("search-input"), page.getByTestId("main-content")],
      targetControls: [page.getByTestId("search-input")],
    };
    await fullMatrix(page, checks);
  });

  test("知识库页：3 视口 × 4 缩放，无水平滚动、上传入口可达", async ({ page }) => {
    const creds = uniqueCredentials("i38z-kb");
    await signUp(page, creds.username, creds.qqEmail, "correct-horse-12");
    await page.goto("/knowledge-base");
    const checks: PageChecks = {
      controls: [page.getByTestId("kb-upload-button"), page.getByTestId("main-content")],
      targetControls: [page.getByTestId("kb-upload-button")],
    };
    await fullMatrix(page, checks);
  });

  test("账户设置页：3 视口 × 4 缩放，无水平滚动、设置卡片可达", async ({ page }) => {
    const creds = uniqueCredentials("i38z-settings");
    await signUp(page, creds.username, creds.qqEmail, "correct-horse-12");
    await page.goto("/account/settings");
    const checks: PageChecks = {
      controls: [page.getByTestId("main-content")],
      targetControls: [page.getByRole("link", { name: "用户画像" })],
    };
    await fullMatrix(page, checks);
  });

  test("画像中心页：3 视口 × 4 缩放，无水平滚动、主内容可达", async ({ page }) => {
    const creds = uniqueCredentials("i38z-profile");
    await signUp(page, creds.username, creds.qqEmail, "correct-horse-12");
    await page.goto("/account/profile");
    const checks: PageChecks = {
      controls: [page.getByTestId("main-content")],
      targetControls: [page.getByRole("link", { name: "用户画像" })],
    };
    await fullMatrix(page, checks);
  });

  test("对话页：3 视口 × 4 缩放，无水平滚动、输入区与发送可达", async ({ page }) => {
    const creds = uniqueCredentials("i38z-chat");
    await signUp(page, creds.username, creds.qqEmail, "correct-horse-12");
    // 真实创建对话：发送一条消息（未配置 Key 时服务端返回可操作错误，对话仍创建）
    await page.getByTestId("composer").getByRole("textbox").fill("缩放检查对话");
    await page.getByRole("button", { name: "发送消息" }).click();
    await page.waitForURL(/\/chat\//);
    await expect(page.getByTestId("composer")).toBeVisible();
    const checks: PageChecks = {
      controls: [page.getByTestId("composer"), page.getByRole("button", { name: "发送消息" })],
      targetControls: [page.getByRole("button", { name: "发送消息" })],
    };
    await fullMatrix(page, checks);
  });

});
