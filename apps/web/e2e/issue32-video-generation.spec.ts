import { expect, type Page, test } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

/**
 * Issue 32：文生视频生成端到端测试。
 *
 * 覆盖：对话框提交生成（真实消息流）→ 任务状态卡（排队/提交中/生成中/
 * 成功）→ 资产卡（真实视频字节/说明文字修改/下载）；刷新后从消息投影
 * 恢复；失败重试；取消（取消中 → 已取消）；能力不可用。全部 API 用
 * page.route 替身（状态机推进任务），页面渲染与交互为真实链路。
 */

const NOW = "2026-08-05T08:00:00Z";
const MP4_BYTES =
  "AAAAGGZ0eXBtcDQyAAAAAGlzb21paXNvMmF2YzEAAAAIZnJlZQAAAAD/AAABAAEAAAEAAA==";

function sseBlock(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

interface VideoTask {
  task_id: string;
  prompt: string;
  model_id: string | null;
  status:
    | "queued"
    | "submitting"
    | "generating"
    | "recovery"
    | "succeeded"
    | "failed"
    | "cancelling"
    | "cancelled";
  error_code: string | null;
  error_message: string | null;
  retryable: boolean;
  asset_id: string | null;
  result_object_id: string | null;
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
  video?: VideoTask | null;
}

function assistantMessage(id: string, content: string, video: VideoTask | null = null): MockMessage {
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
    video,
  };
}

/** 能力探测替身：video 能力可配状态（其余固定 available）。 */
async function mockKeySettings(page: Page, videoStatus = "available"): Promise<void> {
  const capability = (id: string, displayName: string, model: string, status: string) => ({
    capability_id: id,
    display_name: displayName,
    model_id: model,
    status,
    message: status === "unavailable" ? "测试原因：能力不可用。" : undefined,
    can_retry: status === "unavailable" || status === "not_probed",
  });
  await page.route("**/api/auth/key-settings", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "configured",
        configured: true,
        key_tail: "…abcd",
        updated_at: NOW,
        capabilities: [
          capability("chat", "核心对话", "qwen3.7-plus-2026-05-26", "available"),
          capability("embedding", "知识库向量化", "text-embedding-v4", "available"),
          capability("asr", "语音转写", "qwen3-asr-flash-2025-09-08", "available"),
          capability("tts", "语音朗读", "qwen3-tts-flash-2025-11-27", "available"),
          capability("image", "图片生成与编辑", "qwen-image-2.0-pro-2026-06-22", "available"),
          capability("video", "视频生成", "wan2.7-t2v-2026-06-12", videoStatus),
        ],
        message: "测试配置。",
        next_step: "测试。",
      }),
    });
  });
}

interface MockVideoApiOptions {
  /** 轮询第几次返回终态（1 = 挂载后立即成功；2 = 等待一个轮询间隔）。 */
  succeedAfterPolls?: number;
  /** 任务最终失败（失败原因可重试）。 */
  failWith?: { code: string; message: string };
  /** 发送视频载荷时服务端返回 409（能力门控拒绝，前端展示中文原因）。 */
  rejectVideoUnavailable?: boolean;
}

/**
 * 视频链路替身：发送（SSE 携带 video 事件）→ 任务轮询状态机 → 资产面。
 * 发送后立即把任务并入消息历史（助手消息带 queued 投影）；轮询按调用
 * 次数推进：queued → submitting → generating → succeeded（或按 failWith
 * 置 failed）；取消返回 cancelling，下一轮轮询收敛为 cancelled。
 */
