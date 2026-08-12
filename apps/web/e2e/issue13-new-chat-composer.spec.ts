import { expect, test, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

const NOW = "2026-08-03T00:00:00Z";

function sseStream(messageId: string, userMessageId: string) {
  const started = `event: started\ndata: ${JSON.stringify({
    kind: "started",
    conversation_id: "mock-1",
    user_message_id: userMessageId,
    message_id: messageId,
    attempt_number: 1,
  })}\n\n`;
  const done = `event: done\ndata: ${JSON.stringify({
    kind: "done",
    message_id: messageId,
    message: {
      message_id: messageId,
      conversation_id: "mock-1",
      role: "assistant",
      attempt_number: 1,
      status: "done",
      content: "这是替身生成的回答。",
      error_code: null,
      error_message: null,
      duration_ms: 90,
      model_id: "qwen3.7-plus-2026-05-26",
      run_lock_id: "lock-mock",
      created_at: NOW,
      updated_at: NOW,
    },
  })}\n\n`;
  return `${started}${done}`;
}

async function installMockChatApi(
  page: Page,
  options: { delayMs?: number; failOnce?: boolean; loseResponseOnce?: boolean } = {}
): Promise<{
  firstTurnBodies: Array<Record<string, unknown>>;
  conversationPostCount: () => number;
  createdUserMessageCount: () => number;
}> {
  const firstTurnBodies: Array<Record<string, unknown>> = [];
  let conversationPostCount = 0;
  let createdUserMessageCount = 0;
  let sequence = 0;
  let mode: "companion" | "study" = "companion";
  let failOnce = options.failOnce ?? false;
  let loseResponseOnce = options.loseResponseOnce ?? false;
  const eventStreams = new Map<string, string>();
  const idempotentResponses = new Map<string, Record<string, unknown>>();

  const history = () => ({
    conversation_id: "mock-1",
    title: "测试对话",
    mode,
    mode_locked: true,
    created_at: NOW,
    updated_at: NOW,
    messages: [],
    mode_events: [],
  });

  await page.route("**/api/chat/conversations", async (route) => {
    if (route.request().method() === "POST") {
      conversationPostCount += 1;
      await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify(history()) });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ conversations: [] }),
    });
  });

  await page.route("**/api/chat/first-turn", async (route) => {
    const body = (route.request().postDataJSON() ?? {}) as Record<string, unknown>;
    firstTurnBodies.push(body);
    if (options.delayMs) await new Promise((resolve) => setTimeout(resolve, options.delayMs));
    if (failOnce) {
      failOnce = false;
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({
          detail: { error: "chat_store_unavailable", message: "对话存储暂不可用，请稍后重试。" },
        }),
      });
      return;
    }

    const idempotencyKey = typeof body.idempotency_key === "string" ? body.idempotency_key : null;
    const replay = idempotencyKey ? idempotentResponses.get(idempotencyKey) : undefined;
    if (replay) {
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({ ...replay, idempotent_replay: true }),
      });
      return;
    }

    mode = body.mode === "study" ? "study" : "companion";
    sequence += 1;
    createdUserMessageCount += 1;
    const userMessageId = `u-${sequence}`;
    const assistantMessageId = `a-${sequence}`;
    const userMessage = {
      message_id: userMessageId,
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
    const assistantMessage = {
      ...userMessage,
      message_id: assistantMessageId,
      role: "assistant",
      content: "",
      status: "streaming",
    };
    eventStreams.set(assistantMessageId, sseStream(assistantMessageId, userMessageId));
    const responseBody = {
      conversation: { ...history(), messages: [userMessage, assistantMessage] },
      run_id: `run-${assistantMessageId}`,
      cursor: 1,
      user_message: userMessage,
      assistant_message: assistantMessage,
      idempotent_replay: false,
    };
    if (idempotencyKey) idempotentResponses.set(idempotencyKey, responseBody);
    if (loseResponseOnce) {
      loseResponseOnce = false;
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({
          detail: { error: "chat_store_unavailable", message: "响应丢失，请重试。" },
        }),
      });
      return;
    }
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify(responseBody),
    });
  });

  await page.route("**/api/chat/conversations/mock-1", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(history()) });
  });

  await page.route("**/api/chat/conversations/mock-1/messages/*/events", async (route) => {
    const url = new URL(route.request().url());
    const messageId = url.pathname.split("/").at(-2) ?? "";
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: eventStreams.get(messageId) ?? "",
    });
  });

  return {
    firstTurnBodies,
    conversationPostCount: () => conversationPostCount,
    createdUserMessageCount: () => createdUserMessageCount,
  };
}

