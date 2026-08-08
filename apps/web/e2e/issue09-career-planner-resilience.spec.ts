import { expect, test, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";
import { installRunEventsRoutes, runCreated } from "./helpers/chat-mock";

/**
 * Issue 09 — 生涯规划助手快速入题、后台执行与可恢复降级。
 *
 * 覆盖两条验收：
 * - 仅输入模糊请求：过程卡立即进入「需要补充信息」（澄清态），消息正文
 *   即澄清问题，不启动完整规划生成；
 * - 生成进行到「形成路径」阶段时切到另一会话再返回：同一 run 继续完成
 *   （过程卡阶段恢复 → 结果卡「生涯规划完成」），不会重复发送。
 *
 * 消息发送路径用协议级替身（与 issue29 同一策略）：POST 创建运行，
 * events 端点按游标回放事件流；Key 预检沿用真实后端。
 */

const NOW = "2026-08-05T00:00:00Z";
const PASSWORD = "correct-horse-09";

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
  thinking?: Record<string, unknown> | null;
  [key: string]: unknown;
}

function sseBlock(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

/** 澄清投影（Issue 09 intake：output 为 null，携带澄清问题）。 */
function mockClarifyResult(question: string): Record<string, unknown> {
  return {
    plan_id: "a-1",
    intent: "生涯规划助手：帮我规划一下",
    status: "done",
    profile_enabled: true,
    profile_used: false,
    verified_at: NOW,
    output: null,
    clarification: question,
    evidence_sources: [],
    review: null,
    process_state: "clarify",
    process_steps: ["收集信息"],
    error_code: null,
    error_message: null,
    created_at: NOW,
  };
}

/**
 * 聊天 SSE 替身（闸门状态驱动）：
 * - ``clarifyQuestion`` 非空时，发送返回澄清事件流（澄清投影 + done）；
 * - 闸门未释放（``mock.releaseGate()``）：events 只回放已发生的过程事件
 *   段（part1，无终态）——模拟生成进行中；历史 GET 返回 streaming 占位。
 *   闸门释放后：events 回放全量（part1+part2），历史返回 done 终态投影。
 *   与真实服务端「游标回放持久化事件」语义一致：重开页面只回放已持久化
 *   事件，运行终态后补发剩余段。
 */
async function installResilienceMockApi(
  page: Page,
  options: {
    clarifyQuestion?: string | null;
  } = {}
): Promise<{
  messageId: () => string;
  postCount: () => number;
  releaseGate: () => void;
}> {
  const state: {
    messages: MockMessage[];
    postCount: number;
    messageId: string;
    gateReleased: boolean;
  } = { messages: [], postCount: 0, messageId: "a-1", gateReleased: false };
  // 事件段（按消息 id）：part1 为已发生段；part2 为终态段（闸门后补发）
  const turnParts = new Map<string, { part1: string; part2: string }>();

  // 历史 GET：闸门未释放时助手消息保持 streaming 占位（运行进行中）
  const history = () => {
    const final = state.messages;
    if (state.gateReleased || final.length < 2) {
      return {
        conversation_id: "mock-1",
        title: "测试对话",
        mode: "companion",
        messages: final,
      };
    }
    const assistant = final.find((message) => message.role === "assistant");
    const running = final.map((message) =>
      message.message_id === assistant?.message_id
        ? {
            ...message,
            status: "streaming",
            content: "",
            career_planning: null,
            // Issue 02 恢复契约：重开页面从 active_run 游标订阅剩余事件
            active_run: { run_id: `run-${message.message_id}`, cursor: 1 },
          }
        : message
    );
    return {
      conversation_id: "mock-1",
      title: "测试对话",
      mode: "companion",
      messages: running,
    };
  };

  const buildTurn = (content: string) => {
    state.postCount += 1;
    const turn = state.postCount;
    const userMessage: MockMessage = {
      message_id: `u-${turn}`,
      conversation_id: "mock-1",
      role: "user",
      attempt_number: 1,
      status: "done",
      content,
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
    const assistantMessage: MockMessage = {
      message_id: messageId,
      conversation_id: "mock-1",
      role: "assistant",
      attempt_number: 1,
      status: "streaming",
      content: "",
      error_code: null,
      error_message: null,
      duration_ms: null,
      model_id: null,
      run_lock_id: null,
      created_at: NOW,
      updated_at: NOW,
      thinking: { mode: "companion", kind: "thinking", stage: "initial" },
    };
    state.messages.push(userMessage, assistantMessage);
    return { userMessage, assistantMessage, messageId };
  };

  await page.route("**/api/chat/conversations/mock-1", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(history()),
    });
  });

  // 另一会话（切走目标）：空历史
  await page.route("**/api/chat/conversations/mock-2", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        conversation_id: "mock-2",
        title: "另一会话",
        mode: "companion",
        messages: [],
      }),
    });
  });

  const buildTurnStreams = (content: string): { part1: string; part2: string } => {
    const { userMessage, assistantMessage, messageId } = buildTurn(content);
    const startedBlock = sseBlock("started", {
      kind: "started",
      conversation_id: "mock-1",
      user_message_id: userMessage.message_id,
      message_id: messageId,
      attempt_number: 1,
      thinking: assistantMessage.thinking,
    });
    if (options.clarifyQuestion) {
      const question = options.clarifyQuestion;
      const final = {
        ...assistantMessage,
        status: "done",
        content: question,
        career_planning: mockClarifyResult(question),
      };
      state.messages[state.messages.length - 1] = final;
      // 澄清事件在 part1 即到达（过程卡 clarify 态在闸门释放前可见）
      return {
        part1:
          startedBlock +
          sseBlock("career", {
            kind: "career",
            message_id: messageId,
            state: "clarify",
            step_label: "需要补充信息",
            detail: question,
            retryable: false,
            progress_steps: ["收集信息"],
          }),
        part2: sseBlock("done", {
          kind: "done",
          message_id: messageId,
          message: final,
        }),
      };
    }
    // 正常路径：part1 含收集信息/形成路径过程事件；part2 为复核与完成
    const part1 =
      startedBlock +
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
        step_label: "正在生成六类规划结果…",
        detail: null,
        retryable: false,
        progress_steps: ["编译可用证据"],
      });
    const final = {
      ...assistantMessage,
      status: "done",
      content: "综合你的阶段与目标，数据分析是值得考虑的方向。",
      career_planning: {
        plan_id: messageId,
        intent: content,
        status: "done",
        profile_enabled: true,
        profile_used: true,
        verified_at: NOW,
        output: {
          final_text: "综合你的阶段与目标，数据分析是值得考虑的方向。",
          facts: [
            {
              item_id: "fact:1",
              content: "数据分析相关岗位需求在近三年持续增长。",
              evidence_refs: ["statement:current"],
              note: null,
              verified_at: NOW,
            },
          ],
          assumptions: [],
          options: [],
          risks: [],
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
              content: "未来 7 天完成一门 SQL 入门课的课程作业。",
              evidence_refs: ["statement:current"],
              note: null,
              verified_at: NOW,
              verification: "完成后可检验对 SQL 基础的掌握。",
            },
          ],
          boundary_statement: "本规划不构成就业、薪酬或录取保证，也不替代持证职业顾问。",
          open_questions: ["行业报告的统计口径未完全披露，建议进一步核查。"],
        },
        evidence_sources: [
          {
            evidence_id: "statement:current",
            kind: "user_statement",
            title: "用户本次陈述",
            locator: null,
            url: null,
            summary: content,
            accessed_at: NOW,
            stale: false,
          },
        ],
        review: { passed: true, reviews: [], boundary_violations: [], warnings: [] },
        process_state: "done",
        process_steps: ["编译可用证据", "生成六类规划结果", "复核证据与边界"],
        error_code: null,
        error_message: null,
        created_at: NOW,
      },
    };
    state.messages[state.messages.length - 1] = final;
    return {
      part1,
      part2:
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
          message: final,
        }),
    };
  };

  await page.route("**/api/chat/conversations/mock-1/messages", async (route) => {
    if (route.request().method() !== "POST") return;
    const body = JSON.parse(route.request().postData() ?? "{}");
    const parts = buildTurnStreams(String(body.content ?? ""));
    turnParts.set(state.messageId, parts);
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

  // events 回放：闸门未释放只回放已发生段（part1，无终态）；释放后全量
  const streamForMessage = (messageId: string): string => {
    const parts = turnParts.get(messageId);
    if (!parts) return "";
    return state.gateReleased ? parts.part1 + parts.part2 : parts.part1;
  };

  installRunEventsRoutes(page, streamForMessage, "mock-1");
  installRunEventsRoutes(page, streamForMessage, "mock-2");

  return {
    messageId: () => state.messageId,
    postCount: () => state.postCount,
    // 释放闸门：之后的事件订阅回放全量，历史 GET 返回终态投影
    releaseGate: () => {
      state.gateReleased = true;
    },
  };
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

/** 通过「生涯规划助手」对话框发送问题。 */
async function sendViaDialog(page: Page, question: string) {
  await page.getByRole("button", { name: "更多功能" }).click();
  await page.getByRole("menuitem", { name: "生涯规划助手" }).click();
  await expect(page.getByRole("dialog", { name: /生涯规划助手/ })).toBeVisible();
  await page.getByTestId("career-question-input").fill(question);
  await page.getByTestId("career-submit").click();
}

// ---------------------------------------------------------------------------
// 用例 1：信息不足 → 快速澄清（不启动完整规划生成）
// ---------------------------------------------------------------------------

test("模糊请求立即澄清：过程卡 clarify 态，正文即澄清问题，无规划结果", async ({ page }) => {
  await freshAccount(page, "i09-clarify");
  const question = "为了给你可执行的规划，先告诉我：你目前最想探索哪个方向？比如数据分析、产品、算法、运营或其他。";
  const mock = await installResilienceMockApi(page, { clarifyQuestion: question });
  await openChat(page);

  // 对话框自动注入「生涯规划助手：」前缀
  await sendViaDialog(page, "帮我规划一下");

  // 澄清事件先到达：过程卡进入「需要补充信息」（澄清态，阶段停在「收集信息」）
  await expect(page.getByTestId("career-process-clarify")).toBeVisible();
  await expect(page.getByTestId("career-process-clarify")).toContainText("需要补充一个关键信息");
  await expect(page.getByTestId("career-process-clarify")).toContainText(question);
  await expect(page.getByTestId("career-process-clarify")).toContainText("直接回复你的答案即可继续规划");

  // 释放闸门并重开页面：历史返回终态澄清投影，结果卡接管（无六类规划）
  mock.releaseGate();
  await page.goto("/chat/mock-1");
  await expect(page.getByTestId("career-result-card")).toContainText("生涯规划完成");

  // 消息正文 = 澄清问题
  await expect(page.getByTestId("chat-thread")).toContainText(question);

  // 澄清展开分支：补充关键信息说明
  await page.getByTestId("career-result-card").getByRole("button").click();
  await expect(page.getByTestId("career-clarification")).toContainText("需要补充一个关键信息");
  await expect(page.getByTestId("career-clarification")).toContainText(question);

  expect(mock.postCount()).toBe(1);
});

// ---------------------------------------------------------------------------
// 用例 2：形成路径阶段切会话再返回 → 同一 run 完成
// ---------------------------------------------------------------------------

test("形成路径阶段切到另一会话再返回：同一 run 继续完成，不重复发送", async ({ page }) => {
  await freshAccount(page, "i09-switch");
  const mock = await installResilienceMockApi(page);
  await openChat(page);

  await sendViaDialog(page, "我大二在读计算机科学，喜欢数据分析，怎么规划接下来的方向");

  // 生成进行中：过程卡进入「形成路径」（阶段 2 高亮，尚未完成）
  await expect(page.getByTestId("career-process-loading")).toBeVisible();
  await expect(page.getByTestId("career-process-loading")).toContainText("正在生成六类规划结果");

  // 切到另一会话：页面离开订阅，run 仍在后台执行（闸门未释放）
  await page.goto("/chat/mock-2");
  await expect(page.getByTestId("chat-thread")).toBeVisible();

  // 返回原会话（重开）：历史仍为 streaming，订阅回放已发生段——
  // 过程卡恢复「形成路径」阶段轨迹，不重复发送
  await page.goto("/chat/mock-1");
  await expect(page.getByTestId("chat-thread")).toBeVisible();
  await expect(page.getByTestId("career-process-loading")).toBeVisible();
  await expect(page.getByTestId("career-process-loading")).toContainText("正在生成六类规划结果");

  // 释放闸门并重开：历史返回终态投影，同一 run 完成
  mock.releaseGate();
  await page.goto("/chat/mock-1");
  await expect(page.getByTestId("career-result-card")).toBeVisible();
  await expect(page.getByTestId("career-result-card")).toContainText("生涯规划完成");
  await expect(page.getByTestId("chat-thread")).toContainText("综合你的阶段与目标");

  // 同一 run：只有一次发送（切会话/重开不产生第二个模型调用）
  expect(mock.postCount()).toBe(1);
});
