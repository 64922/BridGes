import { expect, test, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";
import { installRunEventsRoutes, runCreated } from "./helpers/chat-mock";

/**
 * Issue 28 — 交付原创净室 bridges-humanizer SKILL。
 *
 * 覆盖：两种模式的「+」菜单与空白对话建议卡均以原创图标进入真实任务
 * 对话框；改写路径（粘贴文本 / 当前账户文件）与生成路径（主题/受众/
 * 体裁/渠道/硬约束）提交走真实消息流；过程卡中文五态（loading/error/
 * recovery）；结果卡展示输出合同五要素（最终文本、修改明细、每项理由、
 * 事实核查、未决问题）与事实锁比较；失败后可从原任务重试且输入保留。
 *
 * 消息发送路径用协议级替身（与 issue11/27 同一策略）；Key 预检沿用
 * 真实后端。
 */

const NOW = "2026-08-05T00:00:00Z";
const PASSWORD = "correct-horse-28";

interface MockMessage {
  message_id: string;
  conversation_id: string;
  role: string;
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
  skill?: Record<string, unknown> | null;
  humanizer?: Record<string, unknown> | null;
  thinking?: Record<string, unknown> | null;
  [key: string]: unknown;
}

function sseBlock(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

/** 完整的人味化结果投影（输出合同五要素 + 事实锁比较 + 步骤轨迹）。 */
function mockHumanizerResult(
  overrides: Partial<Record<string, unknown>> = {}
): Record<string, unknown> {
  return {
    task_id: "a-1",
    skill_id: "bridges-humanizer",
    skill_version: "1.0.0",
    path: "rewrite",
    genre: "popular_science",
    contract: {
      path: "rewrite",
      genre: "popular_science",
      source_text: "测试原文",
      audience: "普通读者",
      channel: "公众号",
    },
    status: "done",
    output: {
      final_text: "这是改写后的最终文本。",
      edits: [
        {
          edit_id: "ed-1",
          kind: "rewrite",
          original: "研究显示",
          revised: "数据显示",
          reason: "科普文案规则：使用面向读者的表述。",
          genre_rule: "ps_core_concept",
        },
      ],
      fact_check: [
        {
          item: "25 μmol·m⁻²·s⁻¹",
          result: "已核实",
          evidence: "与原文一致。",
        },
      ],
      open_questions: ["田间高温胁迫下的实际速率仍待验证。"],
    },
    fact_lock_check: {
      check_id: "flc-1",
      source_text: "原文 vs 改写结果",
      entries: [],
      blocking_conflicts: [],
      needs_human: [],
      passed: true,
    },
    references: [],
    genre_check: [
      "✓ 核心概念的一句话定义：已出现",
      "✓ 至少一个类比：已出现",
    ],
    process_state: "done",
    process_steps: ["解析任务契约", "提取事实锁", "按体裁规则生成", "确定性复核"],
    error_code: null,
    error_message: null,
    created_at: NOW,
    ...overrides,
  };
}

/**
 * 聊天 SSE 替身：POST 发送返回 started → humanizer 过程事件 → done
 * （携带人味化结果投影）；retry 同样走该流；历史 GET 返回固化消息。
 */
async function installMockChatApi(
  page: Page,
  options: {
    /** 首个发送轮次是否以 error 事件失败（用于恢复重试测试）。 */
    failFirstSend?: boolean;
    /** 发送载荷快照（断言 SKILL 载荷随消息落库）。 */
    onSendBody?: (body: Record<string, unknown>) => void;
  } = {}
): Promise<{ messageId: () => string }> {
  const state: {
    messages: MockMessage[];
    sent: number;
    messageId: string;
  } = { messages: [], sent: 0, messageId: "a-1" };
  // Issue 02：消息 → 持久化事件流（POST 创建运行后由 events 端点回放）
  const eventStreams = new Map<string, string>();

  const history = () => ({
    conversation_id: "mock-1",
    title: "测试对话",
    mode: "companion",
    messages: state.messages,
  });

  const buildTurn = (body: Record<string, unknown>) => {
    state.sent += 1;
    const turn = state.sent;
    const userMessage: MockMessage = {
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
      // Issue 28：用户消息携带 SKILL 载荷快照（重试沿用同一份输入）
      skill: (body.skill_input as Record<string, unknown> | undefined) ?? null,
    };
    const messageId = `a-${turn}`;
    state.messageId = messageId;
    const assistantMessage: MockMessage = {
      message_id: messageId,
      conversation_id: "mock-1",
      role: "assistant",
      attempt_number: 1,
      status: "done",
      content: "这是改写后的最终文本。",
      error_code: null,
      error_message: null,
      duration_ms: 1200,
      model_id: "qwen3.6-flash",
      run_lock_id: "lock-mock",
      created_at: NOW,
      updated_at: NOW,
      humanizer: mockHumanizerResult(),
      thinking: {
        steps: ["解析任务契约", "提取事实锁", "按体裁规则生成", "确定性复核"],
        evidence: [],
        tools: ["bridges-humanizer v1.0.0"],
        quality: ["回答已完整生成并保存"],
      },
    };
    state.messages.push(userMessage, assistantMessage);
    return { userMessage, assistantMessage, messageId };
  };

  await page.route("**/api/chat/conversations", async (route) => {
    if (route.request().method() === "POST") {
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify(history()),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        conversations: [
          {
            conversation_id: "mock-1",
            title: "测试对话",
            mode: "companion",
            message_count: state.messages.length,
            created_at: NOW,
            updated_at: NOW,
          },
        ],
      }),
    });
  });

  await page.route("**/api/chat/conversations/mock-1", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(history()),
    });
  });

  const streamFor = (body: Record<string, unknown>) => {
    const { userMessage, assistantMessage, messageId } = buildTurn(body);
    options.onSendBody?.(body);
    if (options.failFirstSend && state.sent === 1) {
      const failed = {
        ...assistantMessage,
        status: "error",
        error_code: "humanizer_generation_failed",
        error_message: "人味化生成失败，请重试（输入已保留）。",
        humanizer: mockHumanizerResult({
          status: "error",
          error_code: "humanizer_generation_failed",
          error_message: "人味化生成失败，请重试（输入已保留）。",
          output: null,
          process_state: "recovery",
        }),
      };
      state.messages[state.messages.length - 1] = failed;
      return (
        sseBlock("started", {
          kind: "started",
          conversation_id: "mock-1",
          user_message_id: userMessage.message_id,
          message_id: messageId,
          attempt_number: 1,
          thinking: assistantMessage.thinking,
        }) +
        sseBlock("humanizer", {
          kind: "humanizer",
          message_id: messageId,
          state: "recovery",
          step_label: "任务未完成",
          detail: "人味化生成失败，请重试（输入已保留）。",
          retryable: true,
          progress_steps: ["解析任务契约", "提取事实锁"],
        }) +
        sseBlock("error", {
          kind: "error",
          message_id: messageId,
          error: {
            code: "humanizer_generation_failed",
            message: "人味化生成失败，请重试（输入已保留）。",
            retryable: true,
          },
          thinking: assistantMessage.thinking,
          duration_ms: 900,
        })
      );
    }
    return (
      sseBlock("started", {
        kind: "started",
        conversation_id: "mock-1",
        user_message_id: userMessage.message_id,
        message_id: messageId,
        attempt_number: 1,
        thinking: assistantMessage.thinking,
      }) +
      sseBlock("humanizer", {
        kind: "humanizer",
        message_id: messageId,
        state: "loading",
        step_label: "正在提取事实锁…",
        detail: null,
        retryable: false,
        progress_steps: ["解析任务契约"],
      }) +
      sseBlock("humanizer", {
        kind: "humanizer",
        message_id: messageId,
        state: "loading",
        step_label: "正在复核事实锁与体裁规则…",
        detail: null,
        retryable: false,
        progress_steps: ["解析任务契约", "提取事实锁", "按体裁规则生成"],
      }) +
      sseBlock("done", {
        kind: "done",
        message_id: messageId,
        message: assistantMessage,
      })
    );
  };

  await page.route("**/api/chat/conversations/mock-1/attachments", async (route) => {
    // 文件改写路径：真实上传替身（返回与后端一致的投影形状）
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({
        object_id: "obj-mock-1",
        account_id: "mock",
        conversation_id: "mock-1",
        message_id: null,
        upload_id: "upload-mock-1",
        original_filename: "光合作用笔记.txt",
        media_type: "text/plain",
        content_length: 12,
        content_hash: "hash-mock",
        status: "uploaded",
        created_at: NOW,
        updated_at: NOW,
        ingestion_status: null,
        ingestion_error: null,
      }),
    });
  });

  await page.route("**/api/chat/conversations/mock-1/messages", async (route) => {
    if (route.request().method() !== "POST") return;
    const body = JSON.parse(route.request().postData() ?? "{}");
    const stream = streamFor(body);
    eventStreams.set(state.messageId, stream);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(
        runCreated(
          `run-${state.messageId}`,
          1,
          state.messages[state.messages.length - 2],
          state.messages[state.messages.length - 1]
        )
      ),
    });
  });

  await page.route("**/api/chat/conversations/mock-1/messages/*/retry", async (route) => {
    if (route.request().method() !== "POST") return;
    const body = { content: "重试", skill_input: state.messages.find((m) => m.role === "user")?.skill };
    const stream = streamFor(body as Record<string, unknown>);
    eventStreams.set(state.messageId, stream);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(
        runCreated(
          `run-${state.messageId}`,
          1,
          state.messages[state.messages.length - 2],
          state.messages[state.messages.length - 1]
        )
      ),
    });
  });

  // Issue 02：订阅运行事件（回放已持久化事件；运行终态后结束）
  installRunEventsRoutes(page, eventStreams);

  return { messageId: () => state.messageId };
}

