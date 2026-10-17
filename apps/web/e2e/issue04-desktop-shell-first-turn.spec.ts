import { expect, test, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

/**
 * 对话改版 Issue 04：新桌面对话外壳与真实首轮入口。
 *
 * 覆盖静态欢迎、输入区模式选择、首轮成功/失败、固定模式、最近会话标题、
 * 轻量建议只预填真实入口，以及三个桌面视口的阅读列和输入区布局。
 * 边界与恢复用例使用协议替身；另有一条零 mock 的真实链路用例，验证两种
 * 模式经正式认证、原子首轮、持久化、会话路由与刷新后的一致性。
 */

const CONVERSATION_ID = "conv-issue04";
const TITLE = "首轮创建的会话";
const NOW = "2026-10-17T00:00:00Z";
const PASSWORD = "correct-horse-issue04";

const VIEWPORTS = [
  { width: 1280, height: 720, name: "1280x720" },
  { width: 1440, height: 900, name: "1440x900" },
  { width: 1920, height: 1080, name: "1920x1080" },
] as const;

type Mode = "companion" | "study";

function message(id: string, role: "user" | "assistant", content: string) {
  return {
    message_id: id,
    conversation_id: CONVERSATION_ID,
    role,
    attempt_number: 1,
    status: "done",
    content,
    error_code: null,
    error_message: null,
    duration_ms: role === "assistant" ? 90 : null,
    model_id: role === "assistant" ? "mock-model" : null,
    run_lock_id: role === "assistant" ? "mock-lock" : null,
    created_at: NOW,
    updated_at: NOW,
  };
}

function history(mode: Mode, messages: ReturnType<typeof message>[]) {
  return {
    conversation_id: CONVERSATION_ID,
    title: TITLE,
    mode,
    mode_locked: true,
    pinned: false,
    created_at: NOW,
    updated_at: NOW,
    messages,
    mode_events: [],
  };
}

async function installMockChatApi(
  page: Page,
  options: { failOnce?: boolean; longHistory?: boolean } = {}
): Promise<{ firstTurnBodies: Array<Record<string, unknown>> }> {
  const firstTurnBodies: Array<Record<string, unknown>> = [];
  let failOnce = options.failOnce ?? false;
  let mode: Mode = "companion";
  let messages: ReturnType<typeof message>[] = [];
  let sequence = 0;
  let conversations: Array<Record<string, unknown>> = [];

  await page.route("**/api/chat/conversations", async (route) => {
    if (route.request().method() === "POST") {
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify(history(mode, messages)),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ conversations }),
    });
  });

  await page.route("**/api/chat/first-turn", async (route) => {
    const body = (route.request().postDataJSON() ?? {}) as Record<string, unknown>;
    firstTurnBodies.push(body);
    if (failOnce) {
      failOnce = false;
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({
          detail: {
            error: "chat_store_unavailable",
            message: "模拟首轮失败，请重试。",
          },
        }),
      });
      return;
    }

    mode = body.mode === "study" ? "study" : "companion";
    sequence += 1;
    const user = message(`u-${sequence}`, "user", String(body.content ?? ""));
    const assistant = message(`a-${sequence}`, "assistant", "这是替身生成的首轮回答。");
    messages = options.longHistory
      ? Array.from({ length: 24 }, (_, index) =>
          index % 2 === 0
            ? message(`u-long-${index}`, "user", `长对话第 ${index + 1} 条消息。`)
            : message(
                `a-long-${index}`,
                "assistant",
                `长对话第 ${index + 1} 条回答，用于验证消息区独立滚动。`
              )
        )
      : [user, assistant];
    conversations = [
      {
        conversation_id: CONVERSATION_ID,
        title: TITLE,
        mode,
        mode_locked: true,
        pinned: false,
        created_at: NOW,
        updated_at: NOW,
      },
    ];
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({
        conversation: history(mode, messages),
        run_id: `run-${assistant.message_id}`,
        cursor: 1,
        user_message: user,
        assistant_message: assistant,
        idempotent_replay: false,
      }),
    });
  });

  await page.route(`**/api/chat/conversations/${CONVERSATION_ID}`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(history(mode, messages)),
    });
  });

  return { firstTurnBodies };
}

