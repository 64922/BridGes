import { expect, test, type Page } from "@playwright/test";
import * as fs from "fs";
import * as path from "path";

import { signUp, uniqueCredentials } from "./helpers/auth";

/**
 * 对话改版 Issue 14：图片与视频在正文旁交付，并保留真实生成状态。
 *
 * 用可观测的协议替身覆盖图片/视频成功、失败、停止、排队、资产读取失败、
 * 删除、字节加载失败、历史重开与面板生命周期；真实账户隔离与任务状态机
 * 由 image/video pytest 覆盖。媒体字节使用受控替身，不触发真实模型。
 */

const SUCCESS_ID = "conv-issue14-success";
const FAILURE_ID = "conv-issue14-failure";
const NOW = "2026-10-18T00:00:00Z";
const PASSWORD = "correct-horse-issue14";
const PNG_1X1 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";

interface MediaMessage {
  message_id: string;
  conversation_id: string;
  role: "user" | "assistant";
  attempt_number: number;
  status: string;
  content: string;
  image?: Record<string, unknown>;
  video?: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

function message(
  conversationId: string,
  id: string,
  role: "user" | "assistant",
  content: string,
  media: { image?: Record<string, unknown>; video?: Record<string, unknown> } = {}
): MediaMessage {
  return {
    message_id: id,
    conversation_id: conversationId,
    role,
    attempt_number: 1,
    status: "done",
    content,
    ...media,
    created_at: NOW,
    updated_at: NOW,
  };
}

function imageTask(
  taskId: string,
  prompt: string,
  overrides: Record<string, unknown> = {}
): Record<string, unknown> {
  return {
    task_id: taskId,
    attempt_number: 1,
    kind: "generate",
    prompt,
    model_id: "qwen-image-2.0-pro-2026-06-22",
    status: "succeeded",
    error_code: null,
    error_message: null,
    retryable: false,
    asset_id: `${taskId}-asset`,
    result_version_id: `${taskId}-v1`,
    deleted: false,
    synthetic: true,
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  };
}

function videoTask(
  taskId: string,
  prompt: string,
  overrides: Record<string, unknown> = {}
): Record<string, unknown> {
  return {
    task_id: taskId,
    prompt,
    synthetic_media: true,
    model_id: "wan2.7-t2v-2026-06-12",
    status: "succeeded",
    error_code: null,
    error_message: null,
    retryable: false,
    asset_id: `${taskId}-asset`,
    result_object_id: `${taskId}-object`,
    deleted: false,
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  };
}

function imageAsset(
  taskId: string,
  prompt: string,
  overrides: Record<string, unknown> = {}
): Record<string, unknown> {
  return {
    asset_id: `${taskId}-asset`,
    alt_text: `${prompt}的替代文本`,
    alt_text_source: "model",
    current_version_id: `${taskId}-v2`,
    version_count: 2,
    synthetic: true,
    versions: [
      {
        version_id: `${taskId}-v1`,
        asset_id: `${taskId}-asset`,
        parent_version_id: null,
        kind: "generate",
        prompt,
        model_id: "qwen-image-2.0-pro-2026-06-22",
        object_id: `${taskId}-object-1`,
        media_type: "image/png",
        content_length: 68,
        synthetic: true,
        created_at: NOW,
      },
      {
        version_id: `${taskId}-v2`,
        asset_id: `${taskId}-asset`,
        parent_version_id: `${taskId}-v1`,
        kind: "edit",
        prompt: `${prompt}（编辑版）`,
        model_id: "qwen-image-2.0-pro-2026-06-22",
        object_id: `${taskId}-object-2`,
        media_type: "image/png",
        content_length: 68,
        synthetic: true,
        created_at: NOW,
      },
    ],
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  };
}

function videoAsset(
  taskId: string,
  prompt: string,
  overrides: Record<string, unknown> = {}
): Record<string, unknown> {
  return {
    asset_id: `${taskId}-asset`,
    synthetic_media: true,
    description: `${prompt}的可访问文字说明`,
    description_source: "prompt",
    object_id: `${taskId}-object`,
    prompt,
    model_id: "wan2.7-t2v-2026-06-12",
    cloud_task_id: `cloud-${taskId}`,
    media_type: "video/webm",
    content_length: 2048,
    deleted: false,
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  };
}

const IMAGE_SUCCESS = imageTask("img-success", "一座桥的素描");
const VIDEO_SUCCESS = videoTask("vid-success", "一段海浪视频");
const IMAGE_FAILED = imageTask("img-failed", "失败的教室插画", {
  status: "failed",
  asset_id: null,
  result_version_id: null,
  error_message: "图片生成失败，请稍后重试。",
});
const IMAGE_QUEUED = imageTask("img-queued", "排队中的海报", {
  status: "queued",
  asset_id: null,
  result_version_id: null,
});
const IMAGE_FORBIDDEN = imageTask("img-forbidden", "跨账户图片");
const IMAGE_FLAKY = imageTask("img-flaky", "暂时读取失败的图片");
const IMAGE_BROKEN = imageTask("img-broken", "字节损坏的图片");
const IMAGE_DELETED = imageTask("img-deleted", "已删除的图片", { deleted: true });
const VIDEO_CANCELLED = videoTask("vid-cancelled", "停止的海浪视频", {
  status: "cancelled",
  asset_id: null,
  result_object_id: null,
});
const VIDEO_GENERATING = videoTask("vid-generating", "生成中的海浪", {
  status: "generating",
  asset_id: null,
  result_object_id: null,
});

const IMAGE_TASKS = [
  IMAGE_SUCCESS,
  IMAGE_FAILED,
  IMAGE_QUEUED,
  IMAGE_FORBIDDEN,
  IMAGE_FLAKY,
  IMAGE_BROKEN,
  IMAGE_DELETED,
];
const VIDEO_TASKS = [VIDEO_SUCCESS, VIDEO_CANCELLED, VIDEO_GENERATING];

function history(conversationId: string, messages: MediaMessage[]) {
  return {
    conversation_id: conversationId,
    title:
      conversationId === SUCCESS_ID ? "媒体成功路径" : "媒体故障与停止",
    mode: "companion",
    mode_locked: true,
    pinned: false,
    created_at: NOW,
    updated_at: NOW,
    messages,
    mode_events: [],
    study: null,
    study_diagnosis_tasks: [],
  };
}

const SUCCESS_MESSAGES: MediaMessage[] = [
  message(SUCCESS_ID, "s-u1", "user", "画一座桥，再生成一段海浪视频。"),
  message(SUCCESS_ID, "s-a1", "assistant", "图片生成完成。", {
    image: IMAGE_SUCCESS,
  }),
  message(SUCCESS_ID, "s-u2", "user", "视频呢？"),
  message(SUCCESS_ID, "s-a2", "assistant", "视频生成完成。", {
    video: VIDEO_SUCCESS,
  }),
];

const FAILURE_MESSAGES: MediaMessage[] = [
  message(FAILURE_ID, "f-u1", "user", "失败、排队、停止和资产异常。"),
  message(FAILURE_ID, "f-a1", "assistant", "图片生成失败。", {
    image: IMAGE_FAILED,
  }),
  message(FAILURE_ID, "f-u2", "user", "排队的情况呢？"),
  message(FAILURE_ID, "f-a2", "assistant", "图片任务排队中。", {
    image: IMAGE_QUEUED,
  }),
  message(FAILURE_ID, "f-u3", "user", "停止视频。"),
  message(FAILURE_ID, "f-a3", "assistant", "视频任务已停止。", {
    video: VIDEO_CANCELLED,
  }),
  message(FAILURE_ID, "f-u4", "user", "正在生成的视频。"),
  message(FAILURE_ID, "f-a4", "assistant", "视频任务生成中。", {
    video: VIDEO_GENERATING,
  }),
  message(FAILURE_ID, "f-u5", "user", "跨账户图片。"),
  message(FAILURE_ID, "f-a5", "assistant", "图片资产不可访问。", {
    image: IMAGE_FORBIDDEN,
  }),
  message(FAILURE_ID, "f-u6", "user", "暂时失败的图片。"),
  message(FAILURE_ID, "f-a6", "assistant", "图片资产暂时读取失败。", {
    image: IMAGE_FLAKY,
  }),
  message(FAILURE_ID, "f-u7", "user", "字节损坏的图片。"),
  message(FAILURE_ID, "f-a7", "assistant", "图片字节加载失败。", {
    image: IMAGE_BROKEN,
  }),
  message(FAILURE_ID, "f-u8", "user", "已删除的图片。"),
  message(FAILURE_ID, "f-a8", "assistant", "图片已删除。", {
    image: IMAGE_DELETED,
  }),
];

async function makeWebmBase64(page: Page): Promise<string> {
  return page.evaluate(async () => {
    if (typeof MediaRecorder === "undefined") return "";
    const canvas = document.createElement("canvas");
    canvas.width = 32;
    canvas.height = 18;
    const context = canvas.getContext("2d");
    if (!context) return "";
    const stream = canvas.captureStream(10);
    const recorder = new MediaRecorder(stream, { mimeType: "video/webm" });
    const chunks: Blob[] = [];
    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) chunks.push(event.data);
    };
    const stopped = new Promise<void>((resolve) => {
      recorder.onstop = () => resolve();
    });
    recorder.start();
    for (let index = 0; index < 5; index += 1) {
      context.fillStyle = index % 2 ? "#1d4ed8" : "#93c5fd";
      context.fillRect(0, 0, canvas.width, canvas.height);
      await new Promise((resolve) => setTimeout(resolve, 40));
    }
    recorder.stop();
    await stopped;
    stream.getTracks().forEach((track) => track.stop());
    const blob = new Blob(chunks, { type: "video/webm" });
    const buffer = new Uint8Array(await blob.arrayBuffer());
    let binary = "";
    for (let index = 0; index < buffer.length; index += 1) {
      binary += String.fromCharCode(buffer[index]);
    }
    return btoa(binary);
  });
}

