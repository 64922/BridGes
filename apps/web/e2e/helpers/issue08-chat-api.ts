import { expect, type Page, type Route } from "@playwright/test";

import { signUp, uniqueCredentials } from "./auth";

export const CONVERSATION_ID = "conv-issue08";
export const OTHER_CONVERSATION_ID = "conv-issue08-other";
const NOW = "2026-10-18T00:00:00Z";
const PASSWORD = "correct-horse-issue08";

export interface MockMessage {
  message_id: string;
  conversation_id: string;
  role: "user" | "assistant";
  attempt_number: number;
  status: string;
  content: string;
  attachments: unknown[];
  retrieval: unknown;
  error_code: string | null;
  error_message: string | null;
  duration_ms: number | null;
  model_id: string | null;
  run_lock_id: string | null;
  created_at: string;
  updated_at: string;
}

export type DetailHandler = (citationId: string) => Promise<{
  status: number;
  body: unknown;
}> | {
  status: number;
  body: unknown;
};

function citation(id: string, rank: number, filename: string, snippet: string) {
  return {
    citation_id: id,
    source_layer: "knowledge_base",
    object_id: `obj-${id}`,
    filename,
    media_type: "text/markdown",
    page_number: rank,
    section_title: "熵增原理",
    snippet,
    rank,
  };
}

export const CITATION_A = citation("cit-a", 1, "课程笔记.md", "熵在孤立系统中不会减少。");
export const CITATION_B = citation("cit-b", 2, "热力学讲义.md", "可逆过程是理想化的极限。");
export const CITATION_RETRY = citation("cit-retry", 3, "需要重试.md", "重试后读取到的片段。");
const CITATION_DELETED = citation("cit-deleted", 4, "已删除.md", "这段不应显示。");
const CITATION_PERMISSION = citation(
  "cit-permission",
  5,
  "授权变化.md",
  "这段敏感片段不应显示。"
);
const CITATION_MISSING = citation("cit-missing", 6, "不存在.md", "这段也不应显示。");

export const ALL_CITATIONS = [
  CITATION_A,
  CITATION_B,
  CITATION_RETRY,
  CITATION_DELETED,
  CITATION_PERMISSION,
  CITATION_MISSING,
];

export function message(
  id: string,
  role: "user" | "assistant",
  content: string,
  citations: ReturnType<typeof citation>[] = []
): MockMessage {
  return {
    message_id: id,
    conversation_id: CONVERSATION_ID,
    role,
    attempt_number: 1,
    status: "done",
    content,
    attachments: [],
    retrieval:
      citations.length > 0
        ? {
            round_id: `round-${id}`,
            message_id: id,
            conversation_id: CONVERSATION_ID,
            use_knowledge_base: true,
            sufficiency: "sufficient",
            layers: [],
            citations,
            note: null,
            created_at: NOW,
          }
        : null,
    error_code: null,
    error_message: null,
    duration_ms: role === "assistant" ? 90 : null,
    model_id: role === "assistant" ? "mock-model" : null,
    run_lock_id: role === "assistant" ? "mock-lock" : null,
    created_at: NOW,
    updated_at: NOW,
  };
}

function history(messages: MockMessage[]) {
  return {
    conversation_id: CONVERSATION_ID,
    title: "引用资料面板",
    mode: "companion",
    mode_locked: true,
    pinned: false,
    created_at: NOW,
    updated_at: NOW,
    messages,
    mode_events: [],
  };
}

const sseStarted = (messageId: string, attempt: number, userMessageId: string) =>
  `event: started\ndata: ${JSON.stringify({
    kind: "started",
    conversation_id: CONVERSATION_ID,
    user_message_id: userMessageId,
    message_id: messageId,
    attempt_number: attempt,
  })}\n\n`;

const sseDelta = (messageId: string, delta: string) =>
  `event: delta\ndata: ${JSON.stringify({ kind: "delta", message_id: messageId, delta })}\n\n`;

const sseDone = (value: MockMessage) =>
  `event: done\ndata: ${JSON.stringify({
    kind: "done",
    message_id: value.message_id,
    message: value,
  })}\n\n`;

export function detailBody(citationProjection: unknown, accessStatus: string, message: string) {
  return {
    citation: citationProjection,
    access_status: accessStatus,
    access_message: message,
    download_url:
      accessStatus === "accessible"
        ? `/knowledge-base/materials/obj-${(citationProjection as { citation_id: string }).citation_id}/download`
        : null,
  };
}