async function registerAndEnterHome(page: Page): Promise<void> {
  const credentials = uniqueCredentials("issue21");
  await signUp(page, credentials.username, credentials.qqEmail, "Passw0rd123!");
  await expect(page.getByTestId("new-chat-home")).toBeVisible();
  await expect(page.locator("h1.sc-visually-hidden")).toHaveText("新聊天");
}

function getBlankStateNote(page: Page) {
  return page.getByText("BridGes 的回答会标注依据与来源；重要内容请核对引用。", {
    exact: true,
  });
}

test.describe("Issue 21 — 精简新聊天首页", () => {
  test("登录落点与侧栏新聊天是同一最小空白态", async ({ page }) => {
    await registerAndEnterHome(page);
    const composer = page.getByTestId("composer");

    await expect(page.getByTestId("empty-quote")).toBeVisible();
    await expect(composer.getByRole("button", { name: "开始听写" })).toBeVisible();
    await expect(composer.getByRole("button", { name: "发送消息" })).toBeVisible();
    await expect(page.getByTestId("suggestion-cards")).toHaveCount(0);
    await expect(composer.getByRole("button", { name: "更多功能" })).toHaveCount(0);
    await expect(composer.getByTestId("composer-source-layers")).toHaveCount(0);
    await expect(page.getByText("长期科学学习与表达伙伴")).toHaveCount(0);
    await expect(page.getByText("有什么可以帮你的？")).toHaveCount(0);
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(page.locator('input[type="file"]')).toHaveCount(0);

    const mode = page.getByTestId("mode-toggle");
    await expect(mode.getByRole("button", { name: "日常陪伴" })).toHaveAttribute("aria-pressed", "true");
    await expect(mode.getByRole("button", { name: "学习模式" })).toHaveAttribute("aria-pressed", "false");

    await page.getByTestId("app-sidebar").getByRole("link", { name: "新聊天", exact: true }).click();
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByTestId("new-chat-home")).toBeVisible();
    await expect(page.getByTestId("empty-quote")).toBeVisible();
  });

  test("轮换名言保留，减少动态效果时静止在第一条", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await registerAndEnterHome(page);
    const quote = page.getByTestId("empty-quote");
    const note = getBlankStateNote(page);
    await expect(quote).toHaveAttribute("data-quote-count", "5");
    await expect(quote).toContainText("——");
    await expect(quote).toHaveCSS("font-size", "24px");
    await expect(note).toBeVisible();
    const quoteBox = await quote.boundingBox();
    const composerBox = await page.getByTestId("composer").boundingBox();
    expect(quoteBox!.y + quoteBox!.height).toBeLessThanOrEqual(composerBox!.y);

    const centeredGroup = await page.getByTestId("new-chat-home").evaluate((blankState) => {
      const quote = blankState.querySelector('[data-testid="empty-quote"]');
      const composer = blankState.querySelector('[data-testid="composer"]');
      if (!quote || !composer) throw new Error("首页居中块缺少格言或输入框");
      const blankStateBox = blankState.getBoundingClientRect();
      const quoteBox = quote.getBoundingClientRect();
      const composerBox = composer.getBoundingClientRect();
      const style = getComputedStyle(blankState);
      const contentTop = blankStateBox.top + Number.parseFloat(style.paddingTop);
      const contentBottom = blankStateBox.bottom - Number.parseFloat(style.paddingBottom);
      return {
        groupCenter: (quoteBox.top + composerBox.bottom) / 2,
        contentCenter: (contentTop + contentBottom) / 2,
      };
    });
    expect(Math.abs(centeredGroup.groupCenter - centeredGroup.contentCenter)).toBeLessThanOrEqual(12);

    const layout = await note.evaluate((element) => {
      const blankState = element.closest('[data-testid="new-chat-home"]');
      if (!blankState) throw new Error("免责声明未位于新聊天空白态中");
      const noteBox = element.getBoundingClientRect();
      const blankStateBox = blankState.getBoundingClientRect();
      const blankStateStyle = getComputedStyle(blankState);
      return {
        noteBottom: noteBox.bottom,
        blankStateBottom: blankStateBox.bottom,
        paddingBottom: Number.parseFloat(blankStateStyle.paddingBottom),
        parentTestId: element.parentElement?.getAttribute("data-testid"),
      };
    });
    expect(layout.parentTestId).toBe("new-chat-home");
    expect(layout.blankStateBottom - layout.noteBottom).toBeCloseTo(layout.paddingBottom, 0);

    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.reload();
    await expect(page.getByTestId("empty-quote")).toHaveAttribute("data-quote-index", "0");
  });

  test("短视口下免责声明不与输入框重叠", async ({ page }) => {
    await registerAndEnterHome(page);
    await page.setViewportSize({ width: 1280, height: 600 });

    const note = getBlankStateNote(page);
    const quote = page.getByTestId("empty-quote");
    const composer = page.getByTestId("composer");
    const noteBox = await note.boundingBox();
    const quoteBox = await quote.boundingBox();
    const composerBox = await composer.boundingBox();

    expect(noteBox).not.toBeNull();
    expect(quoteBox).not.toBeNull();
    expect(composerBox).not.toBeNull();
    expect(noteBox!.y).toBeGreaterThanOrEqual(quoteBox!.y + quoteBox!.height);
    expect(noteBox!.y).toBeGreaterThanOrEqual(composerBox!.y + composerBox!.height);

    const blankState = page.getByTestId("new-chat-home");
    await blankState.locator(":scope > div").evaluate((element) => {
      element.style.minHeight = "1200px";
    });
    expect(
      await blankState.evaluate((element) => element.scrollHeight > element.clientHeight)
    ).toBe(true);
    await blankState.evaluate((element) => {
      element.scrollTop = element.scrollHeight;
    });
    await expect(note).toBeInViewport();
  });

  test("两种模式首轮请求只携带自然语言合同字段", async ({ page }) => {
    await registerAndEnterHome(page);
    const mock = await installMockChatApi(page);
    const mode = page.getByTestId("mode-toggle");
    await mode.getByRole("button", { name: "学习模式" }).click();
    await page.getByLabel("输入消息").fill("找几篇 Transformer 论文");
    await page.getByRole("button", { name: "发送消息" }).click();
    await page.waitForURL(/\/chat\/mock-1/);

    expect(mock.firstTurnBodies).toHaveLength(1);
    const body = mock.firstTurnBodies[0];
    expect(body.content).toBe("找几篇 Transformer 论文");
    expect(body.mode).toBe("study");
    expect(typeof body.idempotency_key).toBe("string");
    for (const retiredField of [
      "conversation_id",
      "project_id",
      "plugin_selection",
      "use_knowledge_base",
      "use_profile",
      "skill_id",
      "skill_input",
      "image",
      "video",
      "mcp_call",
    ]) {
      expect(body).not.toHaveProperty(retiredField);
    }
    expect(mock.conversationPostCount()).toBe(0);
  });

  test("自然语言能力意图不打开菜单或对话框", async ({ page }) => {
    await registerAndEnterHome(page);
    const mock = await installMockChatApi(page);
    const input = page.getByLabel("输入消息");
    for (const prompt of ["找几篇 Transformer 论文", "润色这段文章", "生成一张小猫图片"]) {
      await page.goto("/");
      await input.fill(prompt);
      await expect(page.getByRole("dialog")).toHaveCount(0);
      await input.press("Enter");
      await page.waitForURL(/\/chat\/mock-1/);
    }
    expect(mock.firstTurnBodies.map((body) => body.content)).toEqual([
      "找几篇 Transformer 论文",
      "润色这段文章",
      "生成一张小猫图片",
    ]);
  });

  test("发送失败保留输入与模式，重试后才导航", async ({ page }) => {
    await registerAndEnterHome(page);
    const mock = await installMockChatApi(page, { failOnce: true });
    await page.getByTestId("mode-toggle").getByRole("button", { name: "学习模式" }).click();
    const input = page.getByLabel("输入消息");
    const note = getBlankStateNote(page);
    const noteBeforeSend = await note.boundingBox();
    expect(noteBeforeSend).not.toBeNull();
    await input.fill("需要保留的文本");
    await page.getByRole("button", { name: "发送消息" }).click();
    await expect(page.getByTestId("new-chat-home").getByRole("alert")).toContainText("对话存储暂不可用");
    await expect(input).toHaveValue("需要保留的文本");
    await expect(input).toBeFocused();
    const noteAfterError = await note.boundingBox();
    expect(noteAfterError).not.toBeNull();
    expect(noteAfterError!.y + noteAfterError!.height).toBeCloseTo(
      noteBeforeSend!.y + noteBeforeSend!.height,
      0
    );
    await expect(page.getByTestId("mode-toggle").getByRole("button", { name: "学习模式" })).toHaveAttribute("aria-pressed", "true");
    await expect(page).toHaveURL(/\/$/);
    await input.press("Enter");
    await page.waitForURL(/\/chat\/mock-1/);
    expect(mock.firstTurnBodies).toHaveLength(2);
    expect(mock.firstTurnBodies[0].idempotency_key).toBe(mock.firstTurnBodies[1].idempotency_key);
  });

  test("服务端已提交但响应丢失时重试不会创建重复会话", async ({ page }) => {
    await registerAndEnterHome(page);
    const mock = await installMockChatApi(page, { loseResponseOnce: true });
    const input = page.getByLabel("输入消息");
    await input.fill("只应创建一条消息");
    await page.getByRole("button", { name: "发送消息" }).click();
    await expect(page.getByTestId("new-chat-home").getByRole("alert")).toBeVisible();
    await input.press("Enter");
    await page.waitForURL(/\/chat\/mock-1/);

    expect(mock.firstTurnBodies).toHaveLength(2);
    expect(mock.createdUserMessageCount()).toBe(1);
    expect(mock.firstTurnBodies[0].idempotency_key).toBe(mock.firstTurnBodies[1].idempotency_key);
  });

  test("创建中显示状态并防止重复发送与切换模式", async ({ page }) => {
    await registerAndEnterHome(page);
    await installMockChatApi(page, { delayMs: 700 });
    await page.getByLabel("输入消息").fill("你好");
    await page.getByRole("button", { name: "发送消息" }).click();
    await expect(page.getByTestId("composer-sending-status")).toBeVisible();
    await expect(page.getByRole("button", { name: "发送消息" })).toHaveCount(0);
    await expect(page.getByTestId("mode-toggle").getByRole("button").nth(0)).toBeDisabled();
    await expect(page.getByTestId("mode-toggle").getByRole("button").nth(1)).toBeDisabled();
    await page.waitForURL(/\/chat\/mock-1/);
  });

  test("纯键盘可切换模式、换行并发送，桌面视口无水平溢出", async ({ page }) => {
    await registerAndEnterHome(page);
    const mock = await installMockChatApi(page);
    const study = page.getByTestId("mode-toggle").getByRole("button", { name: "学习模式" });
    await study.focus();
    await page.keyboard.press("Enter");
    await expect(study).toHaveAttribute("aria-pressed", "true");

    const input = page.getByLabel("输入消息");
    await input.focus();
    await page.keyboard.type("第一行");
    await page.keyboard.press("Shift+Enter");
    await page.keyboard.type("第二行");
    await expect(input).toHaveValue("第一行\n第二行");
    await input.press("Enter");
    await page.waitForURL(/\/chat\/mock-1/);
    expect(mock.firstTurnBodies[0].content).toBe("第一行\n第二行");

    for (const viewport of [
      { width: 1280, height: 720 },
      { width: 1440, height: 900 },
      { width: 1920, height: 1080 },
    ]) {
      await page.goto("/");
      await page.setViewportSize(viewport);
      const overflowX = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth
      );
      expect(overflowX).toBeLessThanOrEqual(1);
      const main = await page.getByTestId("main-content").boundingBox();
      const modeBox = await page.getByTestId("mode-toggle").boundingBox();
      expect(modeBox!.x + modeBox!.width).toBeLessThanOrEqual(main!.x + main!.width + 1);
      expect(modeBox!.y).toBeGreaterThanOrEqual(main!.y);
    }

    await page.goto("/");
    await expect(page.getByTestId("new-chat-home")).toBeVisible();
    await page.evaluate(() => {
      document.documentElement.style.zoom = "200%";
    });
    const zoomedOverflowX = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth
    );
    expect(zoomedOverflowX).toBeLessThanOrEqual(1);
    const zoomedMain = await page.getByTestId("main-content").boundingBox();
    const zoomedMode = await page.getByTestId("mode-toggle").boundingBox();
    const zoomedComposer = await page.getByTestId("composer").boundingBox();
    expect(zoomedMode!.x + zoomedMode!.width).toBeLessThanOrEqual(zoomedMain!.x + zoomedMain!.width + 1);
    expect(zoomedComposer!.x + zoomedComposer!.width).toBeLessThanOrEqual(
      zoomedMain!.x + zoomedMain!.width + 1
    );
  });
});
