import { expect, test, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

const CONVERSATION_ID = "conv-issue22";
const NOW = "2026-08-10T00:00:00Z";

type ChatMessage = {
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
  retrieval?: unknown;
};

function message(
  id: string,
  role: ChatMessage["role"],
  content: string,
  status = "done",
  retrieval?: unknown
): ChatMessage {
  return {
    message_id: id,
    conversation_id: CONVERSATION_ID,
    role,
    attempt_number: 1,
    status,
    content,
    error_code: null,
    error_message: null,
    duration_ms: role === "assistant" ? 90 : null,
    model_id: role === "assistant" ? "mock-model" : null,
    run_lock_id: role === "assistant" ? "mock-lock" : null,
    created_at: NOW,
    updated_at: NOW,
    retrieval,
  };
}

function sse(messageId: string, userMessageId: string, assistant: ChatMessage): string {
  const started = `event: started\ndata: ${JSON.stringify({
    kind: "started",
    conversation_id: CONVERSATION_ID,
    user_message_id: userMessageId,
    message_id: messageId,
    attempt_number: 1,
  })}\n\n`;
  const delta = `event: delta\ndata: ${JSON.stringify({
    kind: "delta",
    message_id: messageId,
    delta: assistant.content,
  })}\n\n`;
  const done = `event: done\ndata: ${JSON.stringify({
    kind: "done",
    message_id: messageId,
    message: assistant,
  })}\n\n`;
  return `${started}${delta}${done}`;
}

async function installConversationApi(
  page: Page,
  options: { mode: "companion" | "study"; messages: ChatMessage[] }
) {
  const state = {
    messages: options.messages,
    sentBodies: [] as Record<string, unknown>[],
    eventStreams: new Map<string, string>(),
    sequence: 0,
  };
  const history = () => ({
    conversation_id: CONVERSATION_ID,
    title: "Viewport contract",
    mode: options.mode,
    created_at: NOW,
    updated_at: NOW,
    messages: state.messages,
    mode_events: [],
  });

  await page.route(`**/api/chat/conversations/${CONVERSATION_ID}`, async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(history()) });
  });

  await page.route(`**/api/chat/conversations/${CONVERSATION_ID}/messages`, async (route) => {
    if (route.request().method() !== "POST") {
      await route.continue();
      return;
    }
    const body = JSON.parse(route.request().postData() ?? "{}") as Record<string, unknown>;
    state.sentBodies.push(body);
    state.sequence += 1;
    const user = message(`u-${state.sequence}`, "user", String(body.content ?? ""));
    const assistant = message(`a-${state.sequence}`, "assistant", "自然语言能力已完成。", "done");
    state.messages.push(user, assistant);
    state.eventStreams.set(assistant.message_id, sse(assistant.message_id, user.message_id, assistant));
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        run_id: `run-${assistant.message_id}`,
        cursor: 1,
        user_message: user,
        assistant_message: { ...assistant, status: "streaming", content: "" },
      }),
    });
  });

  await page.route(`**/api/chat/conversations/${CONVERSATION_ID}/messages/*/events**`, async (route) => {
    const messageId = route.request().url().split("/messages/")[1].split("/")[0];
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: state.eventStreams.get(messageId) ?? "",
    });
  });

  await page.route("**/api/chat/conversations/*/messages/*/citations/*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        citation: {
          citation_id: "citation-1",
          filename: "thermodynamics.md",
          page_number: 4,
          section_title: "Entropy",
          snippet: "Entropy increases in an isolated system.",
        },
        access_status: "accessible",
        access_message: null,
        download_url: "/api/mock-download",
      }),
    });
  });

  return state;
}

async function register(page: Page): Promise<void> {
  const credentials = uniqueCredentials("issue22");
  await signUp(page, credentials.username, credentials.qqEmail, "Passw0rd123!");
}

