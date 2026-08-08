import { expect, test, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";
import { installRunEventsRoutes, runCreated } from "./helpers/chat-mock";

/**
 * Issue 07 — 人味化可靠交付正文：硬门/软门两级质量门。
 *
 * 覆盖：软门（体裁等风格指标）失败仍交付完整正文与具体警告（至多一次
 * 修复，不循环重生成）；硬门（事实锁冲突）阻止标记最终稿并给出冲突项
 * 与恢复方式；DOCX 改写只以该附件为原文（source_attachment_ids 与消息
 * 绑定一致）；知识库不相关图片不被检索/展示/引用；离开后返回可见草稿
 * 与最终结果。
 *
 * 消息发送路径用协议级替身（与 issue11/28 同一策略）。
 */

const NOW = "2026-08-08T00:00:00Z";
const PASSWORD = "correct-horse-07";

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

/** 人味化结果投影（含 Issue 07 两级质量门字段）。 */
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
      source_text: null,
      attachment_ids: ["obj-docx-1"],
      audience: "普通读者",
      channel: "公众号",
    },
    status: "done",
    output: {
      final_text: "光合作用指的是植物把光能转化为化学能的过程。你可以把光合作用比作植物的充电过程，但比喻到此为止。",
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
      quality_status: "ok",
      source_attachment_ids: ["obj-docx-1"],
    },
    fact_lock_check: {
      check_id: "flc-1",
      source_text: "原文 vs 改写结果",
      entries: [],
      blocking_conflicts: [],
      needs_human: [],
      passed: true,
    },
    references: [
      {
        reference_id: "ref-1",
        label: "光合作用笔记.docx",
        source_type: "attachment",
        detail: "文件：光合作用笔记.docx",
        citation_surface: null,
        preserved: true,
      },
    ],
    genre_check: ["✓ 核心概念的一句话定义：已出现"],
    quality_warnings: [],
    repair_attempts: 0,
    process_state: "done",
    process_steps: ["解析任务契约", "提取事实锁", "按体裁规则生成", "确定性复核"],
    error_code: null,
    error_message: null,
    created_at: NOW,
    ...overrides,
  };
}

interface InstallOptions {
  /** 首个发送轮次的 SSE 替身（默认 done 正常流）。 */
  streamFor?: (body: Record<string, unknown>, state: MockState) => string;
  onSendBody?: (body: Record<string, unknown>) => void;
  /** 历史中已存在的消息（离开后返回场景预置）。 */
  presetMessages?: MockMessage[];
}

interface MockState {
  messages: MockMessage[];
  sent: number;
  messageId: string;
}

/** 标准软门失败流：过程事件 → 软门警告 → done（正文 + 未完全满足项）。 */
function softGateStream(
  body: Record<string, unknown>,
  state: MockState,
  humanizer: Record<string, unknown>
): string {
  const last = state.messages[state.messages.length - 1];
  const userMessage = state.messages[state.messages.length - 2];
  return (
    sseBlock("started", {
      kind: "started",
      conversation_id: "mock-1",
      user_message_id: userMessage.message_id,
      message_id: last.message_id,
      attempt_number: 1,
    }) +
    sseBlock("stage", {
      kind: "stage",
      message_id: last.message_id,
      stage: "local_retrieval",
      status: "skipped",
    }) +
    sseBlock("humanizer", {
      kind: "humanizer",
      message_id: last.message_id,
      state: "loading",
      step_label: "正在复核事实锁与体裁规则…",
      detail: null,
      retryable: false,
      progress_steps: ["解析任务契约", "提取事实锁", "按体裁规则生成"],
    }) +
    sseBlock("done", {
      kind: "done",
      message_id: last.message_id,
      message: { ...last, humanizer },
    })
  );
}