async function freshAccount(page: Page, prefix: string) {
  const creds = uniqueCredentials(prefix);
  await signUp(page, creds.username, creds.qqEmail, PASSWORD);
  return creds;
}

async function openChat(page: Page) {
  await page.goto("/chat/mock-1");
  await expect(page.getByTestId("chat-thread")).toBeVisible();
}

// ---------------------------------------------------------------------------
// 入口：两种模式的「+」菜单与空白对话建议卡（原创图标）
// ---------------------------------------------------------------------------

test("「+」菜单与建议卡均以原创图标进入人味化对话框；两种模式入口一致", async ({ page }) => {
  await freshAccount(page, "i28-entry");
  await installMockChatApi(page);
  await openChat(page);

  // 日常陪伴模式：「+」菜单 → 文章人味化 → 对话框
  await page.getByRole("button", { name: "更多功能" }).click();
  const menuItem = page.getByRole("menuitem", { name: "文章人味化" });
  await expect(menuItem).toBeVisible();
  await menuItem.click();
  await expect(page.getByRole("dialog", { name: /文章人味化/ })).toBeVisible();
  await page.getByTestId("humanizer-cancel").click();

  // 切到学习模式后入口依然存在（两种模式的「+」菜单均接入真实流程）
  await page.getByTestId("mode-toggle").getByRole("button", { name: /学习/ }).click();
  await page.getByRole("button", { name: "更多功能" }).click();
  await expect(page.getByRole("menuitem", { name: "文章人味化" })).toBeVisible();
  await page.getByRole("menuitem", { name: "文章人味化" }).click();
  await expect(page.getByRole("dialog", { name: /文章人味化/ })).toBeVisible();
  await page.getByTestId("humanizer-cancel").click();
});

