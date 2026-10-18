import { expect, test, type Page, type Route } from "@playwright/test";
import * as fs from "fs";
import * as path from "path";

import { signUp, uniqueCredentials } from "./helpers/auth";

/**
 * 对话改版 Issue 13：消息附件在右侧面板安全预览。
 *
 * 用可观测的协议替身覆盖附件对象身份、支持格式预览、不支持格式降级、
 * 读取失败恢复、迟到响应隔离、切换会话释放面板，以及预览期间继续发送。
 * 真实账户隔离、绑定与解析规则由 chat pytest 覆盖，本 spec 不重复伪造。
 */

const CONVERSATION_ID = "conv-issue13";
const OTHER_CONVERSATION_ID = "conv-issue13-other";
const NOW = "2026-10-18T00:00:00Z";
const PASSWORD = "correct-horse-issue13";

const PNG_1X1 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";

interface Attachment {
  object_id: string;
  original_filename: string;
  media_type: string;
  content_length: number;
  content_hash: string;
  conversation_id: string;
  message_id: string;
  status: string;
  ordinal: number;
  ingestion_status: string;
  ingestion_error: string | null;
  adopted_from_conversation_id: string;
  adopted_from_object_id: string;
  created_at: string;
  updated_at: string;
}

function attachment(overrides: Partial<Attachment> = {}): Attachment {
  return {
    object_id: "obj-md",
    original_filename: "课程笔记.md",
    media_type: "text/markdown",
    content_length: 128,
    content_hash: "hash-md",
    conversation_id: CONVERSATION_ID,
    message_id: "u-attach",
    status: "bound",
    ordinal: 1,
    ingestion_status: "ready",
    ingestion_error: null,
    adopted_from_conversation_id: "",
    adopted_from_object_id: "",
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  };
}

const ATTACHMENTS: Attachment[] = [
  attachment({
    object_id: "obj-photo",
    original_filename: "板书.png",
    media_type: "image/png",
    content_hash: "hash-photo",
    ordinal: 1,
    ingestion_status: "none",
  }),
  attachment({
    object_id: "obj-pdf",
    original_filename: "课程讲义.pdf",
    media_type: "application/pdf",
    content_hash: "hash-pdf",
    ordinal: 2,
  }),
  attachment({
    object_id: "obj-docx",
    original_filename: "实验报告.docx",
    media_type:
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    content_hash: "hash-docx",
    ordinal: 3,
  }),
  attachment({
    object_id: "obj-md",
    original_filename: "课程笔记.md",
    media_type: "text/markdown",
    content_hash: "hash-md",
    ordinal: 4,
  }),
  attachment({
    object_id: "obj-slow",
    original_filename: "慢速附件.md",
    media_type: "text/markdown",
    content_hash: "hash-slow",
    ordinal: 5,
  }),
  attachment({
    object_id: "obj-fast",
    original_filename: "快速附件.md",
    media_type: "text/markdown",
    content_hash: "hash-fast",
    ordinal: 6,
  }),
];

function message(
  id: string,
  role: "user" | "assistant",
  content: string,
  attachments: Attachment[] = []
) {
  return {
    message_id: id,
    conversation_id: CONVERSATION_ID,
    role,
    attempt_number: 1,
    status: "done",
    content,
    attachments,
    module_id: null,
    retrieval: null,
    error_code: null,
    error_message: null,
    duration_ms: role === "assistant" ? 90 : null,
    model_id: role === "assistant" ? "mock-model" : null,
    run_lock_id: role === "assistant" ? "mock-lock" : null,
    created_at: NOW,
    updated_at: NOW,
  };
}

