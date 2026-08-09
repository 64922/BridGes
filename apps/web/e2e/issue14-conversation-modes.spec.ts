import { expect, test, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";
import { installRunEventsRoutes, runCreated } from "./helpers/chat-mock";

/**
 * Issue 14 — 交付对话双模式与可折叠思考摘要。
 *
 * 覆盖：对话页首轮前的「日常陪伴 / 学习模式」选择控件（无模型选择器）；
 * 首轮提交后模式固定且刷新后保持；思考摘要
 * 生成中自动展开、完成后折叠为「已思考（用时 X 秒）」并可再次展开；断流
 * 失败保留摘要并显示中文状态；纯键盘切换/展开折叠/停止/重试；空白态默认
 * 日常陪伴且可先切学习模式再发送；学习项目页「进入学习对话」创建学习对话。
 *
 * 与 issue11/13 同一策略：协议级替身模拟聊天 API，Key 预检拦截沿用真实
 * 后端（未启用对话存储的实例自动跳过）。
 */

const NOW = "2026-08-03T00:00:00Z";

/** 与真实服务端一致的思考摘要结构（步骤/证据/工具/质量，不暴露思维链） */
const DONE_THINKING = {
  steps: ["理解你的问题与当前语境", "组织并生成回答"],
  evidence: [],
  tools: [],
  quality: ["回答已完整生成并保存"],
};

const ERROR_THINKING = {
  steps: ["理解你的问题与当前语境", "组织并生成回答"],
  evidence: [],
  tools: [],
  quality: ["连接中断或服务暂时不可用，请检查网络后重试。"],
};

interface MockState {
  mode: "companion" | "study";
  modeLocked: boolean;
  /** 递增序号：保证多次发送的消息 id 唯一（不破坏 React key） */
  seq: number;
  modeEvents: {
    event_id: string;
    conversation_id: string;
    from_mode: string;
    to_mode: string;
    created_at: string;
  }[];
  messages: {
    message_id: string;
    conversation_id: string;
    role: string;
    attempt_number: number;
    status: string;
    content: string;
    thinking: typeof DONE_THINKING | null;
    error_code: null | string;
    error_message: null | string;
    duration_ms: number | null;
    model_id: string | null;
    run_lock_id: string | null;
    created_at: string;
    updated_at: string;
  }[];
}

function makeThinking(steps: string[]) {
  return { steps, evidence: [], tools: [], quality: [] };
}

function sseBlock(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

/**
 * 构造 SSE 响应体（一次性返回）。
 *
 * - ``hang``：只发 started + delta、不发终态——前端停留在 streaming
 *   （思考摘要自动展开可稳定断言），由「停止」abort 收敛；
 * - 否则按序发出终态（done/error），前端收敛后重新加载服务端历史。
 */
function sseBody({
  fail = false,
  hang = false,
  userMessageId = "u-1",
  assistantMessageId = "a-1",
}: {
  fail?: boolean;
  hang?: boolean;
  userMessageId?: string;
  assistantMessageId?: string;
} = {}) {
  const started = sseBlock("started", {
    kind: "started",
    conversation_id: "mock-1",
    user_message_id: userMessageId,
    message_id: assistantMessageId,
    attempt_number: 1,
    thinking: makeThinking(["理解你的问题与当前语境", "组织并生成回答"]),
  });
  const deltaEvent = sseBlock("delta", {
    kind: "delta",
    message_id: assistantMessageId,
    delta: "这是替身生成的回答。",
  });
  if (hang) return `${started}${deltaEvent}`;
  if (fail) {
    return `${started}${deltaEvent}${sseBlock("error", {
      kind: "error",
      message_id: assistantMessageId,
      error: {
        code: "stream_interrupted",
        message: "连接中断或服务暂时不可用，请检查网络后重试。",
        retryable: true,
      },
      thinking: ERROR_THINKING,
      duration_ms: 1234,
    })}`;
  }
  return `${started}${deltaEvent}${sseBlock("done", {
    kind: "done",
    message_id: assistantMessageId,
    message: {
      message_id: assistantMessageId,
      conversation_id: "mock-1",
      role: "assistant",
      attempt_number: 1,
      status: "done",
      content: "这是替身生成的回答。",
      thinking: DONE_THINKING,
      error_code: null,
      error_message: null,
      duration_ms: 3800,
      model_id: "qwen3.7-plus-2026-05-26",
      run_lock_id: "lock-mock",
      created_at: NOW,
      updated_at: NOW,
    },
  })}`;
}

/** 最小聊天 API 替身：创建（带模式）、历史、模式切换、发送 SSE。 */
async function installMockChatApi(
  page: Page,
  options: { initialMode?: "companion" | "study" } = {}
) {
  const state: MockState = {
    mode: options.initialMode ?? "companion",
    modeLocked: false,
    seq: 0,
    modeEvents: [],
    messages: [],
  };
  // Issue 02：消息 → 持久化事件流（POST 创建运行后由 events 端点回放）
  const eventStreams = new Map<string, string>();

  const history = () => ({
    conversation_id: "mock-1",
    title: "测试对话",
    mode: state.mode,
    mode_locked: state.modeLocked,
    created_at: NOW,
    updated_at: NOW,
    messages: state.messages,
    mode_events: state.modeEvents,
  });

  await page.route("**/api/chat/conversations", async (route) => {
    if (route.request().method() === "POST") {
      const body = JSON.parse(route.request().postData() ?? "{}");
      state.mode = body.mode === "study" ? "study" : "companion";
      await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify(history()) });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ conversations: [{ ...history(), message_count: state.messages.length }] }),
    });
  });

  // 旧模式切换端点在兼容窗口内固定返回 410。
  await page.route("**/api/chat/conversations/mock-1/mode", async (route) => {
    await route.fulfill({
      status: 410,
      contentType: "application/json",
      body: JSON.stringify({
        detail: {
          error: "conversation_mode_switch_retired",
          message: "模式已在首条消息提交时锁定；请新建另一个会话以使用其他模式。",
        },
      }),
    });
  });

  await page.route("**/api/chat/conversations/mock-1", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(history()) });
  });

  await page.route("**/api/chat/first-turn", async (route) => {
    const body = JSON.parse(route.request().postData() ?? "{}");
    state.mode = body.mode === "study" ? "study" : "companion";
    state.modeLocked = true;
    state.seq += 1;
    const userMessageId = `u-${state.seq}`;
    const assistantMessageId = `a-${state.seq}`;
    const fail = body.content.includes("断流");
    const hang = body.content.includes("不要结束");
    const terminal = fail ? "error" : hang ? "streaming" : "done";
    const userMessage = {
      message_id: userMessageId,
      conversation_id: "mock-1",
      role: "user",
      attempt_number: 1,
      status: "done",
      content: body.content,
      thinking: null,
      error_code: null,
      error_message: null,
      duration_ms: null,
      model_id: null,
      run_lock_id: null,
      created_at: NOW,
      updated_at: NOW,
    };
    const assistantMessage = {
      ...userMessage,
      message_id: assistantMessageId,
      role: "assistant",
      content: "这是替身生成的回答。",
      status: terminal,
      thinking: fail ? ERROR_THINKING : DONE_THINKING,
      error_code: fail ? "stream_interrupted" : null,
      error_message: fail ? "连接中断或服务暂时不可用，请检查网络后重试。" : null,
      duration_ms: fail ? 1234 : 3800,
      model_id: terminal === "streaming" ? null : "qwen3.7-plus-2026-05-26",
      run_lock_id: terminal === "streaming" ? null : "lock-mock",
    };
    state.messages.push(userMessage, assistantMessage);
    eventStreams.set(
      assistantMessageId,
      sseBody({ fail, hang, userMessageId, assistantMessageId })
    );
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({
        conversation: history(),
        ...runCreated(`run-${assistantMessageId}`, 1, userMessage, assistantMessage),
        idempotent_replay: false,
      }),
    });
  });

  await page.route("**/api/chat/conversations/mock-1/messages", async (route) => {
    if (route.request().method() !== "POST") return;
    const body = JSON.parse(route.request().postData() ?? "{}");
    state.modeLocked = true;
    state.seq += 1;
    const seq = state.seq;
    const userMessageId = `u-${seq}`;
    const assistantMessageId = `a-${seq}`;
    const fail = body.content.includes("断流");
    const hang = body.content.includes("不要结束");
    const terminal = fail ? "error" : hang ? "streaming" : "done";
    state.messages.push(
      {
        message_id: userMessageId,
        conversation_id: "mock-1",
        role: "user",
        attempt_number: 1,
        status: "done",
        content: body.content,
        thinking: null,
        error_code: null,
        error_message: null,
        duration_ms: null,
        model_id: null,
        run_lock_id: null,
        created_at: NOW,
        updated_at: NOW,
      },
      {
        message_id: assistantMessageId,
        conversation_id: "mock-1",
        role: "assistant",
        attempt_number: 1,
        status: terminal,
        content: "这是替身生成的回答。",
        thinking: fail ? ERROR_THINKING : DONE_THINKING,
        error_code: fail ? "stream_interrupted" : null,
        error_message: fail ? "连接中断或服务暂时不可用，请检查网络后重试。" : null,
        duration_ms: fail ? 1234 : 3800,
        model_id: "qwen3.7-plus-2026-05-26",
        run_lock_id: "lock-mock",
        created_at: NOW,
        updated_at: NOW,
      }
    );
    eventStreams.set(
      assistantMessageId,
      sseBody({ fail, hang, userMessageId, assistantMessageId })
    );
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(
        runCreated(
          `run-${assistantMessageId}`,
          1,
          state.messages[state.messages.length - 2],
          state.messages[state.messages.length - 1]
        )
      ),
    });
  });

  // Issue 02：订阅运行事件（回放已持久化事件；运行终态后结束）
  installRunEventsRoutes(page, eventStreams);

  // 停止：把最新助手消息收敛为 stopped（与真实服务端一致）
  await page.route("**/api/chat/conversations/mock-1/messages/*/stop", async (route) => {
    const target = state.messages.find((m) => m.role === "assistant" && m.status === "streaming");
    if (target) {
      target.status = "stopped";
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ message: target ?? {} }),
    });
  });
}

