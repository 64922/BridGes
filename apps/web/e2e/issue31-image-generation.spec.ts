import { expect, type Page, test } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";
import { installRunEventsRoutes, runCreated } from "./helpers/chat-mock";

/**
 * Issue 31：图片生成与编辑端到端测试。
 *
 * 覆盖：对话框提交生成（真实消息流）→ 任务状态卡（排队/成功）→ 资产卡
 * （真实图片/替代文本修改/版本切换/下载/删除确认）；刷新后从消息投影
 * 恢复；失败重试；取消。全部 API 用 page.route 替身（状态机推进任务），
 * 页面渲染与交互为真实链路。
 */

const NOW = "2026-08-05T08:00:00Z";
const PNG_BYTES =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";

function sseBlock(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

interface ImageTask {
  task_id: string;
  kind: "generate" | "edit";
  prompt: string;
  source_version_id: string | null;
  source_object_id: string | null;
  model_id: string | null;
  status: "queued" | "running" | "recovery" | "succeeded" | "failed" | "cancelled";
  error_code: string | null;
  error_message: string | null;
  retryable: boolean;
  asset_id: string | null;
  result_version_id: string | null;
  deleted: boolean;
  created_at: string;
  updated_at: string;
}

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
  image?: ImageTask | null;
}

function assistantMessage(id: string, content: string, image: ImageTask | null = null): MockMessage {
  return {
    message_id: id,
    conversation_id: "mock-1",
    role: "assistant",
    attempt_number: 1,
    status: "done",
    content,
    error_code: null,
    error_message: null,
    duration_ms: 1200,
    model_id: "qwen3.7-plus-2026-05-26",
    run_lock_id: "lock-1",
    created_at: NOW,
    updated_at: NOW,
    image,
  };
}

interface MockImageApiOptions {
  /** 轮询第几次返回终态（1 = 挂载后立即成功；2 = 等待一个轮询间隔）。 */
  succeedAfterPolls?: number;
  /** 任务最终失败（失败原因可重试）。 */
  failWith?: { code: string; message: string };
}

/**
 * 图片链路替身：发送（SSE 携带 image 事件）→ 任务轮询状态机 → 资产面。
 * 发送后立即把任务并入消息历史（助手消息带 queued 投影）；轮询按调用
 * 次数推进到 succeeded（或按 failWith 置 failed）。
 */