function history() {
  const messages = [];
  for (let index = 0; index < 10; index += 1) {
    messages.push(message(`u-hist-${index}`, "user", `历史问题 ${index}`));
    messages.push(message(`a-hist-${index}`, "assistant", `历史回答 ${index}`));
  }
  messages.push(message("u-attach", "user", "请结合这些附件回答", ATTACHMENTS));
  messages.push(message("a-attach", "assistant", "附件已收到，我会按需引用。"));
  return {
    conversation_id: CONVERSATION_ID,
    title: "附件预览",
    mode: "companion",
    mode_locked: true,
    pinned: false,
    created_at: NOW,
    updated_at: NOW,
    messages,
    mode_events: [],
  };
}

const sseStarted = (messageId: string, userMessageId: string) =>
  `event: started\ndata: ${JSON.stringify({
    kind: "started",
    conversation_id: CONVERSATION_ID,
    user_message_id: userMessageId,
    message_id: messageId,
    attempt_number: 1,
  })}\n\n`;

const sseDelta = (messageId: string, delta: string) =>
  `event: delta\ndata: ${JSON.stringify({
    kind: "delta",
    message_id: messageId,
    delta,
  })}\n\n`;

const sseDone = (value: ReturnType<typeof message>) =>
  `event: done\ndata: ${JSON.stringify({
    kind: "done",
    message_id: value.message_id,
    message: value,
  })}\n\n`;

