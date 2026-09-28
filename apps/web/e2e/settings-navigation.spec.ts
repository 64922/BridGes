import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

/**
 * 设置可达性与通勤凭据配置恢复（.scratch/1 工单 02）桌面验收。
 *
 * 全部用例从真实聊天界面开始（注册落地的新聊天首页或已发出的对话），
 * 不直接打开设置 URL 判定入口修复；只经真实 HTTP 链路（API + 后台执行器 +
 * 前端），零 page route mock。
 *
 * 覆盖：鼠标与键盘两条进入路径（设置 → 密钥与模型管理）、刷新与会话过期、
 * 返回原会话后消息与失败结果仍在、通勤缺高德路线 Key 时的配置入口与
 * 「无效候选失败后旧配置保持有效」。凭据正文不出现在 DOM、输入框与浏览器
 * 存储中。
 */

const PASSWORD = "correct-horse-acceptance";
/** 明确写出出行方式，跳过「怎么走」的澄清追问，直接进入需要凭据的路线查询。 */
const COMMUTE_PROMPT = "从南区宿舍步行到图书馆怎么走";
/** 注定被高德拒绝的候选 Key（不是真实凭据，只用于验证「失败保留旧配置」）。 */
const INVALID_AMAP_KEY = "acceptance-probe-not-a-real-amap-key";

// 真实执行器处理模块派发与模型轮次，比默认 30 秒更长。
test.describe.configure({ timeout: 300_000 });

async function signUpAndLand(page: Page, prefix: string) {
  const credentials = uniqueCredentials(prefix);
  await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);
  await expect(page.getByTestId("new-chat-home")).toBeVisible();
  return credentials;
}

