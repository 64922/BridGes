import { expect, test, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

/**
 * Issue 06 — 端到端时延预算、阶段埋点和有界降级（前端阶段展示）。
 *
 * 场景 A（阶段行）：协议级替身回放带 stage 事件的流，验证发送后用户
 * 消息快速出现（500ms 内）、阶段行按统一阶段显示真实文案（检索本地
 * 资料/搜索公开来源/生成回答中…），替代笼统"思考中"。
 * 场景 B（终态收敛）：完整流结束后阶段行消失，权威历史接管。
 *
 * 说明：与 issue11 同一协议级替身策略（真实流式回答需要人工冒烟）；
 * 阶段行文案由 MessageList 的 STAGE_LABEL 映射驱动。
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

const NOW = "2026-08-08T00:00:00Z";

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
    duration_ms: 120,
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

// Issue 06：统一阶段事件（脱敏：仅阶段枚举/状态，无正文）
const sseStage = (messageId: string, stage: string, status: string) =>
  `event: stage\ndata: ${JSON.stringify({
    kind: "stage",
    message_id: messageId,
    stage,
    status,
  })}\n\n`;

const sseDelta = (messageId: string, delta: string) =>
  `event: delta\ndata: ${JSON.stringify({ kind: "delta", message_id: messageId, delta })}\n\n`;

const sseDone = (message: MockMessage) =>
  `event: done\ndata: ${JSON.stringify({ kind: "done", message_id: message.message_id, message })}\n\n`;

function installMockChatApi(page: Page) {
  const state = {
    messages: [mockUser("u-1", "你好"), mockAssistant("a-1", 1, "这是已保存的回答。", "done")],
    counter: 2,
  };
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

      await page.route("**/api/chat/conversations/mock-1/messages", async (route) => {
        if (route.request().method() !== "POST") return;
        const body = JSON.parse(route.request().postData() ?? "{}");
        state.counter += 1;
        const userMessage = mockUser(`u-${state.counter}`, body.content);
        const assistantMessage = mockAssistant(`a-${state.counter}`, 1, "", "streaming");
        state.messages.push(userMessage, assistantMessage);
        const stream =
          body.content === "不要结束"
            ? // 阶段流：检索 → 搜索 → 生成（不结束，用于阶段行断言）
              `${sseStarted(assistantMessage.message_id, 1, userMessage.message_id)}${sseStage(
                assistantMessage.message_id,
                "local_retrieval",
                "active"
              )}${sseStage(assistantMessage.message_id, "local_retrieval", "done")}${sseStage(
                assistantMessage.message_id,
                "public_search",
                "active"
              )}${sseStage(assistantMessage.message_id, "public_search", "done")}${sseStage(
                assistantMessage.message_id,
                "model_generation",
                "active"
              )}${sseDelta(assistantMessage.message_id, "正在")}`
            : // 完整流：阶段顺序 + 正文 + 终态（阶段行随 done 消失）
              `${sseStarted(assistantMessage.message_id, 1, userMessage.message_id)}${sseStage(
                assistantMessage.message_id,
                "local_retrieval",
                "active"
              )}${sseStage(assistantMessage.message_id, "local_retrieval", "done")}${sseStage(
                assistantMessage.message_id,
                "model_generation",
                "active"
              )}${sseDelta(assistantMessage.message_id, "正在")}${sseStage(
                assistantMessage.message_id,
                "quality_check",
                "active"
              )}${sseStage(assistantMessage.message_id, "finalizing", "active")}${sseDone({
                ...assistantMessage,
                content: "正在生成回答",
                status: "done",
              })}`;
        eventStreams.set(assistantMessage.message_id, stream);
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(runCreated(userMessage, assistantMessage)),
        });
      });

      await page.route("**/api/chat/conversations/mock-1/messages/*/events**", async (route) => {
        const messageId = route.request().url().split("/messages/")[1].split("/")[0];
        await route.fulfill({
          status: 200,
          contentType: "text/event-stream",
          body: eventStreams.get(messageId) ?? "",
        });
      });
    },
  };
}

async function registerAndEnterHome(page: Page): Promise<void> {
  const credentials = uniqueCredentials("issue06");
  await signUp(page, credentials.username, credentials.qqEmail, "Passw0rd123!");
  await expect(page.getByRole("heading", { name: "有什么可以帮你的？" })).toBeVisible();
}

test.describe("Issue 06 — 阶段埋点与有界降级（前端展示）", () => {
  test("发送后快速出现用户消息，阶段行显示真实阶段文案", async ({ page }) => {
    await installMockChatApi(page).install();
    await registerAndEnterHome(page);

    const chatReady = await page.request.get("/api/chat/conversations");
    if (chatReady.status() === 503) {
      test.skip(true, "当前 API 实例未启用对话存储，跳过聊天用例。");
    }

    await page.goto("/chat/mock-1");
    const thread = page.getByRole("list", { name: "对话消息" });
    await expect(thread).toContainText("这是已保存的回答。");

    const composer = page.getByTestId("composer");
    await composer.getByLabel("输入消息").fill("不要结束");
    await composer.getByRole("button", { name: "发送消息" }).click();

    // 验收 1：发送后 500ms 内出现用户消息（创建响应已落库，前端立即渲染）
    await expect(thread.getByText("不要结束").first()).toBeVisible({ timeout: 500 });

    // 验收 2：阶段行显示真实阶段（mock 流停留在模型生成阶段），
    // 而非笼统"思考中"；阶段顺序由后端 pytest 的阶段序列断言覆盖。
    await expect(thread.getByText("生成回答中…").first()).toBeVisible({ timeout: 5000 });
    // 阶段行不再显示笼统文案
    await expect(thread.getByText("正在生成回答…").first()).toHaveCount(0);
  });

  test("完整流结束后阶段行消失，权威历史接管终态", async ({ page }) => {
    await installMockChatApi(page).install();
    await registerAndEnterHome(page);

    const chatReady = await page.request.get("/api/chat/conversations");
    if (chatReady.status() === 503) {
      test.skip(true, "当前 API 实例未启用对话存储，跳过聊天用例。");
    }

    await page.goto("/chat/mock-1");
    const thread = page.getByRole("list", { name: "对话消息" });
    const composer = page.getByTestId("composer");
    await composer.getByLabel("输入消息").fill("你好");
    await composer.getByRole("button", { name: "发送消息" }).click();

    // 完整流（含 done）快速回放：终态由权威历史接管，阶段行随 done 消失
    await expect(thread.getByText("正在生成回答").first()).toBeVisible({ timeout: 5000 });
    await expect(thread.getByText("生成回答中…").first()).toHaveCount(0);
  });
});