/** 标准硬门失败流：过程事件 → error（冲突项 + 恢复方式）。 */
function hardGateStream(
  body: Record<string, unknown>,
  state: MockState,
  humanizer: Record<string, unknown>
): string {
  const last = state.messages[state.messages.length - 1];
  const userMessage = state.messages[state.messages.length - 2];
  return (
    sseBlock("started", {
      kind: "started",
      conversation_id: "mock-1",
      user_message_id: userMessage.message_id,
      message_id: last.message_id,
      attempt_number: 1,
    }) +
    sseBlock("humanizer", {
      kind: "humanizer",
      message_id: last.message_id,
      state: "loading",
      step_label: "正在复核事实锁与体裁规则…",
      detail: null,
      retryable: false,
      progress_steps: ["解析任务契约", "提取事实锁", "按体裁规则生成"],
    }) +
    sseBlock("error", {
      kind: "error",
      message_id: last.message_id,
      error: {
        code: "fact_lock_conflict",
        message: (humanizer.error_message as string) ?? "事实锁冲突，已停止交付。",
        retryable: false,
      },
    })
  );
}

/**
 * 聊天 SSE 替身：POST 发送返回 started → 过程/stage 事件 → 终态；
 * 历史 GET 返回固化消息；事件端点回放已持久化流。
 */
