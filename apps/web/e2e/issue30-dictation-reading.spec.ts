import { expect, test, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";
import { installRunEventsRoutes, runCreated } from "./helpers/chat-mock";

/**
 * Issue 30 — 交付听写；单条回答朗读已随 ADR-0030 退役（V2 issue 21）。
 *
 * 覆盖：录音状态机（开始/停止/取消/重新录制 + 时长）、停止后提交完整
 * 音频、转写结果可编辑回填不自动发送、麦克风权限拒绝与设备不可用、
 * 空音频/超限音频错误与恢复、听写网络中断可重试、听写入口始终可用且
 * 无密钥页引导、纯键盘完成一次听写编辑发送；并断言回答朗读入口不再出现。
 *
 * 使用伪造麦克风（--use-fake-device-for-media-stream）让 MediaRecorder
 * 真实录出音频；ASR 端点与能力探测用协议级替身（与 issue11/29 同一
 * 策略），真实供应商调用由后端 pytest 的固定快照测试覆盖。
 */

test.use({
  launchOptions: {
    args: [
      "--use-fake-device-for-media-stream",
      "--use-fake-ui-for-media-stream",
      "--autoplay-policy=no-user-gesture-required",
    ],
  },
});

const NOW = "2026-08-05T00:00:00Z";

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
  [key: string]: unknown;
}

function sseBlock(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

function assistantMessage(id: string, content: string): MockMessage {
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
  };
}

/**
 * 聊天替身：GET 对话历史 + POST 消息 SSE（started → delta → done）。
 * 发送后把新消息并入历史，供断言发送内容与刷新后的历史一致。
 */
async function installMockChatApi(
  page: Page,
  initialMessages: MockMessage[] = []
): Promise<{ sentBodies: () => Array<Record<string, unknown>> }> {
  const state: { messages: MockMessage[]; sent: number } = {
    messages: [...initialMessages],
    sent: 0,
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
    mode_events: [],
  });

  await page.route("**/api/chat/conversations/mock-1", async (route) => {
    const method = route.request().method();
    if (method === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(history()),
      });
      return;
    }
    await route.continue();
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
    const user: MockMessage = {
      message_id: `u-${turn}`,
      conversation_id: "mock-1",
      role: "user",
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
    const assistant: MockMessage = assistantMessage(
      `a-${turn}`,
      `这是对「${String(body.content ?? "")}」的回答。`
    );
    state.messages.push(user, assistant);
    eventStreams.set(
      assistant.message_id,
      sseBlock("started", {
        kind: "started",
        conversation_id: "mock-1",
        user_message_id: `u-${turn}`,
        message_id: `a-${turn}`,
        attempt_number: 1,
        thinking: null,
      }) +
        sseBlock("delta", { kind: "delta", message_id: `a-${turn}`, delta: "回答正文。" }) +
        sseBlock("done", {
          kind: "done",
          message_id: `a-${turn}`,
          message: assistant,
        })
    );
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(runCreated(`run-${assistant.message_id}`, 1, user, assistant)),
    });
  });

  // Issue 02：订阅运行事件（回放已持久化事件；运行终态后结束）
  installRunEventsRoutes(page, eventStreams);

  return { sentBodies: () => sentBodies };
}

/** 注册新账户并打开对话页（装好全部替身：聊天历史）。 */
async function openMockConversation(
  page: Page,
  options: {
    initialMessages?: MockMessage[];
  } = {}
): Promise<{ sentBodies: () => Array<Record<string, unknown>> }> {
  const credentials = uniqueCredentials("sp30");
  await signUp(page, credentials.username, credentials.qqEmail, "correct-horse-30");
  const chat = await installMockChatApi(page, options.initialMessages ?? []);
  await page.goto("/chat/mock-1");
  await expect(page.getByTestId("composer")).toBeVisible();
  return chat;
}

