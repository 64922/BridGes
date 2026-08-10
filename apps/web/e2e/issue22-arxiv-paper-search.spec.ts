import { expect, test, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";
import { installRunEventsRoutes, runCreated } from "./helpers/chat-mock";

const NOW = "2026-08-04T00:00:00Z";
const CONVERSATION_ID = "conv-issue22";

function arxivSearch(status: string, overrides: Record<string, unknown> = {}) {
  return {
    status,
    trigger_reason: "你明确要求搜索 arXiv 论文",
    query_summary: "量子纠错 综述",
    papers: [],
    searched_at: NOW,
    error_code: null,
    error_message: null,
    can_retry: false,
    can_cancel: status === "loading",
    ...overrides,
  };
}

function successSearch() {
  return arxivSearch("success", {
    papers: [
      {
        citation_id: "arxiv-1",
        arxiv_id: "2401.12345",
        title: "Quantum error correction: a review",
        authors: ["A. Researcher"],
        published_at: NOW,
        abs_url: "https://arxiv.org/abs/2401.12345",
        pdf_url: "https://arxiv.org/pdf/2401.12345",
        abstract: "A real abstract returned by arXiv.",
        summary_zh: "这篇论文介绍量子纠错的基本方法。",
        relevance_basis: "标题和摘要都匹配确认查询中的量子纠错主题。",
        learning_advice_zh: "建议先读摘要，再核对方法与实验条件。",
      },
    ],
  });
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
    web_search: null,
    arxiv_search: search,
    error_code: null,
    error_message: null,
    duration_ms: role === "assistant" ? 90 : null,
    model_id: role === "assistant" ? "qwen3.7-plus-2026-05-26" : null,
    run_lock_id: role === "assistant" ? "lock-mock" : null,
    created_at: NOW,
    updated_at: NOW,
  };
}

function sseStarted(messageId: string, userMessageId: string) {
  return `event: started\ndata: ${JSON.stringify({
    kind: "started",
    conversation_id: CONVERSATION_ID,
    user_message_id: userMessageId,
    message_id: messageId,
    attempt_number: 1,
    arxiv_search: arxivSearch("loading"),
  })}\n\n`;
}

function sseDone(messageValue: ReturnType<typeof message>) {
  return `event: done\ndata: ${JSON.stringify({
    kind: "done",
    message_id: messageValue.message_id,
    message: messageValue,
  })}\n\n`;
}

async function installMockChatApi(page: Page, initialScenario: "success" | "error" = "success") {
  const state = {
    mode: "companion" as "companion" | "study",
    scenario: initialScenario,
    messages: [message("u-0", "user", "你好", "done")],
  };
  // Issue 02：消息 → 持久化事件流（POST 创建运行后由 events 端点回放）
  const eventStreams = new Map<string, string>();
  const history = () => ({
    conversation_id: CONVERSATION_ID,
    title: "Issue 22 论文搜索",
    mode: state.mode,
    pinned: false,
    project_id: null,
    created_at: NOW,
    updated_at: NOW,
    messages: state.messages,
    mode_events: [],
  });

  await page.route("**/api/chat/conversations", async (route) => {
    if (route.request().method() === "POST") {
      await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify(history()) });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ conversations: [{ ...history(), message_count: state.messages.length }] }),
    });
  });
  await page.route(`**/api/chat/conversations/${CONVERSATION_ID}/mode`, async (route) => {
    state.mode = "study";
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ conversation: history(), event: null }),
    });
  });
  await page.route(`**/api/chat/conversations/${CONVERSATION_ID}`, async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(history()) });
  });
  await page.route(`**/api/chat/conversations/${CONVERSATION_ID}/messages`, async (route) => {
    if (route.request().method() !== "POST") return;
    const user = message("u-1", "user", "帮我找量子纠错综述", "done");
    const search = state.scenario === "error"
      ? arxivSearch("error", {
          error_code: "arxiv_timeout",
          error_message: "arXiv 搜索超时，请重试。",
          can_retry: true,
          can_cancel: false,
        })
      : successSearch();
    const assistant = message(
      "a-1",
      "assistant",
      state.scenario === "error" ? "" : "依据 [arxiv-1] 回答。",
      state.scenario === "error" ? "error" : "done",
      search
    );
    state.messages.push(user, assistant);
    if (state.scenario === "error") {
      eventStreams.set(
        assistant.message_id,
        `${sseStarted(assistant.message_id, user.message_id)}event: error\ndata: ${JSON.stringify({
          kind: "error",
          message_id: assistant.message_id,
          error: { code: "arxiv_timeout", message: "arXiv 搜索超时，请重试。", retryable: true },
          arxiv_search: search,
        })}\n\n`
      );
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(runCreated(`run-${assistant.message_id}`, 1, user, assistant)),
      });
      return;
    }
    eventStreams.set(
      assistant.message_id,
      `${sseStarted(assistant.message_id, user.message_id)}${sseDone(assistant)}`
    );
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(runCreated(`run-${assistant.message_id}`, 1, user, assistant)),
    });
  });
  await page.route(`**/api/chat/conversations/${CONVERSATION_ID}/messages/*/retry`, async (route) => {
    state.scenario = "success";
    const retried = message("a-2", "assistant", "依据 [arxiv-1] 回答。", "done", successSearch());
    state.messages.push(retried);
    eventStreams.set(
      retried.message_id,
      `${sseStarted(retried.message_id, "u-1")}${sseDone(retried)}`
    );
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(runCreated(`run-${retried.message_id}`, 1, state.messages[0], retried)),
    });
  });

  // Issue 02：订阅运行事件（回放已持久化事件；运行终态后结束）
  installRunEventsRoutes(page, eventStreams, CONVERSATION_ID);
}