async function installMockImageApi(
  page: Page,
  initialMessages: MockMessage[] = [],
  options: MockImageApiOptions = {}
): Promise<{ sentBodies: () => Array<Record<string, unknown>> }> {
  const { succeedAfterPolls = 2, failWith } = options;
  const version = (id: string, kind: string, prompt: string, created = NOW) => ({
    version_id: id,
    asset_id: "asset-1",
    parent_version_id: null,
    kind,
    prompt,
    model_id: "qwen-image-2.0-pro-2026-06-22",
    object_id: `obj-${id}`,
    media_type: "image/png",
    content_length: 68,
    created_at: created,
  });
  const state: {
    messages: MockMessage[];
    sent: number;
    task: ImageTask | null;
    polls: number;
    cancelled: boolean;
    asset: Record<string, unknown> | null;
    deleted: boolean;
    altText: string;
  } = {
    messages: [...initialMessages],
    sent: 0,
    task: null,
    polls: 0,
    cancelled: false,
    asset: {
      asset_id: "asset-1",
      alt_text: "一座桥的素描（自动生成替代文本）",
      alt_text_source: "fallback",
      current_version_id: "v-1",
      version_count: 1,
      versions: [version("v-1", "generate", "一座桥的素描")],
      created_at: NOW,
      updated_at: NOW,
    },
    deleted: false,
    altText: "一座桥的素描（自动生成替代文本）",
  };
  // Issue 02：消息 → 持久化事件流（POST 创建运行后由 events 端点回放）
  const eventStreams = new Map<string, string>();

  const taskForStatus = (status: ImageTask["status"]): ImageTask => {
    const base = state.task as ImageTask;
    return {
      ...base,
      status,
      updated_at: NOW,
      error_code: status === "failed" ? (failWith?.code ?? "cloud_failed") : null,
      error_message: status === "failed" ? (failWith?.message ?? "云端生成失败") : null,
      retryable: status === "failed",
      asset_id: status === "succeeded" ? "asset-1" : base.asset_id,
      result_version_id: status === "succeeded" ? "v-1" : base.result_version_id,
    };
  };

  await page.route("**/api/chat/conversations/mock-1", async (route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        conversation_id: "mock-1",
        title: "测试对话",
        mode: "companion",
        created_at: NOW,
        updated_at: NOW,
        messages: state.messages,
        mode_events: [],
      }),
    });
  });

  const sentBodies: Array<Record<string, unknown>> = [];
  await page.route("**/api/chat/conversations/mock-1/messages", async (route) => {
    const request = route.request();
    if (request.method() !== "POST") {
      await route.continue();
      return;
    }
    const body = request.postDataJSON() as Record<string, unknown>;
    sentBodies.push(body);
    state.sent += 1;
    const turn = state.sent;
    const image = body.image as { kind: string; prompt: string } | undefined;
    const taskId = `task-${turn}`;
    state.task = {
      task_id: taskId,
      kind: image?.kind === "edit" ? "edit" : "generate",
      prompt: String(image?.prompt ?? body.content ?? ""),
      source_version_id: null,
      source_object_id: null,
      model_id: null,
      status: "queued",
      error_code: null,
      error_message: null,
      retryable: false,
      asset_id: null,
      result_version_id: null,
      deleted: false,
      created_at: NOW,
      updated_at: NOW,
    };
    state.asset = {
      asset_id: "asset-1",
      alt_text: state.altText,
      alt_text_source: "fallback",
      current_version_id: "v-1",
      version_count: 1,
      versions: [version("v-1", "generate", "一座桥的素描")],
      created_at: NOW,
      updated_at: NOW,
    };
    const user: MockMessage = {
      message_id: `u-${turn}`,
      conversation_id: "mock-1",
      role: "user" as const,
      attempt_number: 1,
      status: "done",
      content: String(body.content ?? ""),
      error_code: null,
      error_message: null,
      duration_ms: null,
      model_id: null,
      run_lock_id: null,
      created_at: NOW,
      updated_at: NOW,
    };
    const assistant = assistantMessage(
      `a-${turn}`,
      "已提交图片生成请求，正在处理…",
      { ...(state.task as ImageTask) }
    );
    state.messages.push(user, assistant);
    const sse =
      sseBlock("started", {
        kind: "started",
        conversation_id: "mock-1",
        user_message_id: user.message_id,
        message_id: assistant.message_id,
        attempt_number: 1,
        thinking: null,
      }) +
      sseBlock("image", {
        kind: "image",
        message_id: assistant.message_id,
        task: state.task,
      }) +
      sseBlock("done", {
        kind: "done",
        message_id: assistant.message_id,
        message: assistant,
      });
    eventStreams.set(assistant.message_id, sse);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(runCreated(`run-${assistant.message_id}`, 1, user, assistant)),
    });
  });

  // Issue 02：订阅运行事件（回放已持久化事件；运行终态后结束）
  installRunEventsRoutes(page, eventStreams);

  // 任务轮询状态机：polls 计数推进到终态（失败或成功）。
  await page.route("**/api/chat/conversations/mock-1/image-tasks/**", async (route) => {
    if (!state.task) {
      await route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
      return;
    }
    const url = route.request().url();
    const method = route.request().method();
    if (method === "POST" && url.endsWith("/cancel")) {
      state.cancelled = true;
      state.task = taskForStatus("cancelled");
      const assistant = state.messages.find((m) => m.role === "assistant" && m.image?.task_id === state.task?.task_id);
      if (assistant) assistant.image = { ...(state.task as ImageTask) };
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(state.task) });
      return;
    }
    if (method === "POST" && url.endsWith("/retry")) {
      state.polls = 0;
      state.task = taskForStatus("queued");
      const assistant = state.messages.find((m) => m.role === "assistant" && m.image?.task_id === state.task?.task_id);
      if (assistant) assistant.image = { ...(state.task as ImageTask) };
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(state.task) });
      return;
    }
    state.polls += 1;
    if (state.cancelled) {
      state.task = taskForStatus("cancelled");
    } else if (failWith && state.polls >= succeedAfterPolls) {
      state.task = taskForStatus("failed");
    } else if (!failWith && state.polls >= succeedAfterPolls) {
      state.task = taskForStatus("succeeded");
      const assistant = state.messages.find((m) => m.role === "assistant" && m.image?.task_id === state.task?.task_id);
      if (assistant) {
        assistant.image = { ...(state.task as ImageTask) };
        assistant.content = "图片生成完成。";
      }
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(state.task) });
  });

  // 资产面：查询 / 替代文本修改 / 删除 / 图片字节。
  await page.route("**/api/chat/conversations/mock-1/image-assets/asset-1", async (route) => {
    const request = route.request();
    if (request.method() === "DELETE") {
      state.deleted = true;
      const assistant = state.messages.find((m) => m.role === "assistant" && m.image?.asset_id === "asset-1");
      if (assistant && assistant.image) assistant.image = { ...assistant.image, deleted: true };
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          asset_id: "asset-1",
          removed_versions: state.deleted ? 1 : 0,
          updated_messages: 1,
          object_status: "cleaned",
          deleted_at: NOW,
        }),
      });
      return;
    }
    if (request.method() === "PUT") {
      const body = request.postDataJSON() as { alt_text: string };
      state.altText = body.alt_text;
      state.asset = { ...(state.asset as Record<string, unknown>), alt_text: body.alt_text, alt_text_source: "manual" };
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(state.asset) });
      return;
    }
    if (state.deleted) {
      await route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(state.asset) });
  });

  await page.route("**/api/chat/conversations/mock-1/image-assets/asset-1/versions/*/image", async (route) => {
    if (state.deleted) {
      await route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "image/png",
      headers: { "Cache-Control": "private, max-age=0, no-store" },
      body: Buffer.from(PNG_BYTES, "base64"),
    });
  });

  return { sentBodies: () => sentBodies };
}