test("空白对话建议卡「文章人味化」打开对话框（键盘可达）", async ({ page }) => {
  await freshAccount(page, "i28-suggest");
  await page.goto("/");
  await expect(page.getByTestId("suggestion-cards")).toBeVisible();

  const card = page.getByTestId("suggestion-cards").getByRole("button", { name: /文章人味化/ });
  await card.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog", { name: /文章人味化/ })).toBeVisible();
  await page.getByTestId("humanizer-cancel").click();
});

// ---------------------------------------------------------------------------
// 改写路径：粘贴文本 → 真实消息流 → 结果卡五要素
// ---------------------------------------------------------------------------

test("改写路径：粘贴文本提交，过程卡五态流转，结果卡展示输出合同五要素", async ({ page }) => {
  await freshAccount(page, "i28-rewrite");
  const sentBodies: Record<string, unknown>[] = [];
  await installMockChatApi(page, { onSendBody: (body) => sentBodies.push(body) });
  await openChat(page);

  await page.getByRole("button", { name: "更多功能" }).click();
  await page.getByRole("menuitem", { name: "文章人味化" }).click();
  await expect(page.getByRole("dialog", { name: /文章人味化/ })).toBeVisible();

  // 改写页签：粘贴原文 + 体裁 + 提交
  await page.getByTestId("humanizer-source-text").fill("研究显示，在光照充足的条件下，净光合速率约为 25 μmol·m⁻²·s⁻¹。");
  await page.getByTestId("humanizer-genre-select").selectOption("popular_science");
  await page.getByTestId("humanizer-submit").click();

  // 终态：结果卡可展开，五要素齐全
  await expect(page.getByTestId("humanizer-result-card")).toBeVisible();
  await expect(page.getByTestId("humanizer-result-card")).toContainText("人味化完成");
  await page.getByTestId("humanizer-result-card").getByRole("button").click();
  await expect(page.getByTestId("humanizer-edits")).toContainText("理由");
  await expect(page.getByTestId("humanizer-fact-check")).toContainText("已核实");
  await expect(page.getByTestId("humanizer-open-questions")).toContainText("仍待验证");
  await expect(page.getByTestId("humanizer-fact-lock-conflicts")).toHaveCount(0);
  // 正文 = 最终文本（真实消息流程）
  await expect(page.getByTestId("chat-thread")).toContainText("这是改写后的最终文本。");

  // SKILL 载荷随用户消息落库（快照含任务契约）
  expect(sentBodies.length).toBeGreaterThan(0);
  const body = sentBodies[0];
  expect(body.skill_id).toBe("bridges-humanizer");
  expect((body.skill_input as Record<string, unknown>).contract).toMatchObject({
    path: "rewrite",
    genre: "popular_science",
  });
});