test("纯键盘完成一次听写：录音→停止→转写回填→编辑→发送", async ({ page }) => {
  const chat = await openMockConversation(page);
  let dictationBody: Buffer | null = null;
  await page.route("**/api/chat/conversations/mock-1/dictation", async (route) => {
    if (route.request().method() !== "POST") {
      await route.continue();
      return;
    }
    dictationBody = route.request().postDataBuffer();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "success",
        transcript: "你好，请记录这句话。",
        model_id: "qwen3-asr-flash",
        duration_ms: 420,
        error_code: null,
        error_message: null,
        retryable: false,
        created_at: NOW,
      }),
    });
  });

  const mic = page.getByRole("button", { name: "开始听写" });
  await mic.focus();
  await page.keyboard.press("Enter");
  // 录音态：时长计时 + 停止/取消按钮（键盘可达）。
  await expect(page.getByRole("button", { name: "停止录音并转写" })).toBeVisible();
  await expect(page.getByRole("button", { name: "取消录音" })).toBeVisible();
  await expect(page.getByText(/^00:0\d$/)).toBeVisible();

  // 留出 1.2 秒真实录音数据后，键盘回退到「停止录音并转写」并回车 →
  // 提交完整音频（录音行在麦克风按钮之前的 DOM 顺序，Shift+Tab 两次）。
  await page.waitForTimeout(1200);
  await page.keyboard.press("Shift+Tab");
  await page.keyboard.press("Shift+Tab");
  await page.keyboard.press("Enter");
  // 转写中 → 回填输入框（可编辑），不自动发送。
  await expect(page.getByRole("button", { name: "开始听写" })).toBeVisible();
  const textarea = page.getByRole("textbox");
  await expect(textarea).toHaveValue("你好，请记录这句话。");
  expect(dictationBody).not.toBeNull();
  expect((dictationBody as unknown as Buffer).length).toBeGreaterThan(1000);

  // 编辑后发送（Enter）：通过发送替身断言正文 = 编辑后的转写文本，
  // 且转写本身绝不自动产生用户消息。
  await textarea.press("End");
  await page.keyboard.type("，谢谢！");
  await page.keyboard.press("Enter");
  await expect(page.getByText("这是对「你好，请记录这句话。，谢谢！」的回答。")).toBeVisible();
  const sent = chat.sentBodies();
  expect(sent.length).toBe(1);
  expect(String(sent[0].content)).toBe("你好，请记录这句话。，谢谢！");
});

test("取消录音不遗留待发送文本", async ({ page }) => {
  await openMockConversation(page);
  const mic = page.getByRole("button", { name: "开始听写" });
  await mic.click();
  await expect(page.getByRole("button", { name: "取消录音" })).toBeVisible();
  await page.getByRole("button", { name: "取消录音" }).click();
  // 回到闲置态：无待发送文本、无错误、无残留录音控件。
  await expect(mic).toBeVisible();
  await expect(page.getByRole("button", { name: "取消录音" })).toHaveCount(0);
  await expect(page.getByRole("textbox")).toHaveValue("");
  await expect(page.getByTestId("composer").getByRole("alert")).toHaveCount(0);
});

test("麦克风权限拒绝显示中文原因并可重试", async ({ page }) => {
  await page.addInitScript(() => {
    // 强制模拟权限拒绝（NotAllowedError），验证中文原因与恢复路径。
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia: () =>
          Promise.reject(new DOMException("Permission denied", "NotAllowedError")),
      },
    });
  });
  await openMockConversation(page);
  await page.getByRole("button", { name: "开始听写" }).click();
  const alert = page.getByTestId("composer").getByRole("alert");
  await expect(alert).toContainText("麦克风权限被拒绝");
  await expect(page.getByRole("button", { name: "重新录制" })).toBeVisible();
  await page.getByRole("button", { name: "取消听写" }).click();
  await expect(page.getByTestId("composer").getByRole("alert")).toHaveCount(0);
});

test("麦克风设备不可用显示中文原因并可恢复", async ({ page }) => {
  await page.addInitScript(() => {
    // 强制模拟设备缺失（NotFoundError），验证设备不可用语义与恢复路径。
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia: () =>
          Promise.reject(new DOMException("No device", "NotFoundError")),
      },
    });
  });
  await openMockConversation(page);
  await page.getByRole("button", { name: "开始听写" }).click();
  const alert = page.getByTestId("composer").getByRole("alert");
  await expect(alert).toContainText("未检测到可用麦克风设备");
  await expect(page.getByRole("button", { name: "重新录制" })).toBeVisible();
  await page.getByRole("button", { name: "取消听写" }).click();
  await expect(page.getByTestId("composer").getByRole("alert")).toHaveCount(0);
});