async function registerAndOpen(page: Page): Promise<void> {
  const credentials = uniqueCredentials("issue22");
  await signUp(page, credentials.username, credentials.qqEmail, "Passw0rd123!");
  await page.goto(`/chat/${CONVERSATION_ID}`);
  await expect(page.getByTestId("composer")).toBeVisible();
}

test.describe("Issue 22：自然语言 arXiv 论文搜索", () => {
  test("已有会话移除手动入口，直接使用自然语言展示真实来源", async ({ page }) => {
    await installMockChatApi(page);
    await registerAndOpen(page);

    await expect(page.getByTestId("mode-toggle")).toHaveCount(0);
    await expect(page.getByRole("button", { name: /更多功能/ })).toHaveCount(0);

    await page.getByTestId("composer").getByLabel("输入消息").fill("帮我找量子纠错综述");
    await page.getByTestId("composer").getByRole("button", { name: "发送消息" }).click();

    const card = page.getByTestId("arxiv-search-card");
    await expect(card).toContainText("已确认主题：量子纠错 综述");
    await expect(card).toContainText("Quantum error correction: a review");
    await expect(card).toContainText("这篇论文介绍量子纠错的基本方法");
    await expect(card.getByTestId("arxiv-abs-arxiv-1")).toHaveAttribute("href", "https://arxiv.org/abs/2401.12345");
    await expect(card.getByTestId("arxiv-pdf-arxiv-1")).toHaveAttribute("href", "https://arxiv.org/pdf/2401.12345");

    await card.getByTestId("arxiv-citation-arxiv-1").locator("summary").first().click();
    await expect(card).toContainText("A real abstract returned by arXiv.");
    await expect(card).toContainText("建议先读摘要，再核对方法与实验条件");

    await page.reload();
    await expect(page.getByTestId("arxiv-search-card")).toContainText("Quantum error correction: a review");
  });

  test("搜索错误显示中文恢复入口，重试后恢复真实论文结果", async ({ page }) => {
    await installMockChatApi(page, "error");
    await registerAndOpen(page);
    await page.getByTestId("composer").getByLabel("输入消息").fill("帮我找量子纠错综述");
    await page.getByTestId("composer").getByRole("button", { name: "发送消息" }).click();

    await expect(page.getByTestId("arxiv-search-card-error")).toContainText("arXiv 搜索超时，请重试");
    await page.getByTestId("arxiv-search-retry").click();
    await expect(page.getByTestId("arxiv-search-card")).toContainText("Quantum error correction: a review");
  });
});
