import { expect, test, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

/**
 * Issue 11 — 交付持久化真实 Qwen 流式聊天纵向切片。
 *
 * 场景 A（真实后端）：未配置 Key 时发送被能力预检拦截，展示可操作中文
 * 提示与设置入口，不重复插入用户消息；对话在刷新后仍保留。
 * 场景 B（协议级替身）：mock SSE 流式回答，验证增量正文渲染、停止入口、
 * 重试新建尝试与历史保留。
 *
 * 说明：真实流式回答需要显式测试账户 Key（人工冒烟），自动化 e2e 用
 * 协议级替身验证流式边界——与后端 pytest 的替身策略一致。
 */

interface MockMessage {
  message_id: string;
  conversation_id: string;
  role: "user" | "assistant";
  attempt_number: number;
  status: string;
  content: string;
  error_code: string | null;
  error_message: string | null;
  duration_ms: number | null;
  model_id: string | null;
  run_lock_id: string | null;
  created_at: string;
  updated_at: string;
}

const NOW = "2026-08-03T00:00:00Z";

function mockUser(id: string, content: string): MockMessage {
  return {
    message_id: id,
    conversation_id: "mock-1",
    role: "user",
    attempt_number: 1,
    status: "done",
    content,
    error_code: null,
    error_message: null,
    duration_ms: null,
    model_id: null,
    run_lock_id: null,
    created_at: NOW,
    updated_at: NOW,
  };
}

function mockAssistant(id: string, attempt: number, content: string, status: string): MockMessage {
  return {
    message_id: id,
    conversation_id: "mock-1",
    role: "assistant",
    attempt_number: attempt,
    status,
    content,
    error_code: null,
    error_message: null,
    duration_ms: 90,
    model_id: "qwen3.7-plus-2026-05-26",
    run_lock_id: "lock-mock",
    created_at: NOW,
    updated_at: NOW,
  };
}

const sseStarted = (messageId: string, attempt: number, userMessageId: string) =>
  `event: started\ndata: ${JSON.stringify({
    kind: "started",
    conversation_id: "mock-1",
    user_message_id: userMessageId,
    message_id: messageId,
    attempt_number: attempt,
  })}\n\n`;

const sseDelta = (messageId: string, delta: string) =>
  `event: delta\ndata: ${JSON.stringify({ kind: "delta", message_id: messageId, delta })}\n\n`;

const sseDone = (message: MockMessage) =>
  `event: done\ndata: ${JSON.stringify({ kind: "done", message_id: message.message_id, message })}\n\n`;

/**
 * 有状态的聊天 API 替身：发送/停止/重试都会更新会话历史，
 * 后续 GET 返回新状态（与真实服务端行为一致）。
 */
function installMockChatApi(page: Page) {
  const state = {
    messages: [mockUser("u-1", "你好"), mockAssistant("a-1", 1, "这是已保存的回答。", "done")],
    counter: 2,
  };
  // Issue 02：消息 → 持久化事件流（POST 创建运行后由 events 端点回放）
  const eventStreams = new Map<string, string>();

  const history = () => ({
    conversation_id: "mock-1",
    title: "测试对话",
    mode: "companion",
    created_at: NOW,
    updated_at: NOW,
    messages: state.messages,
  });

  const findAssistant = (messageId: string) =>
    state.messages.find((message) => message.message_id === messageId);

  const runCreated = (userMessage: MockMessage, assistantMessage: MockMessage) => ({
    run_id: `run-${assistantMessage.message_id}`,
    cursor: 1,
    user_message: userMessage,
    assistant_message: assistantMessage,
  });

  return {
    async install(): Promise<void> {
      await page.route("**/api/chat/conversations", async (route) => {
        if (route.request().method() === "POST") {
          await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify(history()) });
          return;
        }
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            conversations: [
              {
                conversation_id: "mock-1",
                title: "测试对话",
                mode: "companion",
                message_count: state.messages.length,
                created_at: NOW,
                updated_at: NOW,
              },
            ],
          }),
        });
      });

      await page.route("**/api/chat/conversations/mock-1", async (route) => {
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(history()) });
      });

      // 发送：创建响应（消息已落库、运行已入队）；事件由 events 端点回放
      await page.route("**/api/chat/conversations/mock-1/messages", async (route) => {
        if (route.request().method() !== "POST") return;
        const body = JSON.parse(route.request().postData() ?? "{}");
        state.counter += 1;
        const userMessage = mockUser(`u-${state.counter}`, body.content);
        const assistantMessage = mockAssistant(`a-${state.counter}`, 1, "", "streaming");
        state.messages.push(userMessage, assistantMessage);
        const stream =
          body.content === "不要结束"
            ? `${sseStarted(assistantMessage.message_id, 1, userMessage.message_id)}${sseDelta(
                assistantMessage.message_id,
                "部分"
              )}`
            : `${sseStarted(assistantMessage.message_id, 1, userMessage.message_id)}${sseDelta(
                assistantMessage.message_id,
                "正在"
              )}${sseDelta(assistantMessage.message_id, "生成")}${sseDone(
                { ...assistantMessage, content: "正在生成", status: "done" }
              )}`;
        eventStreams.set(assistantMessage.message_id, stream);
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(runCreated(userMessage, assistantMessage)),
        });
      });

      // Issue 02：订阅运行事件（回放持久化事件；运行终态后结束）
      // 注意：glob 匹配含 query，尾部 ** 命中 ?cursor=N
      await page.route("**/api/chat/conversations/mock-1/messages/*/events**", async (route) => {
        const messageId = route.request().url().split("/messages/")[1].split("/")[0];
        const message = findAssistant(messageId);
        if (!message || message.status !== "streaming") {
          await route.fulfill({ status: 200, contentType: "text/event-stream", body: eventStreams.get(messageId) ?? "" });
          return;
        }
        await route.fulfill({ status: 200, contentType: "text/event-stream", body: eventStreams.get(messageId) ?? "" });
      });

      // 停止：服务端把 streaming 收敛为 stopped（保留已接收正文）
      await page.route("**/api/chat/conversations/mock-1/messages/*/stop", async (route) => {
        const messageId = route.request().url().split("/messages/")[1].split("/")[0];
        const target = findAssistant(messageId);
        const stopped =
          target ??
          mockAssistant(messageId, 1, "部分", "stopped");
        stopped.status = "stopped";
        stopped.content = stopped.content || "部分";
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ message: stopped }),
        });
      });

      // 重试：新建尝试（attempt +1），历史尝试原样保留
      await page.route("**/api/chat/conversations/mock-1/messages/*/retry", async (route) => {
        const messageId = route.request().url().split("/messages/")[1].split("/")[0];
        const previous = findAssistant(messageId) ?? mockAssistant("a-1", 1, "", "error");
        state.counter += 1;
        const attempt = state.counter;
        const retried = mockAssistant(`a-${attempt}`, 2, "重试后的回答", "done");
        if (previous.status !== "done") {
          previous.status = "error";
        }
        state.messages.push(retried);
        eventStreams.set(
          retried.message_id,
          `${sseStarted(retried.message_id, 2, "u-1")}${sseDelta(retried.message_id, "重试后的")}${sseDelta(
            retried.message_id,
            "回答"
          )}${sseDone(retried)}`
        );
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            run_id: `run-${retried.message_id}`,
            cursor: 1,
            user_message: mockUser("u-1", "你好"),
            assistant_message: retried,
          }),
        });
      });
    },
  };
}

