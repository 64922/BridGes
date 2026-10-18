import { expect, test, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

/**
 * 工单 07：交付状态、失败与真实恢复。
 *
 * 第一条纵切用页面读取语义固定七类终态组合中的四类（部分交付、不可重试、
 * 冷却、待补充）以及重试历史；第二条纵切走正式 API + 后台执行器 + SSE +
 * 持久化，真实验证停止竞态、迟到分块与重试后的尝试追溯。
 */

const PASSWORD = "correct-horse-issue07";
const API_PORT = process.env.API_PORT || "8000";
const API_ORIGIN = `http://127.0.0.1:${API_PORT}`;
const CONVERSATION_ID = "conv-issue07";
const NOW = "2026-10-05T08:00:00Z";

function turnResult(overrides: Record<string, unknown> = {}) {
  return {
    version: "turn-result-v1",
    outcome: "complete",
    outcome_label: "本轮已完成。",
    trust: "qualified",
    trust_label: "结果已通过核验。",
    requested_module_id: null,
    actual_module_id: null,
    capability_list: [],
    route_source: "ordinary_chat",
    delivered: [],
    blocked: [],
    gaps: [],
    recovery: null,
    wait_reason: null,
    task_id: null,
    task_version: null,
    ...overrides,
  };
}

function message(
  id: string,
  role: "user" | "assistant",
  content: string,
  overrides: Record<string, unknown> = {}
) {
  return {
    message_id: id,
    conversation_id: CONVERSATION_ID,
    role,
    attempt_number: 1,
    status: "done",
    content,
    attachments: [],
    thinking: null,
    retrieval: null,
    retrieval_decision: null,
    web_search: null,
    arxiv_search: null,
    route: null,
    teaching: null,
    context_note: null,
    skill: null,
    humanizer: null,
    career_planning: null,
    image: null,
    video: null,
    mcp_call: null,
    read_aloud: null,
    error_code: null,
    error_message: null,
    turn_result: null,
    duration_ms: role === "assistant" ? 120 : null,
    model_id: role === "assistant" ? "qwen3.7-plus-2026-05-26" : null,
    run_lock_id: null,
    active_run: null,
    module_id: null,
    paper_search: null,
    module_suggestion: null,
    tieba_research: null,
    career_plan: null,
    learning_resources: null,
    commute_route: null,
    github_projects: null,
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  };
}

async function mockHistory(page: Page, messages: unknown[]): Promise<void> {
  const history = {
    conversation_id: CONVERSATION_ID,
    title: "工单 07 交付状态",
    mode: "companion",
    pinned: false,
    project_id: null,
    created_at: NOW,
    updated_at: NOW,
    messages,
    mode_events: [],
  };
  await page.route("**/api/chat/conversations", async (route) => {
    await route.fulfill({
      status: route.request().method() === "POST" ? 201 : 200,
      contentType: "application/json",
      body: JSON.stringify(
        route.request().method() === "POST" ? history : { conversations: [] }
      ),
    });
  });
  await page.route(`**/api/chat/conversations/${CONVERSATION_ID}`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(history),
    });
  });
}

interface ApiMessage {
  message_id: string;
  role: "user" | "assistant";
  status: string;
  content: string;
  attempt_number: number;
  turn_result?: { outcome: string; outcome_label: string } | null;
}

async function getConversation(page: Page, conversationId: string) {
  const response = await page.request.get(
    `/api/chat/conversations/${conversationId}`
  );
  expect(response.ok()).toBeTruthy();
  return (await response.json()) as { messages: ApiMessage[] };
}

async function getLastAssistant(
  page: Page,
  conversationId: string
): Promise<ApiMessage> {
  const conversation = await getConversation(page, conversationId);
  const assistants = conversation.messages.filter(
    (item) => item.role === "assistant"
  );
  expect(assistants.length).toBeGreaterThan(0);
  return assistants[assistants.length - 1];
}

async function configureScript(
  page: Page,
  script: { match: string; chunks: string[]; delay_ms?: number }
): Promise<void> {
  const response = await page.request.post(
    `${API_ORIGIN}/_test/chat-stream-script`,
    { data: script }
  );
  expect(response.ok()).toBeTruthy();
}

