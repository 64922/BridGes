import { expect, test, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

/**
 * Issue 29 — 交付生涯规划助手。
 *
 * 覆盖：两种模式的「+」菜单与空白对话建议卡均以原创图标进入真实任务
 * 对话框；生涯问题 + 画像开关提交走真实消息流；过程卡中文五态（loading/
 * recovery）；结果卡六类分区（已知事实/待验证假设/可选方向/关键风险/
 * 分阶段成长路径/近期学习建议，各带证据状态与核查时间）、本轮证据
 * （含核查时间与来源链接）、保证边界声明、未决问题；逐项反馈定位到具体
 * 条目；失败后可重试且输入保留；全程键盘可达。
 *
 * 消息发送路径用协议级替身（与 issue11/27/28 同一策略）；Key 预检沿用
 * 真实后端。
 */

const NOW = "2026-08-05T00:00:00Z";
const PASSWORD = "correct-horse-29";

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
  career_planning?: Record<string, unknown> | null;
  context_note?: Record<string, unknown> | null;
  thinking?: Record<string, unknown> | null;
  [key: string]: unknown;
}

function sseBlock(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

/** 完整的生涯规划结果投影（六类输出 + 证据 + 复核 + 过程轨迹）。 */
function mockCareerResult(
  overrides: Partial<Record<string, unknown>> = {}
): Record<string, unknown> {
  return {
    plan_id: "a-1",
    intent: "生涯规划助手：我大二在读计算机科学，喜欢数据分析。",
    status: "done",
    profile_enabled: true,
    profile_used: true,
    verified_at: NOW,
    output: {
      final_text: "综合你的阶段与目标，数据分析是值得考虑的方向，可以从选课与实习两条线并行推进。",
      facts: [
        {
          item_id: "fact:1",
          content: "数据分析相关岗位需求在近三年持续增长。",
          evidence_refs: ["web:0"],
          note: "来源：行业报告。",
          verified_at: NOW,
        },
      ],
      assumptions: [
        {
          item_id: "assumption:1",
          content: "你可能适合偏业务的数据分析岗。",
          evidence_refs: [],
          note: "【未核实】无任何本轮证据引用，不得作为稳定结论呈现；应视为待验证假设。",
          verified_at: NOW,
          verification_next_step: "与从业者交流或做一次实习验证。",
        },
      ],
      options: [
        {
          item_id: "option:1",
          content: "数据分析方向（业务侧）。",
          evidence_refs: ["web:0"],
          note: null,
          verified_at: NOW,
          rationale: "与你的兴趣与课程背景匹配。",
        },
      ],
      risks: [
        {
          item_id: "risk:1",
          content: "岗位竞争加剧。",
          evidence_refs: ["web:0"],
          note: null,
          verified_at: NOW,
          trigger: "应届求职季人数增加。",
        },
      ],
      path: [
        {
          item_id: "stage:1",
          content: "先补齐统计与 SQL 基础。",
          evidence_refs: ["statement:current"],
          note: null,
          verified_at: NOW,
          timeline: "第 1-3 个月",
        },
      ],
      suggestions: [
        {
          item_id: "suggestion:1",
          content: "完成一个端到端数据分析小项目。",
          evidence_refs: ["statement:current"],
          note: null,
          verified_at: NOW,
          verification: "项目上线后可验证兴趣与能力。",
        },
      ],
      boundary_statement: "本规划不构成就业、薪酬或录取保证，也不替代持证职业顾问。",
      open_questions: ["行业报告的统计口径未完全披露，建议进一步核查。"],
    },
    evidence_sources: [
      {
        evidence_id: "web:0",
        kind: "web_search",
        title: "行业报告：数据分析岗位需求",
        locator: "https://example.com/report",
        url: "https://example.com/report",
        summary: "近三年数据分析相关岗位需求持续增长。",
        accessed_at: NOW,
        stale: false,
      },
      {
        evidence_id: "statement:current",
        kind: "user_statement",
        title: "用户本次陈述",
        locator: null,
        url: null,
        summary: "我大二在读计算机科学，喜欢数据分析。",
        accessed_at: NOW,
        stale: false,
      },
    ],
    review: {
      passed: true,
      reviews: [
        { item_id: "fact:1", category: "facts", state: "verified", reason: "已核验：有可定位来源与核查时间。" },
        { item_id: "assumption:1", category: "assumptions", state: "unverified", reason: "无任何本轮证据引用，不得作为稳定结论呈现；应视为待验证假设。" },
      ],
      boundary_violations: [],
      warnings: [],
    },
    process_state: "done",
    process_steps: ["编译可用证据", "生成六类规划结果", "复核证据与边界"],
    error_code: null,
    error_message: null,
    created_at: NOW,
    ...overrides,
  };
}

/**
 * 聊天 SSE 替身：POST 发送返回 started → career 过程事件 → done
 * （携带生涯规划结果投影）；retry 同样走该流；历史 GET 返回固化消息。
 */
async function installMockChatApi(
  page: Page,
  options: {
    /** 首个发送轮次是否以 error 事件失败（用于恢复重试测试）。 */
    failFirstSend?: boolean;
    /** 发送载荷快照（断言画像开关透传）。 */
    onSendBody?: (body: Record<string, unknown>) => void;
  } = {}
): Promise<{ messageId: () => string }> {
  const state: {
    messages: MockMessage[];
    sent: number;
    messageId: string;
  } = { messages: [], sent: 0, messageId: "a-1" };

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
    };
    const messageId = `a-${turn}`;
    state.messageId = messageId;
    // Issue 29：画像开关随本轮发送透传——关闭时披露为 off 态（不含画像）
    const profileEnabled = body.use_profile !== false;
    const contextNote = profileEnabled
      ? {
          state: "ready",
          profile_enabled: true,
          mode: "companion",
          used_at: NOW,
          profile_items: [
            {
              assertion_id: "assertion-1",
              dimension: "interest_preference",
              dimension_label: "兴趣偏好",
              value_summary: "喜欢数据分析与可视化。",
              inclusion_reason: "与companion模式相关且已授权的最小切片（类别：兴趣偏好）。",
              used_at: NOW,
              status: "active",
              version: 1,
              applicable_scenes: ["companion", "study"],
            },
          ],
          material_categories: ["公网搜索"],
          excluded_count: 1,
          note: "本轮回答使用了 1 条画像记录（兴趣偏好），仅包含与当前任务相关的最小切片。",
        }
      : {
          state: "off",
          profile_enabled: false,
          mode: "companion",
          used_at: NOW,
          profile_items: [],
          material_categories: [],
          excluded_count: 0,
          note: "本轮未使用你的画像记录（发送前已关闭）。回答不基于任何画像信息。",
        };
    const assistantMessage: MockMessage = {
      message_id: messageId,
      conversation_id: "mock-1",
      role: "assistant",
      attempt_number: 1,
      status: "done",
      content: "综合你的阶段与目标，数据分析是值得考虑的方向，可以从选课与实习两条线并行推进。",
      error_code: null,
      error_message: null,
      duration_ms: 1800,
      model_id: "qwen3.6-flash",
      run_lock_id: "lock-mock",
      created_at: NOW,
      updated_at: NOW,
      career_planning: mockCareerResult({
        profile_enabled: profileEnabled,
        profile_used: profileEnabled,
      }),
      context_note: contextNote,
      thinking: {
        steps: ["识别生涯规划意图", "编译可用证据", "生成六类规划结果", "复核证据与边界"],
        evidence: ["行业报告：数据分析岗位需求（核查于 2026-08-05）"],
        tools: ["生涯规划助手"],
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
        error_code: "career_generation_failed",
        error_message: "生涯规划生成失败，请重试（输入已保留）。",
        career_planning: mockCareerResult({
          status: "error",
          error_code: "career_generation_failed",
          error_message: "生涯规划生成失败，请重试（输入已保留）。",
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
        sseBlock("career", {
          kind: "career",
          message_id: messageId,
          state: "recovery",
          step_label: "规划未完成",
          detail: "生涯规划生成失败，请重试（输入已保留）。",
          retryable: true,
          progress_steps: ["编译可用证据"],
        }) +
        sseBlock("error", {
          kind: "error",
          message_id: messageId,
          error: {
            code: "career_generation_failed",
            message: "生涯规划生成失败，请重试（输入已保留）。",
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
      sseBlock("career", {
        kind: "career",
        message_id: messageId,
        state: "loading",
        step_label: "正在编译可用证据…",
        detail: null,
        retryable: false,
        progress_steps: [],
      }) +
      sseBlock("career", {
        kind: "career",
        message_id: messageId,
        state: "loading",
        step_label: "正在复核证据与边界…",
        detail: null,
        retryable: false,
        progress_steps: ["编译可用证据", "生成六类规划结果"],
      }) +
      sseBlock("done", {
        kind: "done",
        message_id: messageId,
        message: assistantMessage,
      })
    );
  };

  await page.route("**/api/chat/conversations/mock-1/messages", async (route) => {
    if (route.request().method() !== "POST") return;
    const body = JSON.parse(route.request().postData() ?? "{}");
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: streamFor(body),
    });
  });

  await page.route("**/api/chat/conversations/mock-1/messages/*/retry", async (route) => {
    if (route.request().method() !== "POST") return;
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: streamFor({ content: "生涯规划助手：重试" }),
    });
  });

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

test("「+」菜单与建议卡均以原创图标进入生涯规划对话框；两种模式入口一致", async ({ page }) => {
  await freshAccount(page, "i29-entry");
  await installMockChatApi(page);
  await openChat(page);

  // 日常陪伴模式：「+」菜单 → 生涯规划助手 → 对话框
  await page.getByRole("button", { name: "更多功能" }).click();
  const menuItem = page.getByRole("menuitem", { name: "生涯规划助手" });
  await expect(menuItem).toBeVisible();
  await menuItem.click();
  await expect(page.getByRole("dialog", { name: /生涯规划助手/ })).toBeVisible();
  await page.getByTestId("career-cancel").click();

  // 切到学习模式后入口依然存在（两种模式的「+」菜单均接入真实流程）
  await page.getByTestId("mode-toggle").getByRole("button", { name: /学习/ }).click();
  await page.getByRole("button", { name: "更多功能" }).click();
  await expect(page.getByRole("menuitem", { name: "生涯规划助手" })).toBeVisible();
  await page.getByRole("menuitem", { name: "生涯规划助手" }).click();
  await expect(page.getByRole("dialog", { name: /生涯规划助手/ })).toBeVisible();
  await page.getByTestId("career-cancel").click();
});

test("空白对话建议卡「生涯规划助手」打开对话框（键盘可达）", async ({ page }) => {
  await freshAccount(page, "i29-suggest");
  await page.goto("/");
  await expect(page.getByTestId("suggestion-cards")).toBeVisible();

  const card = page.getByTestId("suggestion-cards").getByRole("button", { name: /生涯规划助手/ });
  await card.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog", { name: /生涯规划助手/ })).toBeVisible();
  await page.getByTestId("career-cancel").click();
});

// ---------------------------------------------------------------------------
// 完整路径：键盘发起规划 → 过程卡 → 六类结果卡 → 依据 → 逐项反馈
// ---------------------------------------------------------------------------

test("键盘发起规划：填写问题提交，过程卡流转，结果卡六类分区与依据齐全", async ({ page }) => {
  await freshAccount(page, "i29-flow");
  const sentBodies: Record<string, unknown>[] = [];
  await installMockChatApi(page, { onSendBody: (body) => sentBodies.push(body) });
  await openChat(page);

  // 键盘路径：「+」菜单 → 对话框 → 填写 → 提交
  await page.getByRole("button", { name: "更多功能" }).focus();
  await page.keyboard.press("Enter");
  await page.getByRole("menuitem", { name: "生涯规划助手" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog", { name: /生涯规划助手/ })).toBeVisible();

  await page.getByTestId("career-question-input").fill("我大二在读计算机科学，喜欢数据分析，怎么规划接下来的方向");
  await page.getByTestId("career-submit").click();

  // 过程卡（loading 态）出现后由结果卡接管
  await expect(page.getByTestId("career-result-card")).toBeVisible();
  await expect(page.getByTestId("career-result-card")).toContainText("生涯规划完成");

  // 展开结果卡：六类分区齐全
  await page.getByTestId("career-result-card").getByRole("button").click();
  await expect(page.getByTestId("career-facts")).toContainText("数据分析相关岗位需求在近三年持续增长");
  await expect(page.getByTestId("career-facts")).toContainText("已核实");
  await expect(page.getByTestId("career-facts")).toContainText("核查时间：2026-08-05");
  await expect(page.getByTestId("career-assumptions")).toContainText("你可能适合偏业务的数据分析岗");
  await expect(page.getByTestId("career-assumptions")).toContainText("未核实");
  await expect(page.getByTestId("career-assumptions")).toContainText("核查方式：与从业者交流或做一次实习验证");
  await expect(page.getByTestId("career-options")).toContainText("数据分析方向（业务侧）");
  await expect(page.getByTestId("career-options")).toContainText("主要依据");
  await expect(page.getByTestId("career-risks")).toContainText("岗位竞争加剧");
  await expect(page.getByTestId("career-risks")).toContainText("触发条件");
  await expect(page.getByTestId("career-path")).toContainText("先补齐统计与 SQL 基础");
  await expect(page.getByTestId("career-path")).toContainText("时间范围");
  await expect(page.getByTestId("career-suggestions")).toContainText("完成一个端到端数据分析小项目");
  await expect(page.getByTestId("career-suggestions")).toContainText("验证方式");

  // 查看依据：证据来源含核查时间与打开来源链接
  await expect(page.getByTestId("career-evidence-sources")).toContainText("行业报告：数据分析岗位需求");
  await expect(page.getByTestId("career-evidence-sources")).toContainText("核查于 2026-08-05");
  const evidenceLink = page.getByTestId("career-evidence-link-web:0");
  await expect(evidenceLink).toHaveAttribute("href", "https://example.com/report");

  // 边界声明与未决问题
  await expect(page.getByTestId("career-boundary")).toContainText("不构成就业、薪酬或录取保证");
  await expect(page.getByTestId("career-open-questions")).toContainText("行业报告的统计口径未完全披露");

  // 真实消息流：请求内容携带显式规划意图
  expect(sentBodies.length).toBeGreaterThan(0);
  expect(String(sentBodies[0].content)).toContain("生涯规划助手：");
  // 正文 = 规划最终文本
  await expect(page.getByTestId("chat-thread")).toContainText("综合你的阶段与目标");
});

test("画像开关：关闭后请求不含画像内容，披露为 off 态", async ({ page }) => {
  await freshAccount(page, "i29-noprofile");
  const sentBodies: Record<string, unknown>[] = [];
  await installMockChatApi(page, {
    onSendBody: (body) => sentBodies.push(body),
  });
  await openChat(page);

  await page.getByRole("button", { name: "更多功能" }).click();
  await page.getByRole("menuitem", { name: "生涯规划助手" }).click();
  await page.getByTestId("career-question-input").fill("我大二在读，怎么规划数据分析方向");
  // 关闭画像使用开关后提交
  await page.getByTestId("career-use-profile").click();
  await expect(page.getByTestId("career-use-profile")).toHaveAttribute("aria-checked", "false");
  await page.getByTestId("career-submit").click();

  await expect(page.getByTestId("career-result-card")).toBeVisible();
  const body = sentBodies[0];
  expect(body.use_profile).toBe(false);
  // 披露为 off 态：展开上下文说明可见「未使用画像」说明，且无画像条目
  await expect(page.getByTestId("context-note-card")).toContainText("画像使用未授权");
  await page.getByTestId("context-note-card").getByRole("button").click();
  await expect(page.getByText("本轮未使用你的画像记录（发送前已关闭）")).toBeVisible();
  await expect(page.getByTestId("context-note-item")).toHaveCount(0);
});

test("逐项反馈：定位到具体条目并提交，成功提示可见", async ({ page }) => {
  await freshAccount(page, "i29-feedback");
  const feedbackBodies: Record<string, unknown>[] = [];
  await page.route("**/api/chat/conversations/mock-1/messages/*/feedback", async (route) => {
    if (route.request().method() !== "POST") return;
    feedbackBodies.push(JSON.parse(route.request().postData() ?? "{}"));
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        feedback_id: "fb-1",
        account_id: "mock",
        conversation_id: "mock-1",
        message_id: "a-1",
        kind: "answer_inappropriate",
        feedback_text: "这条事实过时了。",
        preference: null,
        assertion_id: null,
        career_item_ref: "fact:1",
        status: "submitted",
        resolution_note: null,
        created_at: NOW,
        updated_at: NOW,
      }),
    });
  });
  await installMockChatApi(page);
  await openChat(page);

  await page.getByRole("button", { name: "更多功能" }).click();
  await page.getByRole("menuitem", { name: "生涯规划助手" }).click();
  await page.getByTestId("career-question-input").fill("怎么规划数据分析方向");
  await page.getByTestId("career-submit").click();
  await expect(page.getByTestId("career-result-card")).toBeVisible();
  await page.getByTestId("career-result-card").getByRole("button").click();

  // 事实条目逐项反馈：打开表单 → 填写 → 提交
  await page.getByTestId("career-item-feedback-fact:1").click();
  await page.getByTestId("career-feedback-text-fact:1").fill("这条事实过时了。");
  await page.getByTestId("career-feedback-submit-fact:1").click();
  await expect(page.getByTestId("career-feedback-submitted")).toBeVisible();

  expect(feedbackBodies.length).toBe(1);
  expect(feedbackBodies[0].career_item_ref).toBe("fact:1");
  expect(feedbackBodies[0].feedback_text).toBe("这条事实过时了。");
});