async function installMockVideoApi(
  page: Page,
  initialMessages: MockMessage[] = [],
  options: MockVideoApiOptions = {}
): Promise<{ sentBodies: () => Array<Record<string, unknown>> }> {
  const { succeedAfterPolls = 2, failWith, rejectVideoUnavailable = false } = options;
  const state: {
    messages: MockMessage[];
    sent: number;
    task: VideoTask | null;
    polls: number;
    cancelled: boolean;
    cancelPolls: number;
    asset: Record<string, unknown> | null;
    deleted: boolean;
    description: string;
  } = {
    messages: [...initialMessages],
    sent: 0,
    task: null,
    polls: 0,
    cancelled: false,
    cancelPolls: 0,
    asset: {
      asset_id: "asset-1",
      description: "由提示词「一条静谧的河」生成的视频",
      description_source: "prompt",
      object_id: "obj-1",
      prompt: "一条静谧的河",
      model_id: "wan2.7-t2v-2026-06-12",
      cloud_task_id: "cloud-1",
      media_type: "video/mp4",
      content_length: 68,
      deleted: false,
      created_at: NOW,
      updated_at: NOW,
    },
    deleted: false,
    description: "由提示词「一条静谧的河」生成的视频",
  };

  const taskForStatus = (status: VideoTask["status"]): VideoTask => {
    const base = state.task as VideoTask;
    return {
      ...base,
      status,
      updated_at: NOW,
      error_code: status === "failed" ? (failWith?.code ?? "cloud_failed") : null,
      error_message: status === "failed" ? (failWith?.message ?? "云端生成失败") : null,
      retryable: status === "failed",
      asset_id: status === "succeeded" ? "asset-1" : base.asset_id,
      result_object_id: status === "succeeded" ? "obj-1" : base.result_object_id,
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
    if (rejectVideoUnavailable) {
      // 服务端能力门控：视频能力不可用时拒绝提交并说明原因。
      await route.fulfill({
        status: 409,
        contentType: "application/json",
        body: JSON.stringify({
          detail: {
            error: "capability_unavailable",
            message: "视频生成能力当前不可用：测试原因：能力不可用。请前往「设置」重新探测。",
          },
        }),
      });
      return;
    }
    state.sent += 1;
    const turn = state.sent;
    const video = body.video as { prompt: string } | undefined;
    const taskId = `task-${turn}`;
    state.task = {
      task_id: taskId,
      prompt: String(video?.prompt ?? body.content ?? ""),
      model_id: null,
      status: "queued",
      error_code: null,
      error_message: null,
      retryable: false,
      asset_id: null,
      result_object_id: null,
      deleted: false,
      created_at: NOW,
      updated_at: NOW,
    };
    state.asset = {
      asset_id: "asset-1",
      description: state.description,
      description_source: "prompt",
      object_id: "obj-1",
      prompt: String(video?.prompt ?? ""),
      model_id: "wan2.7-t2v-2026-06-12",
      cloud_task_id: "cloud-1",
      media_type: "video/mp4",
      content_length: 68,
      deleted: false,
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
      "已提交视频生成请求，正在处理…",
      { ...(state.task as VideoTask) }
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
      sseBlock("video", {
        kind: "video",
        message_id: assistant.message_id,
        task: state.task,
      }) +
      sseBlock("done", {
        kind: "done",
        message_id: assistant.message_id,
        message: assistant,
      });
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: sse,
    });
  });

  // 任务轮询状态机：polls 计数推进（提交中 → 生成中 → 终态）。
  await page.route("**/api/chat/conversations/mock-1/video-tasks/**", async (route) => {
    if (!state.task) {
      await route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
      return;
    }
    const url = route.request().url();
    const method = route.request().method();
    if (method === "POST" && url.endsWith("/cancel")) {
      state.cancelled = true;
      state.cancelPolls = 0;
      state.task = taskForStatus("cancelling");
      const assistant = state.messages.find((m) => m.role === "assistant" && m.video?.task_id === state.task?.task_id);
      if (assistant) assistant.video = { ...(state.task as VideoTask) };
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(state.task) });
      return;
    }
    if (method === "POST" && url.endsWith("/retry")) {
      state.polls = 0;
      state.task = taskForStatus("queued");
      const assistant = state.messages.find((m) => m.role === "assistant" && m.video?.task_id === state.task?.task_id);
      if (assistant) assistant.video = { ...(state.task as VideoTask) };
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(state.task) });
      return;
    }
    state.polls += 1;
    if (state.cancelled) {
      // 取消后保持一轮「取消中」，再收敛为「已取消」（覆盖八态呈现）。
      state.cancelPolls += 1;
      state.task = taskForStatus(state.cancelPolls >= 2 ? "cancelled" : "cancelling");
    } else if (failWith && state.polls >= succeedAfterPolls) {
      state.task = taskForStatus("failed");
    } else if (!failWith && state.polls >= succeedAfterPolls) {
      state.task = taskForStatus("succeeded");
      const assistant = state.messages.find((m) => m.role === "assistant" && m.video?.task_id === state.task?.task_id);
      if (assistant) {
        assistant.video = { ...(state.task as VideoTask) };
        assistant.content = "视频生成完成。";
      }
    } else if (state.polls === 2) {
      state.task = taskForStatus("submitting");
    } else if (state.polls >= 3) {
      state.task = taskForStatus("generating");
    }
    // 第 1 轮轮询保持 queued：重试后重新排队的状态可确定性断言。
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(state.task) });
  });

  // 资产面：查询 / 说明文字修改 / 删除 / 视频字节。
  await page.route("**/api/chat/conversations/mock-1/video-assets/asset-1", async (route) => {
    const request = route.request();
    if (request.method() === "DELETE") {
      state.deleted = true;
      const assistant = state.messages.find((m) => m.role === "assistant" && m.video?.asset_id === "asset-1");
      if (assistant && assistant.video) assistant.video = { ...assistant.video, deleted: true };
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          asset_id: "asset-1",
          removed_objects: state.deleted ? 1 : 0,
          updated_messages: 1,
          object_status: "cleaned",
          deleted_at: NOW,
        }),
      });
      return;
    }
    if (request.method() === "PUT") {
      const body = request.postDataJSON() as { description: string };
      state.description = body.description;
      state.asset = {
        ...(state.asset as Record<string, unknown>),
        description: body.description,
        description_source: "manual",
      };
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(state.asset) });
      return;
    }
    if (state.deleted) {
      await route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(state.asset) });
  });

  // 预览与下载（?download=1）共用同一字节路由（通配查询串）。
  await page.route("**/api/chat/conversations/mock-1/video-assets/asset-1/video*", async (route) => {
    if (state.deleted) {
      await route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "video/mp4",
      headers: { "Cache-Control": "private, max-age=0, no-store" },
      body: Buffer.from(MP4_BYTES, "base64"),
    });
  });

  return { sentBodies: () => sentBodies };
}

