import { expect, test, type Locator, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

/**
 * Issue 38 — V1：正式路由关键页面在 1280×720 / 1440×900 / 1920×1080
 * 的视觉回归快照（登录 / 注册由 issue07-auth-pages.spec.ts 覆盖，不重复）。
 *
 * 快照确定性约定：
 * - 真实注册 + 真实数据流（与 issue08 的固定账户 mock 不同：本 spec 以
 *   真实页面为准，动态区域用 mask 遮罩而不是伪数据）；
 * - 遮罩动态区域：侧栏底部账户菜单（用户名每次注册不同）、最近对话区
 *   （相对时间戳每次运行不同）——遮罩色取侧栏背景，视觉上自然融合；
 * - 打开前模拟 prefers-reduced-motion: reduce（新聊天首页的轮换名言
 *   静止在第一条、过渡即时完成）；
 * - 快照使用 animations: "disabled"（Playwright 官方约定）；
 * - 其余区域均为真实内容（空态 / 未配置态等确定性状态）。
 */

const VIEWPORTS = [
  { width: 1280, height: 720, name: "1280x720" },
  { width: 1440, height: 900, name: "1440x900" },
  { width: 1920, height: 1080, name: "1920x1080" },
] as const;

/** 侧栏动态区域（账户名 / 相对时间戳）——快照遮罩，颜色取侧栏底色。 */
function sidebarMask(page: Page): Locator[] {
  return [
    page.getByRole("button", { name: /账户菜单：/ }),
    page.locator('section[aria-label="最近对话"]'),
  ];
}

/**
 * 打开页面并等待「内容态标志」出现后再截图：数据加载完成前截图会把
 * 加载态误存为基线（各次运行 API 延迟不同，状态时序不可靠）。
 */
async function openStable(page: Page, path: string, contentMarker: Locator) {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto(path);
  await expect(page.getByTestId("main-content")).toBeVisible();
  // 数据加载可能受并行负载影响，放宽到 15s（超过默认 5s）
  await expect(contentMarker).toBeVisible({ timeout: 15_000 });
  await page.waitForTimeout(150);
}

async function expectScreenshots(page: Page, name: string, extraMasks: Locator[] = []) {
  for (const viewport of VIEWPORTS) {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.waitForTimeout(120);
    await expect(page).toHaveScreenshot(`${name}-${viewport.name}.png`, {
      animations: "disabled",
      fullPage: false,
      mask: [...sidebarMask(page), ...extraMasks],
      maskColor: "#f2efe6",
    });
  }
}

// 快照对基础设施瞬时抖动敏感（并行负载下偶发会话竞态），给本 spec
// 两次重试（与 CI 的 retries: 2 一致）；重试不掩盖真实视觉回归——同一
// 快照在重试后仍不一致才会失败。
test.describe.configure({ retries: 2 });

test.describe("Issue 38 — 正式路由视觉回归", () => {
  test("新聊天首页：空白态三视口", async ({ page }) => {
    const creds = uniqueCredentials("i38v-home");
    await signUp(page, creds.username, creds.qqEmail, "correct-horse-12");
    await openStable(page, "/", page.getByRole("heading", { name: "有什么可以帮你的？" }));
    await expectScreenshots(page, "new-chat-home");
  });

  test("对话页：空对话三视口", async ({ page }) => {
    const creds = uniqueCredentials("i38v-chat");
    await signUp(page, creds.username, creds.qqEmail, "correct-horse-12");
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.getByTestId("composer").getByRole("textbox").fill("视觉回归对话");
    await page.getByRole("button", { name: "发送消息" }).click();
    await page.waitForURL(/\/chat\//);
    await expect(page.getByTestId("composer")).toBeVisible();
    // e2e 环境契约：playwright.config.ts 注入测试密钥但无 Qwen 账户 Key，
    // 自动发送必然失败并显示确定文案的错误横幅。等待横幅出现再截图，
    // 避免「横幅已出现 / 尚未出现」的时序竞态污染快照；若未来 e2e 环境
    // 配置了真实 Key，此断言会立即暴露（横幅不再出现），需同步更新。
    await expect(page.getByRole("alert").first()).toBeVisible();
    await expectScreenshots(page, "chat-conversation");
  });

  test("统一搜索页三视口", async ({ page }) => {
    const creds = uniqueCredentials("i38v-search");
    await signUp(page, creds.username, creds.qqEmail, "correct-horse-12");
    await openStable(page, "/search", page.getByTestId("search-input"));
    await expectScreenshots(page, "search");
  });

  test("知识库页三视口", async ({ page }) => {
    const creds = uniqueCredentials("i38v-kb");
    await signUp(page, creds.username, creds.qqEmail, "correct-horse-12");
    await openStable(page, "/knowledge-base", page.getByTestId("kb-upload-button"));
    await expectScreenshots(page, "knowledge-base");
  });

  test("画像中心页三视口", async ({ page }) => {
    const creds = uniqueCredentials("i38v-profile");
    await signUp(page, creds.username, creds.qqEmail, "correct-horse-12");
    await openStable(page, "/account/profile", page.getByRole("heading", { name: "数字分身画像" }));
    await expectScreenshots(page, "profile-center");
  });

  test("账户设置页三视口", async ({ page }) => {
    const creds = uniqueCredentials("i38v-settings");
    await signUp(page, creds.username, creds.qqEmail, "correct-horse-12");
    await openStable(page, "/account/settings", page.getByRole("heading", { name: "设置", exact: true }));
    await expectScreenshots(page, "account-settings");
  });
});