async function register(page: Page, prefix: string): Promise<void> {
  const credentials = uniqueCredentials(prefix);
  await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);
  await expect(page.getByTestId("new-chat-home")).toBeVisible();
}

async function sendFirstTurn(page: Page, text: string): Promise<void> {
  await page.getByLabel("输入消息").fill(text);
  await page.getByRole("button", { name: "发送消息" }).click();
  await page.waitForURL(new RegExp(`/chat/${CONVERSATION_ID}$`));
}

test.describe("Issue 04 — 新对话外壳与首轮", () => {
  test("静态欢迎替代轮换名言，模式入口在输入区内，建议只预填真实入口", async ({ page }) => {
    await register(page, "i04-shell");

    await expect(page.getByRole("heading", { name: "今天，想弄懂什么？" })).toBeVisible();
    await expect(page.getByText("从一个好奇开始", { exact: true })).toBeVisible();
    await expect(page.getByTestId("empty-quote")).toHaveCount(0);
    await expect(page.getByTestId("suggestion-cards")).toHaveCount(0);
    await expect(page.getByText("原型体验", { exact: true })).toHaveCount(0);
    await expect(page.getByText("交互原型", { exact: true })).toHaveCount(0);

    const composer = page.getByTestId("composer");
    const mode = composer.getByTestId("mode-toggle");
    await expect(mode).toBeVisible();
    await expect(mode.getByRole("button", { name: "日常陪伴" })).toHaveAttribute(
      "aria-pressed",
      "true"
    );
    await expect(mode.getByRole("button", { name: "学习模式" })).toBeEnabled();

    const input = page.getByLabel("输入消息");
    await page.getByRole("button", { name: "找几篇入门论文" }).click();
    await expect(composer.getByTestId("composer-module-chip")).toContainText("论文搜索");
    await expect(input).toHaveValue(/帮我找几篇入门论文/);

    // 再次点击已选中的日常模式不清除已选模块。
    await mode.getByRole("button", { name: "日常陪伴" }).click();
    await expect(composer.getByTestId("composer-module-chip")).toContainText("论文搜索");

    // 已有草稿时预填以换行分隔，不把建议与草稿粘成一句。
    await input.fill("我已有的问题");
    await page.getByRole("button", { name: "找几篇入门论文" }).click();
    await expect(input).toHaveValue("帮我找几篇入门论文\n我已有的问题");

    await input.fill("");
    await page.getByRole("button", { name: "把一个概念讲明白" }).click();
    await expect(mode.getByRole("button", { name: "学习模式" })).toHaveAttribute(
      "aria-pressed",
      "true"
    );
    await expect(composer.getByTestId("composer-module-chip")).toHaveCount(0);
    await expect(input).toHaveValue(/把一个概念讲明白/);

    await input.fill("");
    await page.getByRole("button", { name: "看看校园里的新消息" }).click();
    await expect(composer.getByTestId("composer-module-chip")).toContainText("贴吧信息搜集");
    await expect(input).toHaveValue(/华东交通大学吧最近有什么新消息？/);
  });

  test("学习模式首轮锁定后刷新仍显示固定模式，最近会话出现且标题正确", async ({ page }) => {
    await register(page, "i04-study");
    const mock = await installMockChatApi(page);
    await expect(page.getByTestId("recent-conversations")).toContainText("还没有对话记录");

    await page.getByTestId("mode-toggle").getByRole("button", { name: "学习模式" }).click();
    await sendFirstTurn(page, "学习模式下的第一条消息");

    expect(mock.firstTurnBodies).toHaveLength(1);
    expect(mock.firstTurnBodies[0].mode).toBe("study");

    const header = page.getByTestId("chat-header");
    await expect(header.getByRole("heading", { level: 1 })).toHaveText(TITLE);
    await expect(page.getByTestId("conversation-mode")).toContainText("学习模式");
    await expect(page.getByTestId("mode-toggle")).toHaveCount(0);
    await expect(page.getByText("这是替身生成的首轮回答。")).toBeVisible();
    await expect(
      page.getByTestId("app-sidebar").getByText(TITLE, { exact: true })
    ).toBeVisible();

    await page.reload();
    await expect(page.getByTestId("conversation-mode")).toContainText("学习模式");
    await expect(page.getByTestId("mode-toggle")).toHaveCount(0);
    await expect(page.getByTestId("chat-header").getByRole("heading", { level: 1 })).toHaveText(
      TITLE
    );
  });

  test("首轮失败保留草稿、附件与模式，反馈具体且可直接重试", async ({ page }) => {
    await register(page, "i04-failure");
    const mock = await installMockChatApi(page, { failOnce: true });

    const draft = {
      object_id: "draft-issue04",
      original_filename: "失败保留.png",
      media_type: "image/png",
      content_length: 3,
      content_hash: "hash",
      ingestion_status: "none",
      ingestion_error: null,
      created_at: NOW,
      updated_at: NOW,
    };
    let uploaded = false;
    await page.route("**/api/chat/attachment-drafts", async (route) => {
      if (route.request().method() === "POST") {
        uploaded = true;
        await route.fulfill({
          status: 201,
          contentType: "application/json",
          body: JSON.stringify(draft),
        });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(uploaded ? [draft] : []),
      });
    });
    await page.getByTestId("composer-file-input").evaluate((element) => {
      const input = element as HTMLInputElement;
      const dataTransfer = new DataTransfer();
      dataTransfer.items.add(
        new File([new Uint8Array([1, 2, 3])], "失败保留.png", { type: "image/png" })
      );
      Object.defineProperty(input, "files", {
        configurable: true,
        value: dataTransfer.files,
      });
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await expect(page.getByTestId("composer-attachments")).toContainText("失败保留.png");

    const input = page.getByLabel("输入消息");
    await input.fill("失败后应保留的草稿");
    await page.getByRole("button", { name: "发送消息" }).click();

    await expect(
      page.getByTestId("new-chat-home").getByRole("alert")
    ).toContainText("模拟首轮失败，请重试。");
    await expect(page.getByText("草稿与附件已保留，可直接点击「发送」重试。")).toBeVisible();
    await expect(input).toHaveValue("失败后应保留的草稿");
    await expect(page.getByTestId("composer-attachments")).toContainText("失败保留.png");
    await expect(input).toBeFocused();
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByTestId("mode-toggle")).toBeVisible();

    await page.getByRole("button", { name: "发送消息" }).click();
    await page.waitForURL(new RegExp(`/chat/${CONVERSATION_ID}$`));

    expect(mock.firstTurnBodies).toHaveLength(2);
    expect(mock.firstTurnBodies[0].idempotency_key).toBe(
      mock.firstTurnBodies[1].idempotency_key
    );
    expect(mock.firstTurnBodies[0].attachment_ids).toEqual(["draft-issue04"]);
    expect(mock.firstTurnBodies[1].attachment_ids).toEqual(["draft-issue04"]);
    await expect(page.getByTestId("conversation-mode")).toContainText("日常陪伴");
  });

  test("三个桌面视口下首轮前后都保持限宽阅读列与可见输入区", async ({ page }, testInfo) => {
    await register(page, "i04-layout");
    await installMockChatApi(page);

    for (const viewport of VIEWPORTS) {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await page.goto("/");
      await expect(page.getByTestId("new-chat-home")).toBeVisible();

      const home = await page.evaluate(() => {
        const doc = document.documentElement;
        const composer = document.querySelector<HTMLElement>('[data-testid="composer"]')!;
        const title = document.querySelector<HTMLElement>('[data-testid="new-chat-home"] h2')!;
        const header = document.querySelector<HTMLElement>('[data-testid="chat-header"]')!;
        const composerBox = composer.getBoundingClientRect();
        const titleBox = title.getBoundingClientRect();
        return {
          horizontalOverflow: doc.scrollWidth - doc.clientWidth,
          composerBottom: composerBox.bottom,
          composerWidth: composerBox.width,
          composerCenter: (composerBox.left + composerBox.right) / 2,
          titleCenter: (titleBox.left + titleBox.right) / 2,
          headerTop: header.getBoundingClientRect().top,
          viewportHeight: window.innerHeight,
          viewportWidth: window.innerWidth,
        };
      });
      expect(home.horizontalOverflow).toBeLessThanOrEqual(1);
      expect(home.composerBottom).toBeLessThanOrEqual(home.viewportHeight + 1);
      expect(home.composerWidth).toBeLessThanOrEqual(48 * 16 + 1);
      expect(Math.abs(home.composerCenter - home.titleCenter)).toBeLessThanOrEqual(1.5);
      expect(home.headerTop).toBeGreaterThanOrEqual(-1);
      await page.screenshot({
        path: testInfo.outputPath(`home-${viewport.name}.png`),
        fullPage: false,
      });

      await sendFirstTurn(page, `布局检查 ${viewport.name}`);
      await expect(page.getByTestId("conversation-mode")).toBeVisible();
      const chat = await page.evaluate(() => {
        const doc = document.documentElement;
        const composer = document.querySelector<HTMLElement>('[data-testid="composer"]')!;
        const header = document.querySelector<HTMLElement>('[data-testid="chat-header"]')!;
        const threadInner = document.querySelector<HTMLElement>(
          '[data-testid="chat-thread"] > div'
        )!;
        const composerBox = composer.getBoundingClientRect();
        const threadInnerBox = threadInner.getBoundingClientRect();
        return {
          horizontalOverflow: doc.scrollWidth - doc.clientWidth,
          composerBottom: composerBox.bottom,
          composerWidth: composerBox.width,
          composerCenter: (composerBox.left + composerBox.right) / 2,
          threadInnerWidth: threadInnerBox.width,
          threadInnerCenter: (threadInnerBox.left + threadInnerBox.right) / 2,
          headerBottom: header.getBoundingClientRect().bottom,
          viewportHeight: window.innerHeight,
        };
      });
      expect(chat.horizontalOverflow).toBeLessThanOrEqual(1);
      expect(chat.composerBottom).toBeLessThanOrEqual(chat.viewportHeight + 1);
      expect(chat.composerWidth).toBeLessThanOrEqual(48 * 16 + 1);
      expect(chat.threadInnerWidth).toBeLessThanOrEqual(48 * 16 + 1);
      expect(Math.abs(chat.composerCenter - chat.threadInnerCenter)).toBeLessThanOrEqual(1.5);
      expect(chat.headerBottom).toBeLessThanOrEqual(viewport.height);
      await page.screenshot({
        path: testInfo.outputPath(`chat-${viewport.name}.png`),
        fullPage: false,
      });

      await page
        .getByTestId("app-sidebar")
        .getByRole("link", { name: "新聊天", exact: true })
        .click();
      await expect(page.getByTestId("new-chat-home")).toBeVisible();
    }
  });

  test("长对话只滚动消息区，输入区保持沉底可见", async ({ page }) => {
    await register(page, "i04-scroll");
    await installMockChatApi(page, { longHistory: true });
    await sendFirstTurn(page, "长对话滚动检查");

    const thread = page.getByTestId("chat-thread");
    await expect(thread).toBeVisible();
    const metrics = await page.evaluate(() => {
      const element = document.querySelector<HTMLElement>('[data-testid="chat-thread"]')!;
      const composer = document.querySelector<HTMLElement>('[data-testid="composer"]')!;
      return {
        overflow: element.scrollHeight - element.clientHeight,
        composerBottom: composer.getBoundingClientRect().bottom,
        viewportHeight: window.innerHeight,
      };
    });
    expect(metrics.overflow).toBeGreaterThan(0);
    expect(metrics.composerBottom).toBeLessThanOrEqual(metrics.viewportHeight + 1);

    const composerBefore = await page.getByTestId("composer").boundingBox();
    await thread.evaluate((element) => {
      element.scrollTop = element.scrollHeight;
    });
    const composerAfter = await page.getByTestId("composer").boundingBox();
    expect(composerAfter!.y).toBeCloseTo(composerBefore!.y, 0);
  });

  test("真实链路：两种模式首轮创建、锁定、刷新与最近会话一致", async ({ page }) => {
    const credentials = uniqueCredentials("i04-real");
    await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);
    await expect(page.getByTestId("new-chat-home")).toBeVisible();

    for (const mode of ["companion", "study"] as const) {
      if (mode === "study") {
        await page
          .getByTestId("app-sidebar")
          .getByRole("link", { name: "新聊天", exact: true })
          .click();
        await expect(page.getByTestId("new-chat-home")).toBeVisible();
        await page
          .getByTestId("mode-toggle")
          .getByRole("button", { name: "学习模式" })
          .click();
      }

      const text = `真实链路${mode === "study" ? "学习" : "日常"}首轮`;
      await page.getByLabel("输入消息").fill(text);
      await page.getByRole("button", { name: "发送消息" }).click();
      await page.waitForURL(/\/chat\/[^/]+$/);
      const conversationPath = new URL(page.url()).pathname;
      const modeLabel = mode === "study" ? "学习模式" : "日常陪伴";

      await expect(page.getByTestId("conversation-mode")).toContainText(modeLabel);
      const headerTitle = page.getByTestId("chat-header").getByRole("heading", { level: 1 });
      await expect(headerTitle).toHaveText(text);
      const sidebarLink = page
        .getByTestId("app-sidebar")
        .locator(`a[href="${conversationPath}"]`);
      await expect(sidebarLink).toBeVisible();
      await expect(sidebarLink).toContainText(text);

      await page.reload();
      await expect(page.getByTestId("conversation-mode")).toContainText(modeLabel);
      await expect(headerTitle).toHaveText(text);
      await expect(page.getByTestId("mode-toggle")).toHaveCount(0);
      await expect(sidebarLink).toBeVisible();
      await expect(sidebarLink).toContainText(text);

      // 离开后从最近会话重新打开，模式与标题仍由权威历史决定。
      await page
        .getByTestId("app-sidebar")
        .getByRole("link", { name: "新聊天", exact: true })
        .click();
      await expect(page.getByTestId("new-chat-home")).toBeVisible();
      await sidebarLink.click();
      await page.waitForURL((url) => url.pathname === conversationPath);
      await expect(page.getByTestId("conversation-mode")).toContainText(modeLabel);
      await expect(headerTitle).toHaveText(text);
      await expect(page.getByTestId("mode-toggle")).toHaveCount(0);
    }

    // 最近会话的既有删除入口仍可用：删除当前学习会话后回到新聊天。
    const lastPath = new URL(page.url()).pathname;
    await page.getByRole("button", { name: "会话操作：真实链路学习首轮" }).click();
    await page.getByRole("menuitem", { name: "删除" }).click();
    const deleteDialog = page.getByRole("dialog", { name: "删除会话？" });
    await expect(deleteDialog).toContainText("无法恢复");
    await deleteDialog.getByRole("button", { name: "确认删除" }).click();
    await page.waitForURL("/");
    await expect(page.getByTestId("new-chat-home")).toBeVisible();
    await expect(
      page.getByTestId("app-sidebar").locator(`a[href="${lastPath}"]`)
    ).toHaveCount(0);
  });
});