async function registerAndEnterHome(page: Page): Promise<void> {
  const credentials = uniqueCredentials("issue11");
  await signUp(page, credentials.username, credentials.qqEmail, "Passw0rd123!");
  await expect(page.getByRole("heading", { name: "有什么可以帮你的？" })).toBeVisible();
}

test.describe("Issue 11 — 持久化流式聊天", () => {
  test("新账户无需配置 Key 直接收到流式回答，刷新不重复插入", async ({ page }) => {
    await registerAndEnterHome(page);

    // 对话能力依赖真实后端：未启用对话存储的实例跳过（reuseExistingServer 场景）
    const chatReady = await page.request.get("/api/chat/conversations");
    if (chatReady.status() === 503) {
      test.skip(true, "当前 API 实例未启用对话存储（BRIDGES_DATABASE_URL），跳过聊天用例。");
    }

    const composer = page.getByTestId("composer");
    await expect(composer).toBeVisible();
    await composer.getByLabel("输入消息").fill("你好，介绍一下你自己");
    await composer.getByRole("button", { name: "发送消息" }).click();

    // GQ-02：新账户无需任何个人 Qwen 配置即可发送，无预检拦截；
    // test 环境由确定性适配器返回回答（唯一放行机制）
    await page.waitForURL(/\/chat\/[^/]+$/);
    const thread = page.getByRole("list", { name: "对话消息" });
    await expect(thread).toContainText("你好，介绍一下你自己");
    await expect(thread).toContainText("本地替身模式");

    // 刷新后对话仍存在（持久化恢复），消息未被重复插入
    await page.reload();
    await expect(thread).toContainText("你好，介绍一下你自己");
    await expect(thread.getByText("你好，介绍一下你自己")).toHaveCount(1);
    // Issue 12：新聊天入口在全局侧栏中
    await expect(
      page.getByTestId("app-sidebar").getByRole("link", { name: "新聊天", exact: true })
    ).toBeVisible();
  });

  test("流式回答增量渲染，可停止且保留已接收正文", async ({ page }) => {
    await registerAndEnterHome(page);
    await installMockChatApi(page).install();

    await page.goto("/chat/mock-1");
    // 历史消息完整呈现（重启恢复场景的 UI 面）
    const thread = page.getByRole("list", { name: "对话消息" });
    await expect(thread).toContainText("你好");
    await expect(thread).toContainText("这是已保存的回答。");

    // 发送 → 增量正文渲染 + 生成状态 + 停止入口
    const composer = page.getByTestId("composer");
    await composer.getByLabel("输入消息").fill("不要结束");
    await composer.getByRole("button", { name: "发送消息" }).click();
    await expect(page.getByRole("button", { name: "停止生成" })).toBeVisible();
    await expect(thread).toContainText("部分");

    // 停止：服务端收敛为 stopped，已接收正文保留
    await page.getByRole("button", { name: "停止生成" }).click();
    await expect(thread).toContainText("部分");
    await expect(composer.getByLabel("输入消息")).toBeEnabled();
  });

  test("重试创建新的助手尝试并保留失败历史", async ({ page }) => {
    await registerAndEnterHome(page);
    await installMockChatApi(page).install();

    await page.goto("/chat/mock-1");
    const thread = page.getByRole("list", { name: "对话消息" });
    await expect(thread).toContainText("这是已保存的回答。");

    await page
      .getByRole("toolbar", { name: "消息操作" })
      .first()
      .getByRole("button", { name: "重试" })
      .click();

    // 重试流：新尝试正文呈现，历史尝试折叠保留
    await expect(thread).toContainText("重试后的回答");
    await expect(page.getByText("此问题的前 1 次尝试")).toBeVisible();
  });

  test("加载失败状态可恢复，键盘可聚焦输入区并发送", async ({ page }) => {
    await registerAndEnterHome(page);
    await installMockChatApi(page).install();

    // 加载失败状态（替身对未知对话返回 404）：展示错误与重新加载入口
    await page.goto("/chat/unknown-id");
    await expect(page.getByTestId("state-error")).toBeVisible();
    await expect(page.getByRole("button", { name: "重新加载" })).toBeVisible();

    // 键盘路径：聚焦输入框 → Enter 发送 → 生成中停止入口
    await page.goto("/chat/mock-1");
    const composer = page.getByTestId("composer");
    await composer.getByLabel("输入消息").focus();
    await page.keyboard.type("不要结束");
    await page.keyboard.press("Enter");
    await expect(page.getByRole("button", { name: "停止生成" })).toBeVisible();
  });
});