async function openVideoDialog(page: Page): Promise<void> {
  // 「+」菜单 → 视频生成
  await page.getByRole("button", { name: "更多功能" }).click();
  await page.getByRole("menuitem", { name: "视频生成" }).click();
  await expect(page.getByTestId("video-prompt-input")).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  const credentials = uniqueCredentials("vid32");
  await signUp(page, credentials.username, credentials.qqEmail, "correct-horse-32");
});

test("生成流程：对话框提交 → 任务卡 → 资产卡 → 说明文字修改", async ({ page }) => {
  await mockKeySettings(page);
  await installMockVideoApi(page, [], { succeedAfterPolls: 2 });
  await page.goto("/chat/mock-1");

  await openVideoDialog(page);
  await page.getByTestId("video-prompt-input").fill("一条静谧的河");
  await page.getByTestId("video-submit").click();

  // 任务卡（排队中）随后轮询推进为资产卡（真实视频字节渲染）；终态资产
  // 卡是流程完成的确定性断言。
  await expect(page.getByTestId("video-asset-card")).toBeVisible({ timeout: 15000 });
  await expect(page.getByTestId("video-asset-preview")).toHaveAttribute(
    "src",
    /video-assets\/asset-1\/video/
  );

  // 说明文字修改：保存后显示 manual 来源。
  await page.getByTestId("video-description-edit").click();
  await page.getByTestId("video-description-input").fill("晨雾中的河流，画面缓缓推进。");
  await page.getByTestId("video-description-save").click();
  await expect(page.getByText("晨雾中的河流，画面缓缓推进。")).toBeVisible();

  // 下载：点击下载链接触发真实下载事件（附件响应经 mock 提供）。
  const downloadPromise = page.waitForEvent("download");
  await page.getByTestId("video-download").click();
  const download = await downloadPromise;
  expect(download.url()).toContain("/video-assets/asset-1/video?download=1");
});

test("刷新恢复：历史消息的任务投影直接渲染资产卡", async ({ page }) => {
  await mockKeySettings(page);
  const succeededTask: VideoTask = {
    task_id: "task-hist",
    prompt: "一条静谧的河",
    model_id: "wan2.7-t2v-2026-06-12",
    status: "succeeded",
    error_code: null,
    error_message: null,
    retryable: false,
    asset_id: "asset-1",
    result_object_id: "obj-1",
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
      content: "生成一条河的视频",
      error_code: null,
      error_message: null,
      duration_ms: null,
      model_id: null,
      run_lock_id: null,
      created_at: NOW,
      updated_at: NOW,
    },
    assistantMessage("a-hist", "视频生成完成。", succeededTask),
  ];
  await installMockVideoApi(page, initial, { succeedAfterPolls: 1 });
  await page.goto("/chat/mock-1");

  // 刷新后从消息投影恢复：资产卡直接渲染（无需重新提交）。
  await expect(page.getByTestId("video-asset-card")).toBeVisible();
  await expect(page.getByTestId("video-asset-preview")).toHaveAttribute(
    "src",
    /video-assets\/asset-1\/video/
  );
});