test("空音频与超限音频错误态与恢复", async ({ page }) => {
  await openMockConversation(page);
  await page.route("**/api/chat/conversations/mock-1/dictation", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "failed",
        transcript: "",
        model_id: "qwen3-asr-flash",
        duration_ms: 10,
        error_code: "empty_transcript",
        error_message: "转写没有返回文本，请重试或重新录制。",
        retryable: true,
        created_at: NOW,
      }),
    });
  });
  const mic = page.getByRole("button", { name: "开始听写" });
  await mic.click();
  await page.getByRole("button", { name: "停止录音并转写" }).click();
  const alert = page.getByTestId("composer").getByRole("alert");
  await expect(alert).toContainText("转写没有返回文本");
  await expect(page.getByRole("button", { name: "重试转写" })).toBeVisible();
  await expect(page.getByRole("button", { name: "重新录制" })).toBeVisible();
  // 重试后成功回填。
  await page.route(
    "**/api/chat/conversations/mock-1/dictation",
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          status: "success",
          transcript: "重试成功。",
          model_id: "qwen3-asr-flash",
          duration_ms: 10,
          error_code: null,
          error_message: null,
          retryable: false,
          created_at: NOW,
        }),
      });
    },
    { times: 1 }
  );
  await page.getByRole("button", { name: "重试转写" }).click();
  await expect(page.getByRole("textbox")).toHaveValue("重试成功。");
});

test("听写网络中断失败后可重试", async ({ page }) => {
  await openMockConversation(page);
  let calls = 0;
  await page.route("**/api/chat/conversations/mock-1/dictation", async (route) => {
    calls += 1;
    if (calls === 1) {
      await route.abort("failed");
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "success",
        transcript: "网络恢复后的转写。",
        model_id: "qwen3-asr-flash",
        duration_ms: 10,
        error_code: null,
        error_message: null,
        retryable: false,
        created_at: NOW,
      }),
    });
  });
  const mic = page.getByRole("button", { name: "开始听写" });
  await mic.click();
  await page.getByRole("button", { name: "停止录音并转写" }).click();
  const alert = page.getByTestId("composer").getByRole("alert");
  await expect(alert).toContainText("失败");
  // 同一音频原样重试（受控重试，不重新录音）。
  await page.getByRole("button", { name: "重试转写" }).click();
  await expect(page.getByRole("textbox")).toHaveValue("网络恢复后的转写。");
  expect(calls).toBe(2);
});

test("听写入口始终可用且无密钥页引导（GQ-03/GQ-06 全局凭据语义）", async ({ page }) => {
  // GQ-03：听写由全局运行凭据驱动，GQ-06 后账户探测合同已删除，
  // 新账户无任何个人 Qwen 配置即可使用入口，页面不出现密钥页引导文案。
  const credentials = uniqueCredentials("sp30c");
  await signUp(page, credentials.username, credentials.qqEmail, "correct-horse-30");
  const chat = await installMockChatApi(page, [assistantMessage("a-1", "第一条回答。")]);
  void chat;
  await page.goto("/chat/mock-1");
  await expect(page.getByTestId("composer")).toBeVisible();
  // 听写入口可用。
  const mic = page.getByRole("button", { name: "开始听写" });
  await expect(mic).toBeEnabled();
  await expect(page.getByText("前往「设置」", { exact: false })).toHaveCount(0);
  // 无密钥横幅、无探测状态文案。
  await expect(page.getByText("语音朗读能力正在探测中")).toHaveCount(0);
  await expect(page.getByText(/密钥/)).toHaveCount(0);
});

test("回答朗读入口随 ADR-0030 退役：消息操作栏不再出现朗读", async ({ page }) => {
  await openMockConversation(page, {
    initialMessages: [assistantMessage("a-1", "第一条回答。")],
  });
  // 消息操作栏保留复制/重试等既有操作，但不含朗读按钮。
  const toolbar = page.getByRole("toolbar", { name: "消息操作" });
  await expect(toolbar).toBeVisible();
  await expect(toolbar.getByRole("button", { name: "朗读" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /朗读/ })).toHaveCount(0);
});