async function installChatApi(
  page: Page,
  options: {
    downloadHandler?: (objectId: string, route: Route) => Promise<void> | void;
    enableSend?: boolean;
  } = {}
): Promise<{ sendBodies: Array<Record<string, unknown>>; downloadRequests: string[] }> {
  const state = { messages: history().messages as ReturnType<typeof message>[] };
  const sendBodies: Array<Record<string, unknown>> = [];
  const downloadRequests: string[] = [];
  const eventStreams = new Map<string, string>();
  let counter = 100;

  await page.route("**/api/chat/conversations", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        conversations: [
          {
            conversation_id: CONVERSATION_ID,
            title: "附件预览",
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

  await page.route(`**/api/chat/conversations/${CONVERSATION_ID}`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ...history(), messages: state.messages }),
    });
  });

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
          mode_locked: true,
          pinned: false,
          created_at: NOW,
          updated_at: NOW,
          messages: [],
          mode_events: [],
        }),
      });
    }
  );

  await page.route("**/api/chat/attachment-drafts", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([]),
    });
  });

  await page.route(
    `**/api/chat/conversations/${CONVERSATION_ID}/attachments/*/download`,
    async (route) => {
      const objectId = route.request().url().split("/attachments/")[1].split("/")[0];
      downloadRequests.push(objectId);
      if (options.downloadHandler) {
        await options.downloadHandler(objectId, route);
        return;
      }
      if (objectId === "obj-photo") {
        await route.fulfill({
          status: 200,
          contentType: "image/png",
          body: Buffer.from(PNG_1X1, "base64"),
        });
        return;
      }
      if (objectId === "obj-pdf") {
        await route.fulfill({
          status: 200,
          contentType: "application/pdf",
          body: Buffer.from("%PDF-1.4\n% preview\n", "utf-8"),
        });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "text/markdown",
        body: `# 热力学\n\n**熵增**\n\n<script>window.__pwned = true</script>`,
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
        counter += 1;
        const userMessage = message(`u-${counter}`, "user", String(body.content ?? ""));
        const streaming = message(`a-${counter}`, "assistant", "");
        const done = {
          ...message(`a-${counter}`, "assistant", "预览期间回答已收到。"),
        };
        state.messages.push(userMessage, done);
        eventStreams.set(
          streaming.message_id,
          `${sseStarted(streaming.message_id, userMessage.message_id)}` +
            `${sseDelta(streaming.message_id, "预览期间回答已收到。")}` +
            `${sseDone(done)}`
        );
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            run_id: `run-${counter}`,
            cursor: 1,
            user_message: userMessage,
            assistant_message: { ...streaming, status: "streaming" },
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

  return { sendBodies, downloadRequests };
}

async function openConversation(
  page: Page,
  options: Parameters<typeof installChatApi>[1] = {}
) {
  const credentials = uniqueCredentials("issue13");
  await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);
  const api = await installChatApi(page, options);
  await page.goto(`/chat/${CONVERSATION_ID}`);
  await expect(page.getByTestId("composer")).toBeVisible();
  return api;
}

test.describe("Issue 13 — 消息附件安全预览", () => {
  test("默认关闭；预览 PDF 时继续发送，关闭归还焦点并保留阅读位置", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const { sendBodies } = await openConversation(page, { enableSend: true });

    await expect(page.getByTestId("attachment-viewer")).toHaveCount(0);
    const trigger = page.getByTestId("attachment-open-obj-pdf");
    await trigger.click();

    const viewer = page.getByTestId("attachment-viewer");
    await expect(viewer).toBeVisible();
    await expect(viewer).toContainText("课程讲义.pdf");
    await expect(page.getByTestId("attachment-viewer-pdf")).toBeVisible();
    expect(
      await page.getByTestId("attachment-viewer-download").getAttribute("href")
    ).toBe(`/api/chat/conversations/${CONVERSATION_ID}/attachments/obj-pdf/download`);

    // 关闭面板不改变对话阅读位置。
    const thread = page.getByTestId("chat-thread");
    const scrollTop = await thread.evaluate((element) => {
      element.style.scrollBehavior = "auto";
      const target = Math.min(
        160,
        Math.max(0, element.scrollHeight - element.clientHeight - 24)
      );
      element.scrollTop = target;
      return element.scrollTop;
    });
    expect(scrollTop).toBeGreaterThan(0);
    await page.getByTestId("attachment-viewer-close").click();
    await expect(page.getByTestId("attachment-viewer")).toHaveCount(0);
    await expect(trigger).toBeFocused();
    expect(await thread.evaluate((element) => element.scrollTop)).toBe(scrollTop);

    // 预览打开时输入与发送仍可用；新回答完成后面板仍保留。
    await trigger.click();
    await expect(page.getByTestId("attachment-viewer")).toBeVisible();
    await page.locator("#composer-input").fill("预览时继续追问");
    await page.locator("#composer-input").press("Enter");
    await expect(page.getByText("预览期间回答已收到。")).toBeVisible();
    await expect(page.getByTestId("attachment-viewer")).toBeVisible();
    await expect(page.getByTestId("composer")).toBeVisible();
    expect(sendBodies).toHaveLength(1);
  });

  test("照片与 Markdown 安全预览；DOCX 明确降级到原件入口", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const { downloadRequests } = await openConversation(page);

    await page.getByTestId("attachment-open-obj-photo").click();
    await expect(page.getByTestId("attachment-viewer-image")).toBeVisible();
    await expect(page.getByTestId("attachment-viewer-photo-note")).toContainText(
      "不进入全局知识库或画像"
    );
    await expect(page.getByTestId("attachment-viewer-download")).toHaveAttribute(
      "href",
      `/api/chat/conversations/${CONVERSATION_ID}/attachments/obj-photo/download`
    );
    await page.keyboard.press("Escape");

    await page.getByTestId("attachment-open-obj-md").click();
    const markdown = page.getByTestId("attachment-viewer-markdown");
    await expect(markdown.getByRole("heading", { level: 1 })).toContainText("热力学");
    await expect(markdown.getByText("熵增")).toBeVisible();
    expect(await page.evaluate(() => (window as { __pwned?: boolean }).__pwned)).toBe(
      undefined
    );
    await page.keyboard.press("Escape");

    await page.getByTestId("attachment-open-obj-docx").click();
    await expect(page.getByTestId("attachment-viewer-unsupported")).toContainText(
      "暂不支持"
    );
    await expect(page.getByTestId("attachment-viewer-download")).toHaveAttribute(
      "href",
      `/api/chat/conversations/${CONVERSATION_ID}/attachments/obj-docx/download`
    );
    expect(downloadRequests).not.toContain("obj-docx");
  });

  test("读取失败可重试；快速切换只呈现当前附件；切换会话清除预览", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const attempts = new Map<string, number>();
    await openConversation(page, {
      downloadHandler: async (objectId, route) => {
        const attempt = (attempts.get(objectId) ?? 0) + 1;
        attempts.set(objectId, attempt);
        if (objectId === "obj-md" && attempt <= 2) {
          await route.fulfill({
            status: 503,
            contentType: "application/json",
            body: JSON.stringify({ detail: { message: "附件内容当前不可读取。" } }),
          });
          return;
        }
        if (objectId === "obj-slow") {
          await new Promise((resolve) => setTimeout(resolve, 500));
          try {
            await route.fulfill({
              status: 200,
              contentType: "text/markdown",
              body: "慢速附件正文",
            });
          } catch {
            // 客户端切换后已中止请求，迟到响应不得进入面板。
          }
          return;
        }
        await route.fulfill({
          status: 200,
          contentType: "text/markdown",
          body: objectId === "obj-fast" ? "快速附件正文" : "# 重试成功",
        });
      },
    });

    await page.getByTestId("attachment-open-obj-md").click();
    await expect(page.getByTestId("attachment-viewer-error")).toBeVisible();
    await page.getByTestId("attachment-viewer-retry").click();
    await expect(page.getByTestId("attachment-viewer-markdown")).toContainText(
      "重试成功"
    );

    await page.getByTestId("attachment-open-obj-slow").click();
    await page.getByTestId("attachment-open-obj-fast").click();
    await expect(page.getByTestId("attachment-viewer-markdown")).toContainText(
      "快速附件正文"
    );
    await page.waitForTimeout(700);
    await expect(page.getByTestId("attachment-viewer-markdown")).toContainText(
      "快速附件正文"
    );
    await expect(page.getByTestId("attachment-viewer")).not.toContainText(
      "慢速附件正文"
    );

    await page.getByRole("link", { name: /另一个会话/ }).click();
    await page.waitForURL(`**/chat/${OTHER_CONVERSATION_ID}`);
    await expect(page.getByTestId("attachment-viewer")).toHaveCount(0);
  });

  test("三种目标桌面尺寸及 125% 缩放下正文、面板与输入框互不遮挡", async ({
    page,
  }) => {
    await openConversation(page);
    await page.getByTestId("attachment-open-obj-photo").click();
    await expect(page.getByTestId("attachment-viewer-image")).toBeVisible();

    const evidenceDir = path.resolve(
      __dirname,
      "../../../.scratch/chat-presentation-redesign/evidence/13"
    );
    fs.mkdirSync(evidenceDir, { recursive: true });

    for (const viewport of [
      { width: 1280, height: 720, label: "1280x720" },
      { width: 1440, height: 900, label: "1440x900" },
      { width: 1920, height: 1080, label: "1920x1080" },
      { width: 1024, height: 576, label: "1280x720-zoom125" },
    ]) {
      await page.setViewportSize(viewport);
      const main = await page.getByTestId("main-content").boundingBox();
      const panel = await page.getByTestId("attachment-viewer").boundingBox();
      const composer = await page.getByTestId("composer").boundingBox();
      expect(main).not.toBeNull();
      expect(panel).not.toBeNull();
      expect(composer).not.toBeNull();
      if (!main || !panel || !composer) continue;
      expect(panel.x).toBeGreaterThanOrEqual(main.x + main.width - 1);
      expect(composer.x + composer.width).toBeLessThanOrEqual(panel.x + 1);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth + 1
        )
      ).toBe(true);
      await expect(page.getByTestId("attachment-viewer-image")).toBeVisible();
      await page.screenshot({
        path: path.join(evidenceDir, `attachment-panel-${viewport.label}.png`),
        fullPage: false,
      });
    }
  });
});