/** 从新聊天首页（或对话页输入区）发一条真实消息，返回所在会话 ID。 */
async function sendMessage(page: Page, text: string): Promise<string> {
  const [response] = await Promise.all([
    page.waitForResponse(
      (candidate) =>
        candidate.request().method() === "POST" &&
        candidate.url().includes("/api/chat/first-turn")
    ),
    page.getByRole("button", { name: "发送消息" }).click(),
  ]);
  expect(response.ok(), `首轮 HTTP ${response.status()}`).toBeTruthy();
  await expect(page).toHaveURL(/\/chat\//, { timeout: 60_000 });
  return new URL(page.url()).pathname.split("/").pop() ?? "";
}

async function openAccountMenu(page: Page): Promise<void> {
  await page.getByRole("button", { name: /账户菜单：/ }).click();
  await expect(page.getByRole("menu")).toBeVisible();
}

/** 侧栏「最近对话」里回到指定会话（真实导航，不依赖会话标题文本）。 */
async function returnToConversation(page: Page, conversationId: string): Promise<void> {
  await page
    .getByTestId("app-sidebar")
    .locator(`a[href="/chat/${conversationId}"]`)
    .click();
  await page.waitForURL(`/chat/${conversationId}`);
}

/** 用无效会话 Cookie 覆盖当前源的会话：中间件放行，服务端校验必然失败。 */
async function expireSession(page: Page, context: BrowserContext): Promise<void> {
  await context.addCookies([
    {
      name: "bridges_session",
      value: "expired-session-does-not-exist",
      url: new URL(page.url()).origin,
    },
  ]);
}

test.describe("设置入口（鼠标）", () => {
  test("从聊天侧边栏进入真实设置，再进入密钥与模型管理，并可返回原会话", async ({ page }) => {
    await signUpAndLand(page, "sn-mouse");
    const prompt = "设置入口验收：先建立一条真实对话";
    await page.getByLabel("输入消息").fill(prompt);
    const conversationId = await sendMessage(page, prompt);

    // 入口只在真实聊天页上找：账户菜单 →「设置」
    await openAccountMenu(page);
    await page.getByRole("menuitem", { name: "设置" }).click();
    await page.waitForURL("/account/settings");
    await expect(page.getByRole("heading", { name: "设置" })).toBeVisible();
    // 真实设置页的三个分区，且不落在仅开发环境可达的模板页
    await expect(page.getByRole("heading", { name: "个人资料" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "数据与隐私" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "密钥与模型管理" })).toBeVisible();
    expect(page.url()).not.toContain("/templates");

    await page.getByRole("link", { name: "打开密钥与模型管理" }).click();
    await page.waitForURL("/account/settings/models");
    await expect(page.getByRole("heading", { name: "密钥与模型管理" })).toBeVisible();
    // 四类凭据状态各自明确，且高德两组凭据分区独立可定位
    await expect(page.getByTestId("credential-status")).toHaveCount(4);
    await expect(page.locator("#amap-web-service")).toBeVisible();
    await expect(page.locator("#amap-browser-map")).toBeVisible();

    // 返回聊天：原会话与消息保持完整（侧栏最近对话是真实入口）
    await returnToConversation(page, conversationId);
    await expect(page.getByTestId("chat-thread")).toContainText(prompt);
  });

  test("缺高德路线 Key 的通勤失败卡给出配置入口，配置页可返回原会话", async ({ page }) => {
    await signUpAndLand(page, "sn-commute");

    // 真实派发通勤模块：本机 e2e 环境未配置高德路线 Key，模块如实失败并给出配置位置
    await page
      .getByTestId("composer")
      .getByRole("button", { name: "添加功能或文件" })
      .click();
    await page
      .getByRole("menu", { name: "添加功能或文件" })
      .getByRole("menuitem", { name: "校园通勤" })
      .click();
    await page.getByLabel("输入消息").fill(COMMUTE_PROMPT);
    const conversationId = await sendMessage(page, COMMUTE_PROMPT);

    const card = page.getByTestId("commute-route-card-error");
    await expect(card).toBeVisible({ timeout: 180_000 });
    await expect(card).toContainText("amap_not_configured");
    await expect(card).toContainText("高德路线 Web 服务 Key");

    // 路线 Key 与浏览器地图凭据分别说明，不把两者混为一谈
    await expect(page.getByTestId("commute-route-credential-action")).toContainText(
      "高德浏览器地图"
    );

    await page.getByRole("link", { name: "前往配置高德 Web 服务 Key" }).click();
    await page.waitForURL("**/account/settings/models**");
    // 直接落在「高德 Web 服务」凭据区：锚点命中，键盘焦点一并落到该分区
    await expect(page.locator("#amap-web-service")).toBeInViewport();
    await expect(page.locator("#amap-web-service")).toBeFocused();
    await expect(page.getByTestId("settings-return-to-chat")).toBeVisible();

    // 无效候选 Key 验证失败：给出明确失败，旧配置（未配置）保持有效
    const webServiceCard = page.locator("#amap-web-service");
    await page.getByLabel("高德 Web 服务 Key").fill(INVALID_AMAP_KEY);
    await webServiceCard.getByRole("button", { name: "验证并保存" }).click();
    await expect(page.getByText("高德 Web 服务验证失败")).toBeVisible({ timeout: 60_000 });
    await expect(webServiceCard.getByTestId("credential-status")).toHaveText("未配置");
    // 失败摘要不回显候选密钥正文
    await expect(page.getByTestId("error-summary")).not.toContainText(INVALID_AMAP_KEY);

    // 返回原会话：消息与失败结果原样保留
    await page.getByTestId("settings-return-to-chat").click();
    await page.waitForURL(`/chat/${conversationId}`);
    await expect(page.getByTestId("chat-thread")).toContainText(COMMUTE_PROMPT);
    await expect(page.getByTestId("commute-route-card-error")).toContainText(
      "amap_not_configured"
    );
  });
});

test.describe("设置入口（键盘与刷新）", () => {
  test("键盘从聊天页到达设置与密钥管理；刷新后可读状态且不回显密钥", async ({ page }) => {
    const credentials = await signUpAndLand(page, "sn-keyboard");
    await page.getByLabel("输入消息").fill("键盘路径验收");
    await sendMessage(page, "键盘路径验收");

    // 账户菜单 → 键盘打开 → 首项即「设置」→ Enter 进入
    await page
      .getByRole("button", { name: new RegExp(`账户菜单：${credentials.username}`) })
      .focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("menuitem").first()).toBeFocused();
    await page.keyboard.press("Enter");
    await page.waitForURL("/account/settings");
    expect(page.url()).not.toContain("/templates");

    // 键盘激活「打开密钥与模型管理」链接
    const modelsLink = page.getByRole("link", { name: "打开密钥与模型管理" });
    await modelsLink.focus();
    await expect(modelsLink).toBeFocused();
    await page.keyboard.press("Enter");
    await page.waitForURL("/account/settings/models");
    await expect(page.getByRole("heading", { name: "密钥与模型管理" })).toBeVisible();

    // 刷新：仍在真实设置页，凭据状态可读，全部密钥输入框为空（不回显已有密钥）
    await page.reload();
    await expect(page.getByRole("heading", { name: "密钥与模型管理" })).toBeVisible();
    await expect(page.getByTestId("credential-status")).toHaveCount(4);
    for (const label of [
      // 精确匹配：主模型卡另有一个可选输入「Qwen API Key（可选：与主模型一起更换）」。
      "Qwen API Key",
      "Tavily API Key",
      "高德 Web 服务 Key",
      "高德 JS API Key（Web 平台）",
      "高德 JS API 安全码",
    ]) {
      await expect(page.getByLabel(label, { exact: true })).toHaveValue("");
    }
    // 主模型卡的可选密钥框同样不回显任何值。
    await expect(
      page.getByLabel("Qwen API Key（可选：与主模型一起更换）", { exact: true })
    ).toHaveValue("");
    // 浏览器存储不落任何密钥正文占位
    const storageDump = await page.evaluate(() =>
      Object.entries(window.localStorage)
        .map(([key, value]) => `${key}=${value}`)
        .join("\n")
    );
    expect(storageDump).not.toContain(INVALID_AMAP_KEY);
    expect(storageDump).not.toMatch(/sk-|api[_-]?key/i);
  });

  test("会话过期后设置入口被守卫拦下，不渲染受保护设置内容", async ({ page, context }) => {
    await signUpAndLand(page, "sn-expired");
    await page.getByLabel("输入消息").fill("会话过期验收");
    const conversationId = await sendMessage(page, "会话过期验收");

    // 会话失效后整页重新加载：受保护内容不渲染，底部账户菜单退化为「需要重新登录」
    await expireSession(page, context);
    await page.goto(`/chat/${conversationId}`);
    await expect(page.getByText("需要重新登录").first()).toBeVisible();
    await expect(page.getByTestId("app-sidebar").getByRole("menuitem")).toHaveCount(0);

    // 直接访问设置与密钥页同样被拦：要么退化为权限态，要么被送回登录页，
    // 两条路都不渲染凭据与设置内容（每次访问前重新注入失效 Cookie，避免
    // 服务端在 401 后清掉它，把「已失效会话」误当成「没有会话」）。
    for (const path of ["/account/settings", "/account/settings/models"]) {
      await expireSession(page, context);
      await page.goto(path);
      await expect(
        page
          .getByText("需要重新登录")
          .or(page.getByRole("heading", { name: "登录" }))
          .first()
      ).toBeVisible();
      await expect(page.getByTestId("credential-status")).toHaveCount(0);
      await expect(page.getByRole("heading", { name: "密钥与模型管理" })).toHaveCount(0);
    }
  });
});
