import { expect, test, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";
import { installRunEventsRoutes, runCreated } from "./helpers/chat-mock";

/** Issue 21：公网搜索卡片的真实来源、失败恢复与本轮取消。 */

const NOW = "2026-08-04T00:00:00Z";
const CONVERSATION_ID = "conv-issue21";

function webSearch(status: string, overrides: Record<string, unknown> = {}) {
  return {
    status,
    trigger_reason: "你明确要求联网搜索、问题依赖最新信息",
    query_summary: "量子 计算 最新进展",
    results: [],
    searched_at: NOW,
    error_code: null,
    error_message: null,
    can_retry: false,
    can_cancel: status === "loading",
    ...overrides,
  };
}

function message(
  id: string,
  role: "user" | "assistant",
  content: string,
  status: string,
  search: unknown = null
) {
  return {
    message_id: id,
    conversation_id: CONVERSATION_ID,
    role,
    attempt_number: 1,
    status,
    content,
    attachments: [],
    thinking: null,
    retrieval: null,
    web_search: search,
    error_code: null,
    error_message: null,
    duration_ms: role === "assistant" ? 90 : null,
    model_id: role === "assistant" ? "qwen3.7-plus-2026-05-26" : null,
    run_lock_id: role === "assistant" ? "lock-mock" : null,
    created_at: NOW,
    updated_at: NOW,
  };
}

function successSearch() {
  return webSearch("success", {
    results: [
      {
        result_id: "web-1",
        title: "量子计算公开报道",
        site: "example.com",
        url: "https://example.com/quantum",
        snippet: "来自真实响应的公开摘要。",
        accessed_at: NOW,
      },
    ],
  });
}

function sseStarted(messageId: string, userMessageId: string, search: unknown) {
  return `event: started\ndata: ${JSON.stringify({
    kind: "started",
    conversation_id: CONVERSATION_ID,
    user_message_id: userMessageId,
    message_id: messageId,
    attempt_number: 1,
    web_search: search,
  })}\n\n`;
}

function sseDone(messageValue: ReturnType<typeof message>) {
  return `event: done\ndata: ${JSON.stringify({
    kind: "done",
    message_id: messageValue.message_id,
    message: messageValue,
  })}\n\n`;
}

function installMockChatApi(page: Page) {
  const state = {
    messages: [message("u-0", "user", "你好", "done")],
    counter: 0,
    nextScenario: "success" as "success" | "error" | "cancel",
  };

  const history = () => ({
    conversation_id: CONVERSATION_ID,
    title: "Issue 21 对话",
    mode: "companion",
    pinned: false,
    project_id: null,
    created_at: NOW,
    updated_at: NOW,
    messages: state.messages,
    mode_events: [],
  });
  // Issue 02：消息 → 持久化事件流（POST 创建运行后由 events 端点回放）
  const eventStreams = new Map<string, string>();

  return {
    state,
    async install(): Promise<void> {
      await page.route("**/api/chat/conversations", async (route) => {
        if (route.request().method() === "POST") {
          await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify(history()) });
          return;
        }
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ conversations: [{ conversation_id: CONVERSATION_ID, title: "Issue 21 对话", mode: "companion", message_count: state.messages.length, created_at: NOW, updated_at: NOW }] }),
        });
      });
      await page.route(`**/api/chat/conversations/${CONVERSATION_ID}`, async (route) => {
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(history()) });
      });
      await page.route(`**/api/chat/conversations/${CONVERSATION_ID}/messages`, async (route) => {
        if (route.request().method() !== "POST") return;
        state.counter += 1;
        const user = message(`u-${state.counter}`, "user", "请联网核实量子计算最新进展", "done");
        const scenario = state.nextScenario;
        const search = scenario === "error"
          ? webSearch("error", { error_code: "web_search_timeout", error_message: "联网搜索超时，请重试。", can_retry: true })
          : scenario === "cancel" ? webSearch("loading") : successSearch();
        const assistant = message(`a-${state.counter}`, "assistant", scenario === "success" ? "基于来源回答。" : "", scenario === "success" ? "done" : "streaming", search);
        state.messages.push(user, assistant);
        if (scenario === "cancel") {
          eventStreams.set(
            assistant.message_id,
            sseStarted(assistant.message_id, user.message_id, webSearch("loading"))
          );
        } else if (scenario === "error") {
          eventStreams.set(
            assistant.message_id,
            `${sseStarted(assistant.message_id, user.message_id, webSearch("loading"))}event: error\ndata: ${JSON.stringify({ kind: "error", message_id: assistant.message_id, error: { code: "web_search_timeout", message: "联网搜索超时，请重试。", retryable: true }, thinking: null, duration_ms: 10, web_search: search })}\n\n`
          );
        } else {
          eventStreams.set(
            assistant.message_id,
            `${sseStarted(assistant.message_id, user.message_id, webSearch("loading"))}${sseDone(assistant)}`
          );
        }
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(runCreated(`run-${assistant.message_id}`, 1, user, assistant)),
        });
      });
      // Issue 02：订阅运行事件（回放已持久化事件；运行终态后结束）
      installRunEventsRoutes(page, eventStreams, CONVERSATION_ID);
      await page.route(`**/api/chat/conversations/${CONVERSATION_ID}/messages/*/retry`, async (route) => {
        state.counter += 1;
        const retried = message(`a-${state.counter}`, "assistant", "重试后的来源回答。", "done", successSearch());
        state.messages.push(retried);
        eventStreams.set(
          retried.message_id,
          `${sseStarted(retried.message_id, "u-0", webSearch("loading"))}${sseDone(retried)}`
        );
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(runCreated(`run-${retried.message_id}`, 1, state.messages[0], retried)),
        });
      });
      await page.route(`**/api/chat/conversations/${CONVERSATION_ID}/messages/*/stop`, async (route) => {
        const target = state.messages.at(-1);
        if (target?.role === "assistant") {
          target.status = "stopped";
          target.web_search = webSearch("cancelled", { error_message: "已取消本轮联网搜索。", can_cancel: false });
        }
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ message: target }) });
      });
    },
  };
}