test("改写路径：空输入给出可操作提示（empty 态语义）", async ({ page }) => {
  await freshAccount(page, "i28-empty");
  await installMockChatApi(page);
  await openChat(page);

  await page.getByRole("button", { name: "更多功能" }).click();
  await page.getByRole("menuitem", { name: "文章人味化" }).click();
  await expect(page.getByRole("dialog", { name: /文章人味化/ })).toBeVisible();

  // 不填原文：提交按钮保持禁用（空态引导），不发送任何请求
  await page.getByTestId("humanizer-genre-select").selectOption("popular_science");
  await expect(page.getByTestId("humanizer-submit")).toBeDisabled();
});

test("改写路径：选择当前账户文件提交，进入同一真实消息流", async ({ page }) => {
  await freshAccount(page, "i28-file");
  const sentBodies: Record<string, unknown>[] = [];
  await installMockChatApi(page, { onSendBody: (body) => sentBodies.push(body) });
  await openChat(page);

  await page.getByRole("button", { name: "更多功能" }).click();
  await page.getByRole("menuitem", { name: "文章人味化" }).click();
  await expect(page.getByRole("dialog", { name: /文章人味化/ })).toBeVisible();

  // 文件改写：选择附件 → 上传完成 → 提交
  await page.getByTestId("humanizer-file-input").setInputFiles({
    name: "光合作用笔记.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("研究显示净光合速率约为 25 μmol·m⁻²·s⁻¹。"),
  });
  await expect(page.getByTestId("humanizer-file-list")).toContainText("已上传");
  await page.getByTestId("humanizer-submit").click();

  await expect(page.getByTestId("humanizer-result-card")).toBeVisible();
  const body = sentBodies[0];
  const contract = (body.skill_input as Record<string, unknown>).contract as Record<string, unknown>;
  expect(contract.path).toBe("rewrite");
  expect(contract.attachment_ids).toEqual(["obj-mock-1"]);
});