test("失败后过程卡进入 recovery 态，重试沿用原问题", async ({ page }) => {
  await freshAccount(page, "i29-retry");
  await installMockChatApi(page, { failFirstSend: true });
  await openChat(page);

  await page.getByRole("button", { name: "更多功能" }).click();
  await page.getByRole("menuitem", { name: "生涯规划助手" }).click();
  await page.getByTestId("career-question-input").fill("我大二在读，怎么规划数据分析方向");
  await page.getByTestId("career-submit").click();

  // 失败：结果卡以「规划未完成」呈现，可展开看到可操作说明（输入保留）
  // 与安全替代步骤；重试入口在卡片头部（原问题已保留）。
  await expect(page.getByTestId("career-result-card")).toBeVisible();
  await expect(page.getByTestId("career-result-card")).toContainText("规划未完成");
  await expect(page.getByTestId("career-result-card")).toContainText("原问题已保留");
  await page.getByTestId("career-result-card").getByRole("button", { name: /规划未完成/ }).click();
  await expect(page.getByTestId("career-error-detail")).toContainText("输入已保留");

  // 重试：同一任务再走一遍流程并成功（输入与问题保留；历史尝试保留）
  await page.getByTestId("career-result-retry").click();
  await expect(page.getByTestId("career-result-card").last()).toContainText("生涯规划完成");
});