async function installChatApi(
  page: Page,
  options: {
    detailHandler?: DetailHandler;
    enableSend?: boolean;
    messages?: MockMessage[];
  } = {}
): Promise<{ sendBodies: Array<Record<string, unknown>> }> {
  const state = {
    messages: options.messages ?? [
      message("u-1", "user", "解释熵增"),
      message("a-1", "assistant", "熵是系统无序程度的度量。", ALL_CITATIONS),
    ],
    counter: 10,
  };
  const sendBodies: Array<Record<string, unknown>> = [];
  const eventStreams = new Map<string, string>();

  await page.route("**/api/chat/conversations", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        conversations: [
          {
            conversation_id: CONVERSATION_ID,
            title: "引用资料面板",
            mode: "companion",
            message_count: state.messages.length,
            created_at: NOW,
            updated_at: NOW,
          },
          {
            conversation_id: OTHER_CONVERSATION_ID,
            title: "另一个会话",
            mode: "companion",
            message_count: 0,
            created_at: NOW,
            updated_at: NOW,
          },
        ],
      }),
    });
  });

  await page.route(
    `**/api/chat/conversations/${CONVERSATION_ID}`,
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(history(state.messages)),
      });
    }
  );

  await page.route(
    `**/api/chat/conversations/${OTHER_CONVERSATION_ID}`,
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          conversation_id: OTHER_CONVERSATION_ID,
          title: "另一个会话",
          mode: "companion",
          created_at: NOW,
          updated_at: NOW,
          messages: [],
          mode_events: [],
        }),
      });
    }
  );

  if (options.enableSend) {
    await page.route(
      `**/api/chat/conversations/${CONVERSATION_ID}/messages`,
      async (route) => {
        if (route.request().method() !== "POST") return;
        const body = JSON.parse(route.request().postData() ?? "{}");
        sendBodies.push(body);
        state.counter += 1;
        const userMessage = message(`u-${state.counter}`, "user", String(body.content ?? ""));
        const assistantMessage = message(
          `a-${state.counter}`,
          "assistant",
          "",
          [citation(`cit-follow-${state.counter}`, 1, "后续资料.md", "后续引用的片段。")]
        );
        assistantMessage.status = "streaming";
        const doneMessage = {
          ...assistantMessage,
          status: "done",
          content: "后续回答已收到。",
        };
        // 运行终态后客户端会重新读取权威历史；替身直接保存终态正文。
        state.messages.push(userMessage, doneMessage);
        eventStreams.set(
          assistantMessage.message_id,
          `${sseStarted(assistantMessage.message_id, 1, userMessage.message_id)}` +
            `${sseDelta(assistantMessage.message_id, "后续回答已收到。")}` +
            `${sseDone(doneMessage)}`
        );
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            run_id: `run-${assistantMessage.message_id}`,
            cursor: 1,
            user_message: userMessage,
            assistant_message: assistantMessage,
          }),
        });
      }
    );

    await page.route(
      `**/api/chat/conversations/${CONVERSATION_ID}/messages/*/events**`,
      async (route) => {
        const messageId = route.request().url().split("/messages/")[1].split("/")[0];
        await route.fulfill({
          status: 200,
          contentType: "text/event-stream",
          headers: { "Cache-Control": "no-cache", "X-Accel-Buffering": "no" },
          body: eventStreams.get(messageId) ?? "",
        });
      }
    );
  }

  await page.route(
    `**/api/chat/conversations/${CONVERSATION_ID}/messages/*/citations/*`,
    async (route: Route) => {
      const citationId = route.request().url().split("/citations/")[1].split("/")[0];
      const fallback = await defaultDetail(citationId);
      const result = options.detailHandler
        ? await options.detailHandler(citationId)
        : fallback;
      await route.fulfill({
        status: result.status,
        contentType: "application/json",
        body: JSON.stringify(result.body),
      });
    }
  );

  return { sendBodies };
}

export async function defaultDetail(citationId: string): Promise<{
  status: number;
  body: unknown;
}> {
  if (citationId === CITATION_RETRY.citation_id) {
    return { status: 200, body: detailBody(CITATION_RETRY, "accessible", "原文可访问。") };
  }
  if (citationId === CITATION_DELETED.citation_id) {
    return {
      status: 200,
      body: detailBody(CITATION_DELETED, "deleted", "原文已删除，无法打开。"),
    };
  }
  if (citationId === CITATION_PERMISSION.citation_id) {
    return {
      status: 200,
      body: detailBody(
        CITATION_PERMISSION,
        "permission_changed",
        "附件已从消息中移除，授权已变化，无法打开原文。"
      ),
    };
  }
  if (citationId === CITATION_MISSING.citation_id) {
    return { status: 404, body: { detail: { message: "引用不存在或没有访问权限。" } } };
  }
  const found = ALL_CITATIONS.find((item) => item.citation_id === citationId);
  return {
    status: 200,
    body: detailBody(found ?? CITATION_A, "accessible", "原文可访问。"),
  };
}

export async function openConversation(
  page: Page,
  options: Parameters<typeof installChatApi>[1] = {}
): Promise<{ sendBodies: Array<Record<string, unknown>> }> {
  const credentials = uniqueCredentials("issue08");
  await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);
  const api = await installChatApi(page, options);
  await page.goto(`/chat/${CONVERSATION_ID}`);
  await expect(page.getByTestId("composer")).toBeVisible();
  return api;
}