test.describe("conversation viewport and natural-language controls", () => {
  test("keeps both conversation modes read-only and removes manual controls", async ({ page }) => {
    await register(page);

    for (const mode of ["companion", "study"] as const) {
      const state = await installConversationApi(page, {
        mode,
        messages: [message("u-1", "user", "hello"), message("a-1", "assistant", "saved answer")],
      });
      await page.goto(`/chat/${CONVERSATION_ID}`);
      await expect(page.getByTestId("conversation-mode")).toBeVisible();
      await expect(page.getByTestId("conversation-mode")).not.toHaveAttribute("role", "button");
      await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
      await expect(page.getByTestId("composer").getByRole("button")).toHaveCount(2);
      await expect(page.getByTestId("composer-source-layers")).toHaveCount(0);
      await expect(page.getByTestId("mode-event")).toHaveCount(0);
      await expect(page.locator('input[type="file"]')).toHaveCount(0);
      await expect(page.getByTestId("composer").getByRole("button", { name: /更多功能/ })).toHaveCount(0);

      const input = page.getByTestId("composer").locator("textarea");
      await input.fill("Explain entropy in plain language");
      await input.press("Enter");
      await expect.poll(() => state.sentBodies.length).toBe(1);
      expect(Object.keys(state.sentBodies[0])).toEqual(["content"]);
      await expect(page.getByTestId("conversation-mode")).toBeVisible();
      await page.reload();
    }
  });

  test("scrolls only the thread and keeps the composer inside the viewport", async ({ page }) => {
    await register(page);
    const longMessages = Array.from({ length: 24 }, (_, index) => [
      message(`u-${index}`, "user", `question ${index}`),
      message(`a-${index}`, "assistant", `answer ${index}`),
    ]).flat();
    await installConversationApi(page, { mode: "companion", messages: longMessages });
    await page.goto(`/chat/${CONVERSATION_ID}`);

    for (const viewport of [
      { width: 1280, height: 720 },
      { width: 1440, height: 900 },
      { width: 1920, height: 1080 },
    ]) {
      await page.setViewportSize(viewport);
      await page.evaluate(() => {
        document.documentElement.style.zoom = "2";
      });
      const metrics = await page.evaluate(() => {
        const thread = document.querySelector<HTMLElement>('[data-testid="chat-thread"]')!;
        const composer = document.querySelector<HTMLElement>('[data-testid="composer"]')!;
        const before = composer.getBoundingClientRect().toJSON();
        thread.scrollTop = Math.max(0, thread.scrollHeight - thread.clientHeight);
        const after = composer.getBoundingClientRect().toJSON();
        return {
          rootOverflow: document.documentElement.scrollHeight - window.innerHeight,
          bodyOverflow: document.body.scrollHeight - window.innerHeight,
          horizontalOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
          threadScrollable: thread.scrollHeight > thread.clientHeight,
          threadAtEnd: thread.scrollTop > 0,
          before,
          after,
        };
      });
      expect(metrics.rootOverflow).toBeLessThanOrEqual(1);
      expect(metrics.bodyOverflow).toBeLessThanOrEqual(1);
      expect(metrics.horizontalOverflow).toBeLessThanOrEqual(1);
      expect(metrics.threadScrollable).toBe(true);
      expect(metrics.threadAtEnd).toBe(true);
      expect(Math.abs(metrics.before.x - metrics.after.x)).toBeLessThanOrEqual(1);
      expect(Math.abs(metrics.before.y - metrics.after.y)).toBeLessThanOrEqual(1);
      expect(Math.abs(metrics.before.width - metrics.after.width)).toBeLessThanOrEqual(1);
      expect(Math.abs(metrics.before.height - metrics.after.height)).toBeLessThanOrEqual(1);
      expect(metrics.after.y + metrics.after.height).toBeLessThanOrEqual(viewport.height / 2 + 1);
      await page.evaluate(() => {
        document.documentElement.style.zoom = "1";
      });
    }
  });

  test("keeps compact citations and natural-language keyboard flow", async ({ page }) => {
    await register(page);
    const retrieval = {
      citations: [
        {
          citation_id: "citation-1",
          filename: "thermodynamics.md",
          page_number: 4,
          section_title: "Entropy",
          rank: 1,
        },
      ],
    };
    await installConversationApi(page, {
      mode: "study",
      messages: [
        message("u-1", "user", "Explain entropy"),
        message("a-1", "assistant", "Entropy is a measure of disorder.", "done", retrieval),
      ],
    });
    await page.goto(`/chat/${CONVERSATION_ID}`);

    await expect(page.getByTestId("retrieval-citations-card")).toBeVisible();
    await expect(page.getByTestId("retrieval-citations-card")).toContainText("thermodynamics.md");
    await expect(page.getByText("本地检索")).toHaveCount(0);
    await expect(page.getByText("检索充足")).toHaveCount(0);
    await page.getByTestId("citation-item-1").press("Enter");
    await expect(page.getByTestId("citation-detail-1")).toContainText("Entropy");

    const input = page.getByTestId("composer").locator("textarea");
    await input.focus();
    await input.press("Shift+Enter");
    await input.type("search papers in plain language");
    await input.press("Enter");
  });
});
