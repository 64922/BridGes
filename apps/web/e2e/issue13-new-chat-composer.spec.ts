import { expect, test, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

/**
 * Issue 13 — 交付新聊天输入区与完整空白态。
 *
 * 覆盖：登录落点与「新聊天」为同一空白态；输入区顶部恰好五条学习名言
 * 轮换并尊重减少动态效果；无临时聊天 / 模型选择器 / 实时语音入口；
 * 「+」菜单固定六入口（未实现入口给明确不可用原因）；三张建议卡预填
 * 结构化意图并走正常消息流；消息操作、键盘路径与超长中文布局。
 *
 * 发送路径用协议级替身（与 issue11 同一策略）；Key 预检拦截沿用真实
 * 后端（未启用对话存储的实例自动跳过）。
 */

const NOW = "2026-08-03T00:00:00Z";

const FIVE_QUOTES = [
  "学而不思则罔，思而不学则殆。",
  "知之者不如好之者，好之者不如乐之者。",
  "读书破万卷，下笔如有神。",
  "吾生也有涯，而知也无涯。",
  "少壮不努力，老大徒伤悲。",
];

function sseStream(messageId: string, userMessageId: string, delta: string, done: boolean) {
  const started = `event: started\ndata: ${JSON.stringify({
    kind: "started",
    conversation_id: "mock-1",
    user_message_id: userMessageId,
    message_id: messageId,
    attempt_number: 1,
  })}\n\n`;
  const deltaEvent = `event: delta\ndata: ${JSON.stringify({ kind: "delta", message_id: messageId, delta })}\n\n`;
  if (!done) return `${started}${deltaEvent}`;
  const doneEvent = `event: done\ndata: ${JSON.stringify({
    kind: "done",
    message_id: messageId,
    message: {
      message_id: messageId,
      conversation_id: "mock-1",
      role: "assistant",
      attempt_number: 1,
      status: "done",
      content: delta,
      error_code: null,
      error_message: null,
      duration_ms: 90,
      model_id: "qwen3.7-plus-2026-05-26",
      run_lock_id: "lock-mock",
      created_at: NOW,
      updated_at: NOW,
    },
  })}\n\n`;
  return `${started}${deltaEvent}${doneEvent}`;
}

/** 最小聊天 API 替身：创建对话、历史、发送 SSE、停止。 */
async function installMockChatApi(page: Page, options: { createDelayMs?: number } = {}) {
  const state = {
    messages: [] as {
      message_id: string;
      conversation_id: string;
      role: string;
      attempt_number: number;
      status: string;
      content: string;
      error_code: null;
      error_message: null;
      duration_ms: number | null;
      model_id: string | null;
      run_lock_id: string | null;
      created_at: string;
      updated_at: string;
    }[],
  };

  const history = () => ({
    conversation_id: "mock-1",
    title: "测试对话",
    mode: "companion",
    created_at: NOW,
    updated_at: NOW,
    messages: state.messages,
  });

  await page.route("**/api/chat/conversations", async (route) => {
    if (route.request().method() === "POST") {
      if (options.createDelayMs) {
        await new Promise((resolve) => setTimeout(resolve, options.createDelayMs));
      }
      await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify(history()) });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ conversations: [] }),
    });
  });

  await page.route("**/api/chat/conversations/mock-1", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(history()) });
  });

  await page.route("**/api/chat/conversations/mock-1/messages", async (route) => {
    if (route.request().method() !== "POST") return;
    const body = JSON.parse(route.request().postData() ?? "{}");
    const userMessage = {
      message_id: "u-1",
      conversation_id: "mock-1",
      role: "user",
      attempt_number: 1,
      status: "done",
      content: body.content,
      error_code: null,
      error_message: null,
      duration_ms: null,
      model_id: null,
      run_lock_id: null,
      created_at: NOW,
      updated_at: NOW,
    };
    const assistantMessage = { ...userMessage, message_id: "a-1", role: "assistant", content: "" };
    assistantMessage.status = "streaming";
    state.messages.push(userMessage, assistantMessage);
    const done = body.content !== "不要结束";
    if (done) {
      // 与真实服务端一致：done 即已落库，后续 GET 返回最终正文与状态
      assistantMessage.content = "这是替身生成的回答。";
      assistantMessage.status = "done";
    }
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: sseStream("a-1", "u-1", "这是替身生成的回答。", done),
    });
  });

  await page.route("**/api/chat/conversations/mock-1/messages/*/stop", async (route) => {
    const target = state.messages.find((m) => m.message_id === "a-1");
    if (target) {
      target.status = "stopped";
      target.content = target.content || "部分";
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ message: target ?? {} }),
    });
  });
}