async function openImageDialog(page: Page): Promise<void> {
  // 「+」菜单 → 图片生成
  await page.getByRole("button", { name: "更多功能" }).click();
  await page.getByRole("menuitem", { name: "图片生成" }).click();
  await expect(page.getByTestId("image-prompt-input")).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  const credentials = uniqueCredentials("img31");
  await signUp(page, credentials.username, credentials.qqEmail, "correct-horse-31");
});

test("生成流程：对话框提交 → 任务卡 → 资产卡 → 替代文本修改", async ({ page }) => {
  await installMockImageApi(page, [], { succeedAfterPolls: 2 });
  await page.goto("/chat/mock-1");

  await openImageDialog(page);
  await page.getByTestId("image-prompt-input").fill("一座桥的素描");
  await page.getByTestId("image-submit").click();

  // 任务卡（排队中，消息投影快照）随后轮询推进为资产卡（真实图片字节
  // 渲染）；终态资产卡是流程完成的确定性断言。
  await expect(page.getByTestId("image-asset-card")).toBeVisible({ timeout: 15000 });
  await expect(page.getByTestId("image-asset-preview")).toHaveAttribute(
    "src",
    /image-assets\/asset-1\/versions\/v-1\/image/
  );

  // 替代文本修改：保存后显示 manual 来源。
  await page.getByTestId("image-alt-edit").click();
  await page.getByTestId("image-alt-input").fill("深夜里的跨江大桥素描");
  await page.getByTestId("image-alt-save").click();
  await expect(page.getByText("深夜里的跨江大桥素描")).toBeVisible();
});

test("刷新恢复：历史消息的任务投影直接渲染资产卡", async ({ page }) => {
  const succeededTask: ImageTask = {
    task_id: "task-hist",
    kind: "generate",
    prompt: "一座桥的素描",
    source_version_id: null,
    source_object_id: null,
    model_id: "qwen-image-2.0-pro-2026-06-22",
    status: "succeeded",
    error_code: null,
    error_message: null,
    retryable: false,
    asset_id: "asset-1",
    result_version_id: "v-1",
    deleted: false,
    created_at: NOW,
    updated_at: NOW,
  };
  const initial = [
    {
      message_id: "u-hist",
      conversation_id: "mock-1",
      role: "user" as const,
      attempt_number: 1,
      status: "done",
      content: "生成一张桥的素描",
      error_code: null,
      error_message: null,
      duration_ms: null,
      model_id: null,
      run_lock_id: null,
      created_at: NOW,
      updated_at: NOW,
    },
    assistantMessage("a-hist", "图片生成完成。", succeededTask),
  ];
  await installMockImageApi(page, initial, { succeedAfterPolls: 1 });
  await page.goto("/chat/mock-1");

  // 刷新后从消息投影恢复：资产卡直接渲染（无需重新提交）。
  await expect(page.getByTestId("image-asset-card")).toBeVisible();
  await expect(page.getByTestId("image-asset-preview")).toHaveAttribute(
    "src",
    /image-assets\/asset-1\/versions\/v-1\/image/
  );
});