function sendJson(route: import("@playwright/test").Route, body: unknown) {
  return route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

async function installChatApi(page: Page, videoBase64: string) {
  const assetAttempts = new Map<string, number>();

  await page.route("**/api/chat/conversations", async (route) => {
    await sendJson(route, {
      conversations: [
        {
          conversation_id: SUCCESS_ID,
          title: "媒体成功路径",
          mode: "companion",
          message_count: SUCCESS_MESSAGES.length,
          created_at: NOW,
          updated_at: NOW,
        },
        {
          conversation_id: FAILURE_ID,
          title: "媒体故障与停止",
          mode: "companion",
          message_count: FAILURE_MESSAGES.length,
          created_at: NOW,
          updated_at: NOW,
        },
      ],
    });
  });

  await page.route(
    `**/api/chat/conversations/${SUCCESS_ID}`,
    async (route) => {
      await sendJson(route, history(SUCCESS_ID, SUCCESS_MESSAGES));
    }
  );
  await page.route(
    `**/api/chat/conversations/${FAILURE_ID}`,
    async (route) => {
      await sendJson(route, history(FAILURE_ID, FAILURE_MESSAGES));
    }
  );
  await page.route("**/api/chat/attachment-drafts", async (route) => {
    await sendJson(route, []);
  });

  await page.route("**/api/chat/conversations/*/image-tasks/*", async (route) => {
    const taskId = route.request().url().split("/image-tasks/")[1].split("?")[0];
    const task = IMAGE_TASKS.find((item) => item.task_id === taskId);
    if (!task) return route.fulfill({ status: 404, body: "{}" });
    await sendJson(route, task);
  });
  await page.route("**/api/chat/conversations/*/video-tasks/*", async (route) => {
    const taskId = route.request().url().split("/video-tasks/")[1].split("?")[0];
    const task = VIDEO_TASKS.find((item) => item.task_id === taskId);
    if (!task) return route.fulfill({ status: 404, body: "{}" });
    await sendJson(route, task);
  });

  await page.route(
    "**/api/chat/conversations/*/image-assets/*/versions/*/image**",
    async (route) => {
      const url = route.request().url();
      if (url.includes("img-broken")) {
        await route.fulfill({
          status: 404,
          contentType: "application/json",
          body: JSON.stringify({ detail: { message: "图片字节不存在。" } }),
        });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "image/png",
        body: Buffer.from(PNG_1X1, "base64"),
      });
    }
  );

  await page.route("**/api/chat/conversations/*/image-assets/*", async (route) => {
    const url = route.request().url();
    const assetId = url.split("/image-assets/")[1].split("?")[0];
    if (assetId === "img-forbidden-asset") {
      await route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify({ detail: { message: "图片资产不存在。" } }),
      });
      return;
    }
    if (assetId === "img-flaky-asset") {
      const attempt = (assetAttempts.get(assetId) ?? 0) + 1;
      assetAttempts.set(assetId, attempt);
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: { message: "图片资产暂不可用。" } }),
      });
      return;
    }
    const task = IMAGE_TASKS.find((item) => `${item.task_id}-asset` === assetId);
    if (!task) return route.fulfill({ status: 404, body: "{}" });
    await sendJson(route, imageAsset(String(task.task_id), String(task.prompt)));
  });

  await page.route("**/api/chat/conversations/*/video-assets/*/video**", async (route) => {
    if (!videoBase64) {
      await route.fulfill({
        status: 200,
        contentType: "video/webm",
        body: Buffer.alloc(0),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "video/webm",
      body: Buffer.from(videoBase64, "base64"),
    });
  });

  await page.route("**/api/chat/conversations/*/video-assets/*", async (route) => {
    const assetId = route.request().url().split("/video-assets/")[1].split("?")[0];
    const task = VIDEO_TASKS.find((item) => `${item.task_id}-asset` === assetId);
    if (!task) return route.fulfill({ status: 404, body: "{}" });
    await sendJson(route, videoAsset(String(task.task_id), String(task.prompt)));
  });

  return { assetAttempts };
}