async function sendFromHome(page: Page, text: string): Promise<string> {
  const composer = page.getByTestId("composer");
  await composer.getByLabel("输入消息").fill(text);
  await composer.getByRole("button", { name: "发送消息" }).click();
  await expect(page).toHaveURL(/\/chat\/[^/]+$/);
  return page.url().split("/chat/")[1].split(/[?#]/)[0];
}

test("终态说明：部分交付、不可重试、冷却与待补充不伪造操作", async ({
  page,
}) => {
  const credentials = uniqueCredentials("i07m");
  await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);
  await mockHistory(page, [
    message("u-1", "user", "先给岗位分析，再补学习资料和练手项目"),
    message("a-1", "assistant", "上一轮网络中断。", {
      attempt_number: 1,
      status: "error",
      error_message: "上一轮网络中断。",
      turn_result: turnResult({
        outcome: "failed",
        outcome_label: "本轮未完成。",
        trust: null,
        trust_label: null,
      }),
    }),
    message("a-2", "assistant", "先交付岗位分析与资料清单。", {
      attempt_number: 2,
      turn_result: turnResult({
        outcome: "partial",
        outcome_label: "本轮先交付有效部分，其余未完成。",
        trust: "evidence_bound",
        trust_label: "结果已附证据，仍有未完成核验的范围。",
        delivered: [
          {
            module_id: "career",
            label: "职业规划",
            state: "success",
            trust: "qualified",
            detail: "",
          },
          {
            module_id: "resources",
            label: "学习资料",
            state: "links_only",
            trust: "evidence_bound",
            detail: "",
          },
        ],
        blocked: [
          {
            module_id: "github",
            label: "GitHub 项目推荐",
            state: "error",
            trust: "evidence_bound",
            detail: "GitHub 接口限流",
          },
        ],
        gaps: ["GitHub 接口限流", "其余来源等待上游恢复"],
      }),
    }),
    message("u-2", "user", "失败的那部分还能重试吗？"),
    message("a-3", "assistant", "本轮未完成。", {
      status: "error",
      error_code: "model_request_rejected",
      error_message: "模型拒绝了本次请求，重试不会恢复。",
      turn_result: turnResult({
        outcome: "failed",
        outcome_label: "本轮未完成。",
        trust: null,
        trust_label: null,
      }),
    }),
    message("u-3", "user", "搜索太频繁了"),
    message("a-4", "assistant", "本轮未完成。", {
      status: "error",
      error_message: "搜索请求过于频繁。",
      turn_result: turnResult({
        outcome: "failed",
        outcome_label: "本轮未完成。",
        trust: null,
        trust_label: null,
        recovery: {
          action: "wait",
          label: "请稍后重试。",
          retryable: true,
          available_after: "2099-10-05T12:00:00+00:00",
        },
      }),
    }),
    message("u-4", "user", "我补充方向：后端"),
    message("a-5", "assistant", "等待你补充目标岗位方向后继续。", {
      turn_result: turnResult({
        outcome: "needs_input",
        outcome_label: "等待你补充信息后继续；任务保持待输入。",
        trust: null,
        trust_label: null,
        wait_reason: "请补充目标岗位方向",
      }),
    }),
  ]);

  await page.goto(`/chat/${CONVERSATION_ID}`);

  const notes = page.getByTestId("delivery-status");
  await expect(notes).toHaveCount(4);

  const partial = page.locator('[data-outcome="partial"]');
  await expect(partial).toContainText("本轮先交付有效部分");
  await expect(partial).toContainText("结果已附证据");
  await expect(partial.getByTestId("delivery-delivered")).toContainText(
    "职业规划（已完成）"
  );
  await expect(partial.getByTestId("delivery-delivered")).toContainText(
    "学习资料（仅有链接）"
  );
  await expect(partial.getByTestId("delivery-blocked")).toContainText(
    "GitHub 项目推荐（失败）：GitHub 接口限流"
  );
  const gaps = partial.getByTestId("delivery-gaps");
  await expect(gaps).toContainText("其余来源等待上游恢复");
  await expect(gaps).not.toContainText("GitHub 接口限流");
  await expect(partial.getByTestId("delivery-retry")).toHaveCount(0);
  await expect(
    page
      .getByTestId("assistant-message")
      .filter({ hasText: "先交付岗位分析与资料清单" })
      .getByRole("button", { name: "重新生成" })
  ).toHaveCount(0);

  // 重试继承原问题与旧尝试；新回答标出第 2 次尝试。
  await expect(page.getByText("第 2 次尝试", { exact: true })).toBeVisible();
  await expect(page.getByText("此问题的前 1 次尝试")).toBeVisible();
  await expect(
    page.getByText("先给岗位分析，再补学习资料和练手项目", { exact: true })
  ).toHaveCount(1);

  // 不可重试失败只说明终态，不出现可点击恢复。
  const nonRetryable = page
    .getByTestId("assistant-message")
    .filter({ hasText: "模型拒绝了本次请求" });
  await expect(nonRetryable.getByTestId("delivery-outcome")).toContainText(
    "本轮未完成"
  );
  await expect(nonRetryable.getByTestId("delivery-retry")).toHaveCount(0);
  await expect(
    nonRetryable.getByRole("button", { name: "重新生成" })
  ).toHaveCount(0);

  // 冷却未到时按钮不可执行；到时只解除禁用，不自动请求。
  const cooling = page
    .getByTestId("assistant-message")
    .filter({ hasText: "搜索请求过于频繁" });
  const coolingRecovery = cooling.getByTestId("delivery-recovery");
  await expect(coolingRecovery).toContainText("请稍后重试");
  await expect(coolingRecovery).toContainText("可在此时间后重试：");
  await expect(cooling.getByTestId("delivery-retry")).toBeDisabled();

  // 待补充只解释等待原因，不把“重新生成”当作补充入口。
  const waiting = page
    .getByTestId("assistant-message")
    .filter({ hasText: "等待你补充" });
  await expect(waiting.getByTestId("delivery-outcome")).toContainText(
    "请补充目标岗位方向"
  );
  await expect(waiting.getByTestId("delivery-retry")).toHaveCount(0);
  await expect(waiting.getByRole("button", { name: "重新生成" })).toHaveCount(0);

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth
  );
  expect(overflow).toBeLessThanOrEqual(1);
});