async function installMockChatApi(
  page: Page,
  options: InstallOptions = {}
): Promise<{ messageId: () => string }> {
  const state: MockState = {
    messages: options.presetMessages ?? [],
    sent: 0,
    messageId: "a-1",
  };
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
        quality: [],
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

  const defaultStream = (
    body: Record<string, unknown>,
    current: MockState
  ): string => {
    const { userMessage, assistantMessage } = buildTurn(body);
    void userMessage;
    return (
      sseBlock("started", {
        kind: "started",
        conversation_id: "mock-1",
        user_message_id: userMessage.message_id,
        message_id: assistantMessage.message_id,
        attempt_number: 1,
      }) +
      sseBlock("stage", {
        kind: "stage",
        message_id: assistantMessage.message_id,
        stage: "local_retrieval",
        status: "skipped",
      }) +
      sseBlock("done", {
        kind: "done",
        message_id: assistantMessage.message_id,
        message: assistantMessage,
      })
    );
  };

  await page.route("**/api/chat/conversations/mock-1/attachments", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: "[]",
      });
      return;
    }
    // 文件改写路径：真实上传替身（返回与后端一致的投影形状）
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({
        object_id: "obj-docx-1",
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
    const stream = options.streamFor
      ? options.streamFor(body, state)
      : defaultStream(body, state);
    options.onSendBody?.(body);
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

async function openHumanizerDialog(page: Page) {
  await page.getByRole("button", { name: "更多功能" }).click();
  await page.getByRole("menuitem", { name: "文章人味化" }).click();
  await expect(page.getByRole("dialog", { name: /文章人味化/ })).toBeVisible();
}

// ---------------------------------------------------------------------------
// 软门：体裁未达阈值仍交付完整正文与具体软警告
// ---------------------------------------------------------------------------

test("软门失败：正文完整交付 + 未完全满足项可展开（不扣留正文）", async ({ page }) => {
  await freshAccount(page, "i07-soft");
  const finalText =
    "光合作用指的是植物把光能转化为化学能的过程。研究显示，净光合速率约为 25 μmol·m⁻²·s⁻¹。";
  const softHumanizer = mockHumanizerResult({
    status: "needs_human",
    output: {
      final_text: finalText,
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
      open_questions: [],
      quality_status: "warn",
      source_attachment_ids: [],
    },
    quality_warnings: [
      "体裁规则「至少一个类比（好比/就像/比作）」未完全满足：缺失",
      "体裁规则「类比的边界说明（类比不能无限延伸）」未完全满足：缺失",
    ],
    repair_attempts: 1,
    genre_check: [
      "✓ 核心概念的一句话定义：已出现",
      "✗ 至少一个类比（好比/就像/比作）：缺失",
    ],
  });
  const sentBodies: Record<string, unknown>[] = [];
  await installMockChatApi(page, {
    onSendBody: (body) => sentBodies.push(body),
    streamFor: (body, state) => {
      const { userMessage } = (() => {
        const turn = state.sent + 1;
        return {
          userMessage: {
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
            skill: (body.skill_input as Record<string, unknown> | undefined) ?? null,
          } as MockMessage,
        };
      })();
      // 直接构造本轮的 assistant 消息（软门终态 needs_human）
      const messageId = `a-${state.sent + 1}`;
      const assistantMessage: MockMessage = {
        message_id: messageId,
        conversation_id: "mock-1",
        role: "assistant",
        attempt_number: 1,
        status: "done",
        content: finalText,
        error_code: null,
        error_message: null,
        duration_ms: 1800,
        model_id: "qwen3.6-flash",
        run_lock_id: "lock-mock",
        created_at: NOW,
        updated_at: NOW,
        humanizer: softHumanizer,
        thinking: {
          steps: ["解析任务契约", "提取事实锁", "按体裁规则生成", "确定性复核"],
          evidence: [],
          tools: ["bridges-humanizer v1.0.0"],
          quality: [],
        },
      };
      state.sent += 1;
      state.messageId = messageId;
      state.messages.push(userMessage, assistantMessage);
      return (
        sseBlock("started", {
          kind: "started",
          conversation_id: "mock-1",
          user_message_id: userMessage.message_id,
          message_id: messageId,
          attempt_number: 1,
        }) +
        sseBlock("stage", {
          kind: "stage",
          message_id: messageId,
          stage: "local_retrieval",
          status: "skipped",
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
    },
  });
  await openChat(page);

  // 生成路径提交（体裁未达阈值 → 软门）
  await openHumanizerDialog(page);
  await page.getByTestId("humanizer-tab-generate").click();
  await page.getByTestId("humanizer-topic-input").fill("光合作用为何高效");
  await page.getByTestId("humanizer-genre-select").selectOption("popular_science");
  await page.getByTestId("humanizer-submit").click();

  // 正文完整交付（消息正文 = 最终文本），结果卡标注「已交付（含未完全满足项）」
  await expect(page.getByTestId("chat-thread")).toContainText(finalText);
  const card = page.getByTestId("humanizer-result-card");
  await expect(card).toBeVisible();
  await expect(card).toContainText("已交付（含未完全满足项）");
  await card.locator("button[aria-expanded]").click();
  await expect(page.getByTestId("humanizer-quality-warnings")).toContainText("类比");
  await expect(page.getByTestId("humanizer-quality-warnings")).toContainText("未完全满足");
  // 至多一次修复：结果卡说明修复次数
  await expect(card).toContainText("定向修正 1 次");
});

// ---------------------------------------------------------------------------
// 硬门：事实锁冲突阻止标记最终稿，冲突项与恢复方式可见
// ---------------------------------------------------------------------------

test("硬门失败：错误卡列出冲突项与恢复方式，正文不冒充最终稿", async ({ page }) => {
  await freshAccount(page, "i07-hard");
  const conflictMessage =
    "事实锁冲突，已停止交付：原文中的「25 μmol·m⁻²·s⁻¹」在结果中未出现，被改写为「30 μmol·m⁻²·s⁻¹」。恢复方式：在原文中修正上述冲突字段后重试（任务输入与附件已保留）。";
  const hardHumanizer = mockHumanizerResult({
    status: "error",
    output: null,
    error_code: "fact_lock_conflict",
    error_message: conflictMessage,
    fact_lock_check: {
      check_id: "flc-1",
      source_text: "原文 vs 改写结果",
      entries: [
        {
          entry_id: "fl-1",
          kind: "number",
          surface_before: "25 μmol·m⁻²·s⁻¹",
          surface_after: "30 μmol·m⁻²·s⁻¹",
          canonical: "25 umol m-2 s-1",
          status: "changed",
          severity: "blocking",
          note: "数值被改写",
        },
      ],
      blocking_conflicts: ["原文中的「25 μmol·m⁻²·s⁻¹」被改写为「30 μmol·m⁻²·s⁻¹」"],
      needs_human: [],
      passed: false,
    },
    process_state: "error",
  });
  await installMockChatApi(page, {
    streamFor: (body, state) => {
      const { userMessage } = (() => {
        const turn = state.sent + 1;
        return {
          userMessage: {
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
            skill: (body.skill_input as Record<string, unknown> | undefined) ?? null,
          } as MockMessage,
        };
      })();
      const messageId = `a-${state.sent + 1}`;
      const assistantMessage: MockMessage = {
        message_id: messageId,
        conversation_id: "mock-1",
        role: "assistant",
        attempt_number: 1,
        status: "error",
        content: "光合作用指的是植物把光能转化为化学能的过程。研究显示，净光合速率约为 30 μmol·m⁻²·s⁻¹。",
        error_code: "fact_lock_conflict",
        error_message: conflictMessage,
        duration_ms: 1500,
        model_id: "qwen3.6-flash",
        run_lock_id: "lock-mock",
        created_at: NOW,
        updated_at: NOW,
        humanizer: hardHumanizer,
        thinking: {
          steps: ["解析任务契约", "提取事实锁", "按体裁规则生成", "确定性复核"],
          evidence: [],
          tools: ["bridges-humanizer v1.0.0"],
          quality: [],
        },
      };
      state.sent += 1;
      state.messageId = messageId;
      state.messages.push(userMessage, assistantMessage);
      return hardGateStream(body, { ...state, messages: state.messages }, hardHumanizer);
    },
  });
  await openChat(page);

  await openHumanizerDialog(page);
  await page.getByTestId("humanizer-source-text").fill("研究显示，净光合速率约为 25 μmol·m⁻²·s⁻¹。");
  await page.getByTestId("humanizer-genre-select").selectOption("popular_science");
  await page.getByTestId("humanizer-submit").click();

  // 错误卡：冲突项 + 恢复方式可见；正文为草稿（未冒充最终稿）
  const card = page.getByTestId("humanizer-result-card");
  await expect(card).toBeVisible();
  await expect(card).toContainText("任务未完成");
  await card.locator("button[aria-expanded]").click();
  await expect(page.getByTestId("humanizer-error-detail")).toContainText("恢复方式");
  await expect(page.getByTestId("humanizer-fact-lock-conflicts")).toContainText("μmol");
  await expect(page.getByTestId("humanizer-result-retry")).toBeVisible();
});

// ---------------------------------------------------------------------------
// DOCX 改写：source attachment ID 与消息绑定一致；无知识库图片
// ---------------------------------------------------------------------------

test("改写 DOCX：source_attachment_ids 与消息绑定一致，知识库不相关图片不引用", async ({
  page,
}) => {
  await freshAccount(page, "i07-docx");
  const finalText =
    "光合作用指的是植物把光能转化为化学能的过程。你可以把光合作用比作植物的充电过程，但比喻到此为止。";
  const docxHumanizer = mockHumanizerResult({
    output: {
      final_text: finalText,
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
      open_questions: [],
      quality_status: "ok",
      source_attachment_ids: ["obj-docx-1"],
    },
    references: [
      {
        reference_id: "ref-1",
        label: "光合作用笔记.txt",
        source_type: "attachment",
        detail: "文件：光合作用笔记.txt",
        citation_surface: null,
        preserved: true,
      },
    ],
  });
  const sentBodies: Record<string, unknown>[] = [];
  await installMockChatApi(page, {
    onSendBody: (body) => sentBodies.push(body),
    streamFor: (body, state) => {
      const { userMessage } = (() => {
        const turn = state.sent + 1;
        return {
          userMessage: {
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
            skill: (body.skill_input as Record<string, unknown> | undefined) ?? null,
          } as MockMessage,
        };
      })();
      const messageId = `a-${state.sent + 1}`;
      const assistantMessage: MockMessage = {
        message_id: messageId,
        conversation_id: "mock-1",
        role: "assistant",
        attempt_number: 1,
        status: "done",
        content: finalText,
        error_code: null,
        error_message: null,
        duration_ms: 1400,
        model_id: "qwen3.6-flash",
        run_lock_id: "lock-mock",
        created_at: NOW,
        updated_at: NOW,
        humanizer: docxHumanizer,
        thinking: {
          steps: ["解析任务契约", "提取事实锁", "按体裁规则生成", "确定性复核"],
          evidence: [],
          tools: ["bridges-humanizer v1.0.0"],
          quality: [],
        },
      };
      state.sent += 1;
      state.messageId = messageId;
      state.messages.push(userMessage, assistantMessage);
      return (
        sseBlock("started", {
          kind: "started",
          conversation_id: "mock-1",
          user_message_id: userMessage.message_id,
          message_id: messageId,
          attempt_number: 1,
        }) +
        sseBlock("stage", {
          kind: "stage",
          message_id: messageId,
          stage: "local_retrieval",
          status: "skipped",
        }) +
        sseBlock("done", {
          kind: "done",
          message_id: messageId,
          message: assistantMessage,
        })
      );
    },
  });
  await openChat(page);

  // 改写路径：选择文件 → 上传 → 提交
  await openHumanizerDialog(page);
  await page.getByTestId("humanizer-file-input").setInputFiles({
    name: "光合作用笔记.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("研究显示净光合速率约为 25 μmol·m⁻²·s⁻¹。"),
  });
  await expect(page.getByTestId("humanizer-file-list")).toContainText("已上传");
  await page.getByTestId("humanizer-submit").click();

  await expect(page.getByTestId("humanizer-result-card")).toBeVisible();
  // 消息绑定的附件 ID = contract.attachment_ids = 结果 source_attachment_ids
  const body = sentBodies[0];
  const contract = (body.skill_input as Record<string, unknown>).contract as Record<string, unknown>;
  expect(contract.attachment_ids).toEqual(["obj-docx-1"]);
  expect(body.attachment_ids).toEqual(["obj-docx-1"]);

  // 结果卡：来源 = 仅该 DOCX（无知识库 retrieval/图片）
  const card = page.getByTestId("humanizer-result-card");
  await card.locator("button[aria-expanded]").click();
  await expect(page.getByTestId("humanizer-references")).toContainText("光合作用笔记.txt");
  await expect(page.getByTestId("humanizer-references")).not.toContainText("retrieval");
  await expect(page.getByTestId("humanizer-references")).not.toContainText("知识库");
  await expect(page.getByTestId("humanizer-references")).not.toContainText("图片");
});

// ---------------------------------------------------------------------------
// 离开后返回：草稿、阶段与结果在历史消息中可见
// ---------------------------------------------------------------------------

test("离开后返回：历史消息可见草稿与最终结果（刷新不丢失）", async ({ page }) => {
  await freshAccount(page, "i07-return");
  const finalText =
    "光合作用指的是植物把光能转化为化学能的过程。你可以把光合作用比作植物的充电过程，但比喻到此为止。";
  const presetHumanizer = mockHumanizerResult({
    output: {
      final_text: finalText,
      edits: [],
      fact_check: [
        {
          item: "25 μmol·m⁻²·s⁻¹",
          result: "已核实",
          evidence: "与原文一致。",
        },
      ],
      open_questions: [],
      quality_status: "ok",
      source_attachment_ids: ["obj-docx-1"],
    },
    references: [
      {
        reference_id: "ref-1",
        label: "光合作用笔记.txt",
        source_type: "attachment",
        detail: "文件：光合作用笔记.txt",
        citation_surface: null,
        preserved: true,
      },
    ],
  });
  const presetMessages: MockMessage[] = [
    {
      message_id: "u-1",
      conversation_id: "mock-1",
      role: "user",
      attempt_number: 1,
      status: "done",
      content: "文章人味化（科普文案）：改写《光合作用笔记》",
      error_code: null,
      error_message: null,
      duration_ms: null,
      model_id: null,
      run_lock_id: null,
      created_at: NOW,
      updated_at: NOW,
      skill: {
        skill_id: "bridges-humanizer",
        contract: {
          path: "rewrite",
          genre: "popular_science",
          attachment_ids: ["obj-docx-1"],
          audience: "普通读者",
          channel: "公众号",
        },
      },
    },
    {
      message_id: "a-1",
      conversation_id: "mock-1",
      role: "assistant",
      attempt_number: 1,
      status: "done",
      content: finalText,
      error_code: null,
      error_message: null,
      duration_ms: 1200,
      model_id: "qwen3.6-flash",
      run_lock_id: "lock-mock",
      created_at: NOW,
      updated_at: NOW,
      humanizer: presetHumanizer,
      thinking: {
        steps: ["解析任务契约", "提取事实锁", "按体裁规则生成", "确定性复核"],
        evidence: [],
        tools: ["bridges-humanizer v1.0.0"],
        quality: [],
      },
    },
  ];
  await installMockChatApi(page, { presetMessages });
  await openChat(page);

  // 历史消息直接展示正文（刷新/离开后仍可见）
  await expect(page.getByTestId("chat-thread")).toContainText(finalText);
  const card = page.getByTestId("humanizer-result-card");
  await expect(card).toBeVisible();
  await card.locator("button[aria-expanded]").click();
  await expect(page.getByTestId("humanizer-references")).toContainText("光合作用笔记.txt");
});