test("删除：确认对话框显示影响说明，确认后显示已删除", async ({ page }) => {
  await mockKeySettings(page);
  const succeededTask: VideoTask = {
    task_id: "task-del",
    prompt: "一条静谧的河",
    model_id: "wan2.7-t2v-2026-06-12",
    status: "succeeded",
    error_code: null,
    error_message: null,
    retryable: false,
    asset_id: "asset-1",
    result_object_id: "obj-1",
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
      content: "生成一条河的视频",
      error_code: null,
      error_message: null,
      duration_ms: null,
      model_id: null,
      run_lock_id: null,
      created_at: NOW,
      updated_at: NOW,
    },
    assistantMessage("a-del", "视频生成完成。", succeededTask),
  ];
  await installMockVideoApi(page, initial, { succeedAfterPolls: 1 });
  await page.goto("/chat/mock-1");
  await expect(page.getByTestId("video-asset-card")).toBeVisible();

  // 删除确认：影响说明（本地对象 + 消息引用），确认后显示已删除。
  await page.getByTestId("video-delete").click();
  await expect(page.getByTestId("video-delete-confirm")).toContainText("不可撤销");
  await page.getByTestId("video-delete-confirm-button").click();
  await expect(page.getByTestId("video-asset-deleted")).toBeVisible();
  await expect(page.getByTestId("video-asset-deleted")).toContainText("该视频已删除");
});

test("失败重试：任务卡显示原因，重试同输入重新入队", async ({ page }) => {
  await mockKeySettings(page);
  await installMockVideoApi(page, [], {
    succeedAfterPolls: 2,
    failWith: { code: "cloud_failed", message: "云端视频生成失败" },
  });
  await page.goto("/chat/mock-1");

  await openVideoDialog(page);
  await page.getByTestId("video-prompt-input").fill("一条静谧的河");
  await page.getByTestId("video-submit").click();

  await expect(page.getByTestId("video-task-card")).toBeVisible();
  await expect(page.getByTestId("video-task-status")).toHaveText("失败", { timeout: 15000 });
  await expect(page.getByTestId("video-task-error")).toContainText("云端视频生成失败");
  // 重试按钮可用（可重试错误），点击后重新排队（挂载轮询立即返回 queued）。
  await page.getByTestId("video-task-retry").click();
  await expect(page.getByTestId("video-task-status")).toHaveText("排队中", { timeout: 3000 });
});

test("取消：运行中任务取消后经取消中收敛为已取消，不发布资产", async ({ page }) => {
  await mockKeySettings(page);
  await installMockVideoApi(page, [], { succeedAfterPolls: 99 });
  await page.goto("/chat/mock-1");

  await openVideoDialog(page);
  await page.getByTestId("video-prompt-input").fill("一条静谧的河");
  await page.getByTestId("video-submit").click();

  await expect(page.getByTestId("video-task-card")).toBeVisible();
  await page.getByTestId("video-task-cancel").click();
  // 取消接口返回「取消中」；下一轮轮询收敛为「已取消」。
  await expect(page.getByTestId("video-task-status")).toHaveText("取消中", { timeout: 3000 });
  await expect(page.getByTestId("video-task-status")).toHaveText("已取消", { timeout: 15000 });
  await expect(page.getByTestId("video-asset-card")).not.toBeVisible();
});

test("能力不可用：菜单入口明确停用并说明原因", async ({ page }) => {
  await mockKeySettings(page, "unavailable");
  await installMockVideoApi(page);
  await page.goto("/chat/mock-1");

  // 视频能力不可用（探测快照）：菜单入口点击给出中文原因，不打开对话框。
  await page.getByRole("button", { name: "更多功能" }).click();
  await page.getByRole("menuitem", { name: "视频生成" }).click();
  await expect(page.getByTestId("tool-unavailable-notice")).toContainText(
    "视频生成能力当前不可用"
  );
  await expect(page.getByRole("dialog", { name: "视频生成" })).not.toBeVisible();
});