test("真实补充：待输入说明保留，用户补充后继续同一轮", async ({ page }) => {
  const credentials = uniqueCredentials("i07n");
  await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);

  const conversationId = await sendFromHome(page, "帮我找几篇论文");
  await expect
    .poll(async () => (await getLastAssistant(page, conversationId)).status, {
      timeout: 30_000,
    })
    .toBe("done");
  const pending = await getLastAssistant(page, conversationId);
  expect(pending.turn_result?.outcome).toBe("needs_input");
  const waiting = page.locator('[data-outcome="needs_input"]');
  await expect(waiting).toContainText("等待你补充");
  await expect(waiting.getByTestId("delivery-retry")).toHaveCount(0);

  const composer = page.getByTestId("composer");
  await composer.getByLabel("输入消息").fill("Transformer 架构");
  await composer.getByRole("button", { name: "发送消息" }).click();
  await expect
    .poll(
      async () => {
        const conversation = await getConversation(page, conversationId);
        const assistants = conversation.messages.filter(
          (item) => item.role === "assistant"
        );
        return {
          count: assistants.length,
          status: assistants[assistants.length - 1]?.status,
        };
      },
      { timeout: 30_000 }
    )
    .toEqual({ count: 2, status: "done" });

  // 原问题与第一次待输入说明都保留，补充内容成为新的用户消息。
  await expect(waiting).toContainText("等待你补充");
  const thread = page.getByTestId("chat-thread");
  await expect(thread.getByText("帮我找几篇论文", { exact: true })).toHaveCount(1);
  await expect(
    thread.getByText("Transformer 架构", { exact: true })
  ).toHaveCount(1);
});

test("真实流式：停止后正文保留，继续生成产生可追溯的新尝试", async ({
  page,
}) => {
  const credentials = uniqueCredentials("i07r");
  await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);

  const query = "工单07停止竞态：请分两段回答。";
  await configureScript(page, {
    match: "工单07停止竞态",
    chunks: ["第一段已交付。", "第二段迟到不应出现在停止运行中。"],
    delay_ms: 900,
  });
  const conversationId = await sendFromHome(page, query);

  const thread = page.getByTestId("chat-thread");
  await expect(thread).toContainText("第一段已交付", { timeout: 30_000 });

  await expect(page.getByRole("button", { name: "停止生成" })).toBeVisible({
    timeout: 15_000,
  });
  await page.getByTestId("composer").getByLabel("输入消息").focus();
  await page.keyboard.press("Escape");

  await expect
    .poll(async () => (await getLastAssistant(page, conversationId)).status, {
      timeout: 30_000,
    })
    .toBe("stopped");
  const stopped = await getLastAssistant(page, conversationId);
  expect(stopped.content).toContain("第一段已交付");
  expect(stopped.content).not.toContain("第二段迟到");
  expect(stopped.turn_result?.outcome).toBe("cancelled");

  // 迟到分块窗口后仍是同一尝试，不自动续跑。
  await page.waitForTimeout(1800);
  const afterStop = await getConversation(page, conversationId);
  const stoppedAssistants = afterStop.messages.filter(
    (item) => item.role === "assistant"
  );
  expect(stoppedAssistants).toHaveLength(1);
  expect(stoppedAssistants[0].attempt_number).toBe(1);

  await page.reload();
  const stoppedNote = page.locator('[data-outcome="cancelled"]');
  await expect(stoppedNote).toBeVisible({ timeout: 15_000 });
  await expect(stoppedNote).toContainText("不会自动继续");
  await expect(thread).toContainText("第一段已交付");
  await expect(thread).not.toContainText("第二段迟到");

  // 真实恢复命令：重试生成新的助手尝试，原问题与旧尝试都保留。
  await stoppedNote.getByTestId("delivery-retry").click();
  await expect
    .poll(
      async () => {
        const conversation = await getConversation(page, conversationId);
        const assistants = conversation.messages.filter(
          (item) => item.role === "assistant"
        );
        return {
          count: assistants.length,
          attempt: assistants[assistants.length - 1]?.attempt_number,
          status: assistants[assistants.length - 1]?.status,
        };
      },
      { timeout: 30_000 }
    )
    .toEqual({ count: 2, attempt: 2, status: "done" });

  await expect(page.getByText("第 2 次尝试", { exact: true })).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByText("此问题的前 1 次尝试")).toBeVisible();
  await expect(thread.getByText(query, { exact: true })).toHaveCount(1);

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth
  );
  expect(overflow).toBeLessThanOrEqual(1);
});