async function openConversation(page: Page, conversationId: string, videoBase64: string) {
  const credentials = uniqueCredentials("issue14");
  await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);
  if (!videoBase64) videoBase64 = await makeWebmBase64(page);
  const stats = await installChatApi(page, videoBase64);
  await page.goto(`/chat/${conversationId}`);
  await expect(page.getByTestId("composer")).toBeVisible();
  return stats;
}

test.describe("Issue 14 — 正文中的图片与视频", () => {
  test("图片与视频成功路径、历史重开、面板生命周期和桌面尺寸", async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await openConversation(page, SUCCESS_ID, "");

    const imageMedia = page
      .locator('[data-testid="image-media"]')
      .filter({ hasText: "一座桥的素描" });
    await expect(imageMedia.getByTestId("image-media-status")).toHaveText("已完成");
    const imagePreview = imageMedia.getByTestId("image-media-preview");
    await expect(imagePreview).toBeVisible();
    await expect(imagePreview).toHaveAttribute("alt", /一座桥的素描/);
    await expect(imageMedia.getByTestId("image-media-alt-text")).toContainText(
      "一座桥的素描"
    );
    await expect(imageMedia.getByTestId("image-media-download")).toHaveAttribute(
      "href",
      new RegExp(`/chat/conversations/${SUCCESS_ID}/image-assets/img-success-asset/versions/img-success-v1/image\\?download=1`)
    );

    const videoMedia = page
      .locator('[data-testid="video-media"]')
      .filter({ hasText: "一段海浪视频" });
    await expect(videoMedia.getByTestId("video-media-status")).toHaveText("已完成");
    const video = videoMedia.getByTestId("video-media-preview");
    await expect(video).toBeVisible();
    await expect(video).toHaveAttribute("controls", "");
    await expect(video).not.toHaveAttribute("autoplay", "");
    await expect(video).toHaveAttribute("preload", "none");
    await expect(videoMedia.getByTestId("video-media-description")).toContainText(
      "海浪视频的可访问文字说明"
    );

    // 图片详情面板：按任务版本读取，显示替代文本与下载；Esc 关闭并归还焦点。
    const imageButton = imageMedia.getByTestId("image-media-details");
    await imageButton.click();
    const panel = page.getByTestId("media-viewer");
    await expect(panel).toBeVisible();
    await expect(page.getByTestId("media-viewer-image")).toBeVisible();
    await expect(page.getByTestId("media-viewer-alt-text")).toContainText(
      "一座桥的素描"
    );
    await expect(page.getByTestId("media-viewer-download")).toHaveAttribute(
      "href",
      new RegExp("versions/img-success-v1/image\\?download=1")
    );
    await page.keyboard.press("Escape");
    await expect(panel).toHaveCount(0);
    await expect(imageButton).toBeFocused();

    // 视频详情面板；关闭后播放器随面板卸载。
    const videoButton = videoMedia.getByTestId("video-media-details");
    await videoButton.click();
    await expect(page.getByTestId("media-viewer")).toBeVisible();
    await expect(page.getByTestId("media-viewer-download")).toBeVisible();
    await page.getByTestId("media-viewer-close").click();
    await expect(page.getByTestId("media-viewer")).toHaveCount(0);
    await expect(videoButton).toBeFocused();

    // 历史重开仍读取同一任务与资产，不插入重复产物。
    await page.reload();
    await expect(page.getByTestId("composer")).toBeVisible();
    await expect(
      page.locator('[data-testid="image-media"]').filter({ hasText: "一座桥的素描" })
    ).toHaveCount(1);
    await expect(
      page.locator('[data-testid="video-media"]').filter({ hasText: "一段海浪视频" })
    ).toHaveCount(1);
    await expect(
      page.locator('[data-testid="image-media-preview"]')
    ).toHaveCount(1);
    await expect(
      page.locator('[data-testid="video-media-preview"]')
    ).toHaveCount(1);

    // 打开面板后切换会话，旧面板立即清除。
    await imageMedia.getByTestId("image-media-details").click();
    await expect(page.getByTestId("media-viewer")).toBeVisible();
    await page.getByRole("link", { name: /媒体故障与停止/ }).click();
    await page.waitForURL(`**/chat/${FAILURE_ID}`);
    await expect(page.getByTestId("media-viewer")).toHaveCount(0);

    // 三种目标桌面尺寸与 125% 缩放：媒体容器不撑破页面。
    await page.goto(`/chat/${SUCCESS_ID}`);
    await expect(page.getByTestId("composer")).toBeVisible();
    const evidenceDir = path.resolve(
      __dirname,
      "../../../.scratch/chat-presentation-redesign/evidence/14"
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
      const media = await page
        .locator('[data-testid="image-media"]')
        .filter({ hasText: "一座桥的素描" })
        .boundingBox();
      expect(main).not.toBeNull();
      expect(media).not.toBeNull();
      if (!main || !media) continue;
      expect(media.x).toBeGreaterThanOrEqual(main.x - 1);
      expect(media.x + media.width).toBeLessThanOrEqual(main.x + main.width + 1);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth + 1
        )
      ).toBe(true);
      const imageBox = await page
        .locator('[data-testid="image-media-preview"]')
        .first()
        .boundingBox();
      if (imageBox) {
        expect(imageBox.width).toBeLessThanOrEqual(media.width + 1);
      }
      await page.screenshot({
        path: path.join(evidenceDir, `media-${viewport.label}.png`),
        fullPage: false,
      });
    }
  });

  test("失败、停止、排队、资产读取失败与删除真实呈现，恢复按实际能力", async ({
    page,
  }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    const stats = await openConversation(page, FAILURE_ID, "");

    const failed = page
      .locator('[data-testid="image-media"]')
      .filter({ hasText: "失败的教室插画" });
    await expect(failed.getByTestId("image-media-status")).toHaveText("失败");
    await expect(failed.getByTestId("image-media-error")).toContainText(
      "图片生成失败"
    );
    await expect(failed).toContainText("图片生成已退役");
    await expect(failed.getByTestId("image-media-preview")).toHaveCount(0);
    await expect(failed.getByRole("button", { name: /重试/ })).toHaveCount(0);

    const queued = page
      .locator('[data-testid="image-media"]')
      .filter({ hasText: "排队中的海报" });
    await expect(queued.getByTestId("image-media-status")).toHaveText("排队中");
    await expect(queued).toContainText("后台独立进行");

    const cancelled = page
      .locator('[data-testid="video-media"]')
      .filter({ hasText: "停止的海浪视频" });
    await expect(cancelled.getByTestId("video-media-status")).toHaveText("已停止");
    await expect(cancelled.getByTestId("video-media-stopped")).toContainText(
      "迟到的云端结果不会保存"
    );

    const generating = page
      .locator('[data-testid="video-media"]')
      .filter({ hasText: "生成中的海浪" });
    await expect(generating.getByTestId("video-media-status")).toHaveText("生成中");

    const forbidden = page
      .locator("article")
      .filter({ hasText: "图片资产不可访问。" })
      .getByTestId("image-media");
    await expect(forbidden.getByTestId("image-media-asset-error")).toContainText(
      "已删除或当前账户无权访问"
    );
    await expect(forbidden.getByTestId("image-media-preview")).toHaveCount(0);
    await expect(forbidden.getByTestId("image-media-download")).toHaveCount(0);

    const flaky = page
      .locator("article")
      .filter({ hasText: "图片资产暂时读取失败。" })
      .getByTestId("image-media");
    await expect(flaky.getByTestId("image-media-asset-error")).toContainText("HTTP 503");
    await expect(flaky.getByTestId("image-media-preview")).toHaveCount(0);
    const attemptsBeforeRetry =
      stats.assetAttempts.get("img-flaky-asset") ?? 0;
    await flaky.getByTestId("image-media-retry").dispatchEvent("click");
    await expect
      .poll(() => stats.assetAttempts.get("img-flaky-asset") ?? 0)
      .toBeGreaterThan(attemptsBeforeRetry);
    await expect(flaky.getByTestId("image-media-asset-error")).toContainText("HTTP 503");

    const broken = page
      .locator("article")
      .filter({ hasText: "图片字节加载失败。" })
      .getByTestId("image-media");
    await expect(broken.getByTestId("image-media-preview-error")).toContainText(
      "图片无法显示"
    );
    await expect(broken).toContainText("字节损坏的图片");
    await expect(broken.getByTestId("image-media-download")).toBeVisible();

    const deleted = page
      .locator("article")
      .filter({ hasText: "图片已删除。" })
      .getByTestId("image-media-deleted");
    await expect(deleted).toContainText("该图片已删除。");
    await expect(deleted.getByTestId("image-media-preview")).toHaveCount(0);
    await expect(deleted.getByTestId("image-media-download")).toHaveCount(0);
  });
});