async function registerAndOpen(page: Page): Promise<void> {
  const credentials = uniqueCredentials("issue21");
  await signUp(page, credentials.username, credentials.qqEmail, "Passw0rd123!");
  await page.goto(`/chat/${CONVERSATION_ID}`);
  await expect(page.getByTestId("composer")).toBeVisible();
}

test.describe("Issue 21 — DuckDuckGo 隐私搜索", () => {
  test("展示触发原因、真实 URL、站点与访问时间", async ({ page }) => {
    const mock = installMockChatApi(page);
    await mock.install();
    await registerAndOpen(page);
    await page.getByLabel("输入消息").fill("请联网核实量子计算最新进展");
    await page.getByRole("button", { name: "发送消息" }).click();
    await expect(page.getByTestId("web-search-card")).toBeVisible();
    await expect(page.getByTestId("web-search-card")).toContainText("你明确要求联网搜索");
    await expect(page.getByTestId("web-search-card")).toContainText("example.com");
    await expect(page.getByTestId("web-search-result-web-1")).toHaveAttribute("href", "https://example.com/quantum");
  });

  test("搜索失败显示可操作重试，且重试不新增用户消息", async ({ page }) => {
    const mock = installMockChatApi(page);
    mock.state.nextScenario = "error";
    await mock.install();
    await registerAndOpen(page);
    await page.getByLabel("输入消息").fill("请联网核实量子计算最新进展");
    await page.getByRole("button", { name: "发送消息" }).click();
    await expect(page.getByTestId("web-search-card-error")).toContainText("联网搜索超时");
    await page.getByTestId("web-search-retry").click();
    await expect(page.getByRole("list", { name: "对话消息" })).toContainText("重试后的来源回答");
    await expect(page.getByRole("list", { name: "对话消息" }).getByText("请联网核实量子计算最新进展", { exact: true })).toHaveCount(1);
  });

  test("取消本轮搜索后离开 searching 状态", async ({ page }) => {
    const mock = installMockChatApi(page);
    mock.state.nextScenario = "cancel";
    await mock.install();
    await registerAndOpen(page);
    await page.getByLabel("输入消息").fill("请联网核实量子计算最新进展");
    await page.getByRole("button", { name: "发送消息" }).click();
    await expect(page.getByTestId("web-search-cancel")).toBeVisible();
    await page.getByTestId("web-search-cancel").click();
    await expect(page.getByTestId("web-search-card-cancelled")).toContainText("已取消本轮联网搜索");
  });
});