// ---------------------------------------------------------------------------
// 生成路径：主题/受众/体裁/渠道/硬约束
// ---------------------------------------------------------------------------

test("生成路径：填写主题与硬约束后提交，进入同一真实消息流", async ({ page }) => {
  await freshAccount(page, "i28-generate");
  const sentBodies: Record<string, unknown>[] = [];
  await installMockChatApi(page, { onSendBody: (body) => sentBodies.push(body) });
  await openChat(page);

  await page.getByRole("button", { name: "更多功能" }).click();
  await page.getByRole("menuitem", { name: "文章人味化" }).click();
  await page.getByTestId("humanizer-tab-generate").click();

  await page.getByTestId("humanizer-topic-input").fill("为什么人的睡眠时长随着年龄变化");
  await page.getByTestId("humanizer-genre-select").selectOption("popular_science");
  await page.getByTestId("humanizer-audience-input").fill("大一新生");
  await page.getByTestId("humanizer-channel-input").fill("公众号");
  await page.getByTestId("humanizer-constraints-input").fill("不得声称睡眠时长决定健康水平。\n全文不超过 1200 字。");
  await page.getByTestId("humanizer-submit").click();

  await expect(page.getByTestId("humanizer-result-card")).toBeVisible();
  const body = sentBodies[0];
  const contract = (body.skill_input as Record<string, unknown>).contract as Record<string, unknown>;
  expect(contract.path).toBe("generate");
  expect(contract.topic).toBe("为什么人的睡眠时长随着年龄变化");
  expect(contract.hard_constraints).toEqual([
    "不得声称睡眠时长决定健康水平。",
    "全文不超过 1200 字。",
  ]);
});

// ---------------------------------------------------------------------------
// 失败恢复：error → recovery 过程卡 → 重试（输入保留）
// ---------------------------------------------------------------------------

test("失败后过程卡进入 recovery 态，重试沿用原任务输入", async ({ page }) => {
  await freshAccount(page, "i28-retry");
  await installMockChatApi(page, { failFirstSend: true });
  await openChat(page);

  await page.getByRole("button", { name: "更多功能" }).click();
  await page.getByRole("menuitem", { name: "文章人味化" }).click();
  await page.getByTestId("humanizer-source-text").fill("研究显示，在光照充足的条件下，净光合速率约为 25 μmol·m⁻²·s⁻¹。");
  await page.getByTestId("humanizer-submit").click();

  // 失败：结果卡以「任务未完成」呈现，可展开看到可操作说明与重试入口
  await expect(page.getByTestId("humanizer-result-card")).toBeVisible();
  await expect(page.getByTestId("humanizer-result-card")).toContainText("任务未完成");
  await expect(page.getByTestId("humanizer-result-card")).toContainText("输入已保留");

  // 重试：同一任务再走一遍流程并成功（输入与任务契约保留；历史尝试保留）
  await page.getByTestId("humanizer-result-retry").click();
  await expect(page.getByTestId("humanizer-result-card").last()).toContainText("人味化完成");
});