async function registerAndEnterHome(page: Page): Promise<void> {
  const credentials = uniqueCredentials("issue13");
  await signUp(page, credentials.username, credentials.qqEmail, "Passw0rd123!");
  await expect(page.getByRole("heading", { name: "有什么可以帮你的？" })).toBeVisible();
}

test.describe("Issue 13 — 新聊天输入区与完整空白态", () => {
  test("登录落点空白态：名言在输入区顶部、三张建议卡、无临时聊天/模型选择器/实时语音", async ({ page }) => {
    await registerAndEnterHome(page);

    // 名言位于输入区顶部，恰好五条且带出处
    const quote = page.getByTestId("empty-quote");
    await expect(quote).toBeVisible();
    await expect(quote).toHaveAttribute("data-quote-count", "5");
    await expect(quote).toContainText("——");
    expect(FIVE_QUOTES).toHaveLength(5);
    const quoteBox = await quote.boundingBox();
    const composerBox = await page.getByTestId("composer").boundingBox();
    expect(quoteBox!.y + quoteBox!.height).toBeLessThanOrEqual(composerBox!.y);

    // 三张原创图标建议卡在输入区下方
    const cards = page.getByTestId("suggestion-cards");
    await expect(cards).toBeVisible();
    for (const label of ["论文搜索", "文章人味化", "生涯规划助手"]) {
      const card = cards.getByRole("button", { name: label });
      await expect(card).toBeVisible();
      await expect(card.locator("svg").first()).toBeVisible();
    }
    const cardsBox = await cards.boundingBox();
    expect(cardsBox!.y).toBeGreaterThanOrEqual(composerBox!.y + composerBox!.height);

    // 无临时聊天、无模型选择器、无实时语音/语音通话入口
    await expect(page.getByText("临时聊天")).toHaveCount(0);
    await expect(page.getByRole("button", { name: /模型|极速/ })).toHaveCount(0);
    await expect(page.getByRole("button", { name: /语音通话|实时语音|启动语音功能/ })).toHaveCount(0);
    // 输入区只有听写与发送
    const composer = page.getByTestId("composer");
    await expect(composer.getByRole("button", { name: "开始听写" })).toBeVisible();
    await expect(composer.getByRole("button", { name: "发送消息" })).toBeVisible();
  });

  test("点击侧栏「新聊天」回到同一空白态", async ({ page }) => {
    await registerAndEnterHome(page);
    await installMockChatApi(page);

    await page.goto("/chat/mock-1");
    await expect(page.getByTestId("chat-thread")).toBeVisible();

    await page
      .getByTestId("app-sidebar")
      .getByRole("link", { name: "新聊天", exact: true })
      .click();
    await page.waitForURL("/");
    await expect(page.getByRole("heading", { name: "有什么可以帮你的？" })).toBeVisible();
    await expect(page.getByTestId("empty-quote")).toBeVisible();
    await expect(page.getByTestId("suggestion-cards")).toBeVisible();
  });

  test("名言按节奏轮换且尊重减少动态效果设置", async ({ page }) => {
    await registerAndEnterHome(page);
    const quote = page.getByTestId("empty-quote");

    // 默认动效：8 秒节奏内轮换到下一条，且内容在五条池内
    await expect(quote).toHaveAttribute("data-quote-index", "0");
    await expect(quote).toHaveAttribute("data-quote-index", "1", { timeout: 10_000 });
    await expect(quote).toContainText(FIVE_QUOTES[1]);
    await expect(quote).toContainText("《论语·雍也》");

    // 减少动态效果：静止展示第一条，不轮换
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.reload();
    const reducedQuote = page.getByTestId("empty-quote");
    await expect(reducedQuote).toBeVisible();
    await expect(reducedQuote).toContainText(FIVE_QUOTES[0]);
    await page.waitForTimeout(9_000);
    await expect(reducedQuote).toHaveAttribute("data-quote-index", "0");
  });

  test("「+」菜单固定七入口，未实现入口给明确不可用原因", async ({ page }) => {
    await registerAndEnterHome(page);
    const composer = page.getByTestId("composer");

    await composer.getByRole("button", { name: "更多功能" }).click();
    const menu = page.getByRole("menu", { name: "更多功能" });
    await expect(menu).toBeVisible();

    // 固定顺序七入口（Issue 31：图片生成为真实任务对话框入口）
    const labels = await menu.getByRole("menuitem").allTextContents();
    expect(labels).toEqual([
      "上传文件/图片",
      "论文搜索",
      "文章人味化",
      "生涯规划助手",
      "图片生成",
      "选择学习项目",
      "选择已启用插件",
    ]);

    // Issue 31：「图片生成」已落地——打开真实任务对话框（生成页签），
    // Esc 关闭后不产生假结果；对话框焦点陷阱收起时菜单同时关闭，需重新打开。
    await menu.getByRole("menuitem", { name: "图片生成" }).click();
    const imageDialog = page.getByRole("dialog", { name: "图片生成与编辑" });
    await expect(imageDialog).toBeVisible();
    await expect(imageDialog.getByTestId("image-prompt-input")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await composer.getByRole("button", { name: "更多功能" }).click();
    await expect(menu).toBeVisible();

    // Issue 19：「选择学习项目」已落地——打开真实选择对话框（当前账户无项目
    // 时给真实空状态），不再是不可用占位；Esc 关闭后不产生假结果。
    await menu.getByRole("menuitem", { name: "选择学习项目" }).click();
    const projectPicker = page.getByRole("dialog", { name: "选择学习项目" });
    await expect(projectPicker).toBeVisible();
    await expect(projectPicker).toContainText("还没有学习项目");
    await expect(page.getByTestId("tool-unavailable-notice")).toHaveCount(0);
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toHaveCount(0);

    // 未实现入口：明确不可用原因，不弹假对话框、不产生假结果
    await composer.getByRole("button", { name: "更多功能" }).click();
    await page.getByRole("menu", { name: "更多功能" }).getByRole("menuitem", { name: "选择已启用插件" }).click();
    const notice = page.getByTestId("tool-unavailable-notice");
    await expect(notice).toBeVisible();
    await expect(notice).toContainText("目前没有可选择的已启用插件");
  });

  test("建议卡预填结构化意图并聚焦，发送走正常消息流", async ({ page }) => {
    await registerAndEnterHome(page);
    await installMockChatApi(page);

    // 点击建议卡 → 预填 + 聚焦（不直接跳过输入区）
    await page.getByTestId("suggestion-cards").getByRole("button", { name: "论文搜索" }).click();
    const input = page.getByTestId("composer").getByLabel("输入消息");
    await expect(input).toHaveValue(/^论文搜索：/);
    await expect(input).toBeFocused();

    // 补全内容后 Enter 发送：创建对话 → 跳转 → 自动发送 → 流式回答
    await input.type("量子纠错近一年综述");
    await input.press("Enter");
    await page.waitForURL(/\/chat\/mock-1/);
    const thread = page.getByRole("list", { name: "对话消息" });
    await expect(thread).toContainText("论文搜索：量子纠错近一年综述");
    await expect(thread).toContainText("这是替身生成的回答。");

    // 消息操作：复制、重试、反馈、朗读，中文可访问名称
    const toolbar = page.getByRole("toolbar", { name: "消息操作" }).last();
    await expect(toolbar.getByRole("button", { name: "复制" })).toBeVisible();
    await expect(toolbar.getByRole("button", { name: "重试" })).toBeVisible();
    await expect(toolbar.getByRole("button", { name: "回答有帮助" })).toBeVisible();
    await expect(toolbar.getByRole("button", { name: "回答需改进" })).toBeVisible();
  });

  test("键盘路径：遍历「+」菜单、Esc 关闭归还焦点、发送与停止", async ({ page }) => {
    await registerAndEnterHome(page);
    await installMockChatApi(page);

    const composer = page.getByTestId("composer");
    const plusButton = composer.getByRole("button", { name: "更多功能" });

    // 键盘打开菜单、方向键遍历、Esc 关闭并归还焦点
    await plusButton.focus();
    await page.keyboard.press("Enter");
    const menu = page.getByRole("menu", { name: "更多功能" });
    await expect(menu).toBeVisible();
    await expect(menu.getByRole("menuitem", { name: "上传文件/图片" })).toBeFocused();
    await page.keyboard.press("ArrowDown");
    await expect(menu.getByRole("menuitem", { name: "论文搜索" })).toBeFocused();
    await page.keyboard.press("End");
    await expect(menu.getByRole("menuitem", { name: "选择已启用插件" })).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(menu).not.toBeVisible();
    await expect(plusButton).toBeFocused();

    // 键盘触发建议卡：聚焦 + Enter → 进入真实任务流程
    // （Issue 28：「文章人味化」打开任务对话框而非预填；「论文搜索」仍预填）
    await page.getByTestId("suggestion-cards").getByRole("button", { name: "文章人味化" }).focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("dialog", { name: /文章人味化/ })).toBeVisible();
    await page.getByTestId("humanizer-cancel").click();
    const input = composer.getByLabel("输入消息");
    await page.getByTestId("suggestion-cards").getByRole("button", { name: "论文搜索" }).focus();
    await page.keyboard.press("Enter");
    await expect(input).toHaveValue(/^论文搜索：/);

    // 键盘发送：生成中出现停止入口，Esc 停止
    await input.fill("");
    await input.focus();
    await page.keyboard.type("不要结束");
    await page.keyboard.press("Enter");
    await page.waitForURL(/\/chat\/mock-1/);
    await expect(page.getByRole("button", { name: "停止生成" })).toBeVisible();
    await page.keyboard.press("Escape");
    const thread = page.getByRole("list", { name: "对话消息" });
    await expect(thread).toContainText("部分");
    await expect(page.getByRole("button", { name: "发送消息" })).toBeVisible();
  });

  test("超长中文与粘贴内容不破坏桌面布局，发送后创建失败展示可恢复错误", async ({ page, context }) => {
    await registerAndEnterHome(page);

    // 超长中文：输入框高度封顶，页面无横向滚动
    const input = page.getByTestId("composer").getByLabel("输入消息");
    await input.fill("量子".repeat(800));
    const inputBox = await input.boundingBox();
    expect(inputBox!.height).toBeLessThanOrEqual(200);
    const overflowX = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflowX).toBeLessThanOrEqual(1);

    // 粘贴路径：超长粘贴内容同样不破坏布局
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await input.fill("");
    await input.focus();
    await page.evaluate((text) => navigator.clipboard.writeText(text), "纠缠".repeat(800));
    await page.keyboard.press("Control+V");
    await expect(input).toHaveValue(/纠缠/);
    const pasteBox = await input.boundingBox();
    expect(pasteBox!.height).toBeLessThanOrEqual(200);
    const overflowAfterPaste = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflowAfterPaste).toBeLessThanOrEqual(1);

    // 创建对话失败（替身 503）：错误横幅真实可操作，停留在空白态
    await page.route("**/api/chat/conversations", async (route) => {
      if (route.request().method() === "POST") {
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ detail: { error: "chat_store_unavailable", message: "对话存储暂不可用，请稍后重试。" } }),
        });
        return;
      }
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ conversations: [] }) });
    });
    await page.getByTestId("composer").getByRole("button", { name: "发送消息" }).click();
    await expect(page.getByTestId("main-content").getByRole("alert")).toContainText("对话存储暂不可用");
    await expect(page.getByRole("heading", { name: "有什么可以帮你的？" })).toBeVisible();
  });

  test("创建对话期间展示 loading 状态并防止重复发送", async ({ page }) => {
    await registerAndEnterHome(page);
    await installMockChatApi(page, { createDelayMs: 900 });

    const composer = page.getByTestId("composer");
    await composer.getByLabel("输入消息").fill("你好");
    await composer.getByRole("button", { name: "发送消息" }).click();

    // loading：创建对话期间有中文状态提示，且不会出现第二个发送入口
    await expect(page.getByText("正在创建对话…")).toBeVisible();
    await expect(composer.getByRole("button", { name: "发送消息" })).toHaveCount(0);
    await page.waitForURL(/\/chat\/mock-1/);
    await expect(page.getByRole("list", { name: "对话消息" })).toContainText("你好");
  });

  test("无 Key 时建议卡预填后发送仍被服务端预检拦截（不跳过授权与审计）", async ({ page }) => {
    await registerAndEnterHome(page);

    // 能力预检依赖真实后端：未启用对话存储的实例跳过（reuseExistingServer 场景）
    const chatReady = await page.request.get("/api/chat/conversations");
    if (chatReady.status() === 503) {
      test.skip(true, "当前 API 实例未启用对话存储（BRIDGES_DATABASE_URL），跳过预检用例。");
    }

    // Issue 28/29：「文章人味化」「生涯规划助手」已改为打开真实任务对话框；
    // 本用例语义是「预填后发送不跳过授权与审计」，改用仍预填的「论文搜索」卡。
    await page.getByTestId("suggestion-cards").getByRole("button", { name: "论文搜索" }).click();
    const input = page.getByTestId("composer").getByLabel("输入消息");
    await expect(input).toHaveValue(/^论文搜索：/);
    await input.type("研一如何安排论文阅读");
    await input.press("Enter");

    // 正常消息流：创建真实对话并跳转，未配置 Key → 预检中文提示 + 设置入口
    await page.waitForURL(/\/chat\/[^/]+$/);
    const alert = page.getByRole("alert").filter({ hasText: "尚未配置 Qwen API Key" });
    await expect(alert).toContainText("尚未配置 Qwen API Key");
    await expect(alert.getByRole("link", { name: "前往设置配置 Key" })).toBeVisible();
  });
});