test("删除：确认对话框显示影响说明，确认后显示已删除", async ({ page }) => {
  const succeededTask: ImageTask = {
    task_id: "task-del",
    kind: "generate",
    prompt: "一座桥的素描",
    source_version_id: null,
    source_object_id: null,
    model_id: "qwen-image-2.0-pro-2026-06-22",
    status: "succeeded",
    error_code: null,
    error_message: null,
    retryable: false,
    asset_id: "asset-1",
    result_version_id: "v-1",
    deleted: false,
    created_at: NOW,
    updated_at: NOW,
  };
  const initial = [
    {
      message_id: "u-del",
      conversation_id: "mock-1",
      role: "user" as const,
      attempt_number: 1,
      status: "done",
      content: "生成一张桥的素描",
      error_code: null,
      error_message: null,
      duration_ms: null,
      model_id: null,
      run_lock_id: null,
      created_at: NOW,
      updated_at: NOW,
    },
    assistantMessage("a-del", "图片生成完成。", succeededTask),
  ];
  await installMockImageApi(page, initial, { succeedAfterPolls: 1 });
  await page.goto("/chat/mock-1");
  await expect(page.getByTestId("image-asset-card")).toBeVisible();

  // 删除确认：影响说明（版本数 + 消息引用），确认后显示已删除。
  await page.getByTestId("image-delete").click();
  await expect(page.getByTestId("image-delete-confirm")).toContainText("1 个版本");
  await page.getByTestId("image-delete-confirm-button").click();
  await expect(page.getByTestId("image-asset-deleted")).toBeVisible();
  await expect(page.getByTestId("image-asset-deleted")).toContainText("该图片已删除");
});

test("失败重试：任务卡显示原因，重试同输入重新入队", async ({ page }) => {
  await installMockImageApi(page, [], {
    succeedAfterPolls: 2,
    failWith: { code: "cloud_failed", message: "云端图片生成失败" },
  });
  await page.goto("/chat/mock-1");

  await openImageDialog(page);
  await page.getByTestId("image-prompt-input").fill("一座桥的素描");
  await page.getByTestId("image-submit").click();

  await expect(page.getByTestId("image-task-card")).toBeVisible();
  await expect(page.getByTestId("image-task-status")).toHaveText("失败", { timeout: 15000 });
  await expect(page.getByTestId("image-task-error")).toContainText("云端图片生成失败");
  // 重试按钮可用（可重试错误），点击后重新排队（挂载轮询立即返回 queued）。
  await page.getByTestId("image-task-retry").click();
  await expect(page.getByTestId("image-task-status")).toHaveText("排队中", { timeout: 3000 });
});

test("取消：运行中任务取消后显示已取消，不发布资产", async ({ page }) => {
  await installMockImageApi(page, [], { succeedAfterPolls: 99 });
  await page.goto("/chat/mock-1");

  await openImageDialog(page);
  await page.getByTestId("image-prompt-input").fill("一座桥的素描");
  await page.getByTestId("image-submit").click();

  await expect(page.getByTestId("image-task-card")).toBeVisible();
  await page.getByTestId("image-task-cancel").click();
  await expect(page.getByTestId("image-task-status")).toHaveText("已取消");
  await expect(page.getByTestId("image-asset-card")).not.toBeVisible();
});

test("图片能力入口始终可用（GQ-04 全局凭据语义）", async ({ page }) => {
  // GQ-04：图片/视频由全局运行凭据驱动，不再按账户探测禁用入口，也不
  // 再请求 /api/auth/key-settings——新账户无任何个人 Qwen 配置即可进入
  // 图片生成对话框，页面不出现密钥页引导文案。
  await installMockImageApi(page);
  await page.goto("/chat/mock-1");

  await page.getByRole("button", { name: "更多功能" }).click();
  const item = page.getByRole("menuitem", { name: "图片生成" });
  await expect(item).toBeEnabled();
  await item.click();
  await expect(page.getByRole("dialog", { name: "图片生成与编辑" })).toBeVisible();
  await expect(page.getByTestId("tool-unavailable-notice")).toHaveCount(0);
  await expect(page.getByText("前往「设置」", { exact: false })).toHaveCount(0);
});