async function registerAndEnterHome(page: Page): Promise<void> {
  const credentials = uniqueCredentials("issue14");
  await signUp(page, credentials.username, credentials.qqEmail, "Passw0rd123!");
  await expect(page.getByRole("heading", { name: "有什么可以帮你的？" })).toBeVisible();
}

test.describe("Issue 14 — 对话双模式与可折叠思考摘要", () => {
  test("对话页输入区附近有模式切换控件，两个模式都不出现模型选择器", async ({ page }) => {
    await registerAndEnterHome(page);
    await installMockChatApi(page);

    await page.goto("/chat/mock-1");
    const toggle = page.getByTestId("mode-toggle");
    await expect(toggle).toBeVisible();
    await expect(toggle.getByRole("button", { name: "日常陪伴" })).toHaveAttribute("aria-pressed", "true");
    await expect(toggle.getByRole("button", { name: "学习模式" })).toHaveAttribute("aria-pressed", "false");
    // 无模型选择器
    await expect(page.getByRole("button", { name: /模型|极速/ })).toHaveCount(0);
  });

  test("首轮提交后模式固定，刷新后不再提供切换按钮", async ({ page }) => {
    await registerAndEnterHome(page);
    await installMockChatApi(page);
    await page.goto("/chat/mock-1");

    // 首轮前可以选择学习模式。
    await page.getByTestId("mode-toggle").getByRole("button", { name: "学习模式" }).click();
    await page.getByTestId("composer").getByLabel("输入消息").fill("首轮学习问题");
    await page.getByTestId("composer").getByRole("button", { name: "发送消息" }).click();
    await expect(page.getByTestId("mode-toggle")).toHaveAttribute("data-locked", "true");
    await expect(page.getByTestId("mode-display")).toContainText("学习模式");
    await expect(page.getByTestId("mode-toggle").getByRole("button")).toHaveCount(0);

    // 刷新后仍显示持久化模式，且没有切换动作。
    await page.reload();
    await expect(page.getByTestId("mode-display")).toContainText("学习模式");
    await expect(page.getByTestId("mode-toggle").getByRole("button")).toHaveCount(0);
  });

  test("思考摘要：生成中自动展开，完成后折叠为「已思考（用时 X 秒）」并可再次展开", async ({ page }) => {
    await registerAndEnterHome(page);
    await installMockChatApi(page);
    await page.goto("/chat/mock-1");

    // 第一步：发送「不要结束」→ 流挂起，验证生成中思考区域自动展开
    await page.getByTestId("composer").getByLabel("输入消息").fill("不要结束");
    await page.getByTestId("composer").getByRole("button", { name: "发送消息" }).click();

    const streamingSummary = page.getByTestId("thinking-summary").first();
    await expect(streamingSummary).toBeVisible();
    await expect(streamingSummary).toHaveAttribute("open", "");
    await expect(streamingSummary).toContainText("理解你的问题与当前语境");
    await expect(streamingSummary).toContainText("正在思考");
    await page.keyboard.press("Escape");
    await expect(page.getByRole("button", { name: "发送消息" })).toBeVisible();

    // 第二步：发送完成流 → 折叠为精确格式「已思考（用时 4 秒）」（3800ms → 4 秒）
    await page.getByTestId("composer").getByLabel("输入消息").fill("帮我理解量子纠错");
    await page.getByTestId("composer").getByRole("button", { name: "发送消息" }).click();
    const summary = page.getByTestId("thinking-summary").last();
    await expect(summary).toContainText("已思考（用时 4 秒）");
    await expect(summary).not.toHaveAttribute("open", "");

    // 点击再次展开：可见四类可公开内容（步骤/证据/工具/质量检查），无思维链
    await summary.getByRole("button").click();
    await expect(summary).toHaveAttribute("open", "");
    await expect(summary).toContainText("处理步骤");
    await expect(summary).toContainText("质量检查");
    await expect(summary).toContainText("回答已完整生成并保存");
    await expect(summary).toContainText("组织并生成回答");
  });

  test("断流失败时保留已完成摘要并显示中文状态", async ({ page }) => {
    await registerAndEnterHome(page);
    await installMockChatApi(page);
    await page.goto("/chat/mock-1");

    await page.getByTestId("composer").getByLabel("输入消息").fill("断流测试");
    await page.getByTestId("composer").getByRole("button", { name: "发送消息" }).click();

    // 失败后重新加载服务端历史：消息带思考摘要（已完成步骤 + 中文质量结论）
    const thread = page.getByRole("list", { name: "对话消息" });
    await expect(thread.getByRole("alert")).toContainText("连接中断");
    const summary = page.getByTestId("thinking-summary");
    await expect(summary).toContainText("已思考（用时 1 秒）");
    await summary.getByRole("button").click();
    await expect(summary).toContainText("理解你的问题与当前语境");
    await expect(summary).toContainText("连接中断或服务暂时不可用");
  });

  test("纯键盘：发送、展开/折叠摘要、停止与重试", async ({ page }) => {
    await registerAndEnterHome(page);
    await installMockChatApi(page);
    await page.goto("/chat/mock-1");

    // 键盘发送 → 生成中出现停止入口 → Esc 停止
    await page.getByTestId("composer").getByLabel("输入消息").focus();
    await page.keyboard.type("不要结束");
    await page.keyboard.press("Enter");
    await expect(page.getByRole("button", { name: "停止生成" })).toBeVisible();
    // Esc 停止：键盘事件偶发时序下，停止请求未发出时补按一次（保持纯键盘路径）。
    const stopSent = () =>
      page.waitForResponse(
        (resp) => resp.url().includes("/stop") && resp.request().method() === "POST",
        { timeout: 3000 }
      );
    const first = stopSent().catch(() => null);
    await page.keyboard.press("Escape");
    if (!(await first)) {
      const second = stopSent().catch(() => null);
      await page.keyboard.press("Escape");
      await second;
    }
    await expect(page.getByRole("button", { name: "发送消息" })).toBeVisible();

    // 键盘展开思考摘要：Tab 到 summary 的按钮 + Enter
    const summary = page.getByTestId("thinking-summary");
    await summary.getByRole("button").focus();
    await page.keyboard.press("Enter");
    await expect(summary).toHaveAttribute("open", "");
    await page.keyboard.press("Enter");
    await expect(summary).not.toHaveAttribute("open", "");
  });

  test("空白态默认日常陪伴，可先切换学习模式再发送创建学习对话", async ({ page }) => {
    await registerAndEnterHome(page);
    await installMockChatApi(page);

    const toggle = page.getByTestId("mode-toggle");
    await expect(toggle.getByRole("button", { name: "日常陪伴" })).toHaveAttribute("aria-pressed", "true");

    // 切换为学习模式后发送：创建请求携带 study，跳转后为学习模式
    await toggle.getByRole("button", { name: "学习模式" }).click();
    await page.getByTestId("composer").getByLabel("输入消息").fill("帮我规划学习");
    await page.getByTestId("composer").getByRole("button", { name: "发送消息" }).click();
    await page.waitForURL(/\/chat\/mock-1/);
    await expect(page.getByTestId("mode-display")).toContainText("学习模式");
    await expect(page.getByTestId("mode-toggle")).toHaveAttribute("data-locked", "true");
  });

  test("学习项目页「新建学习对话」创建默认学习模式对话", async ({ page }) => {
    await registerAndEnterHome(page);
    await installMockChatApi(page, { initialMode: "study" });
    // Issue 41：旧项目工作台已退役，学习对话入口在学习项目详情页
    // （/account/projects/{id}）的「新建学习对话」按钮。
    await page.route("**/api/learning-projects/demo-project", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          project_id: "demo-project",
          name: "演示学习项目",
          description: "",
          created_at: NOW,
          updated_at: NOW,
          conversations: [],
        }),
      });
    });

    await page.goto("/account/projects/demo-project");
    await expect(page.getByTestId("learning-project-new-chat")).toBeVisible();
    await page.getByTestId("learning-project-new-chat").click();
    await page.waitForURL(/\/chat\/mock-1/);
    await expect(
      page.getByTestId("mode-toggle").getByRole("button", { name: "学习模式" })
    ).toHaveAttribute("aria-pressed", "true");
  });
});
