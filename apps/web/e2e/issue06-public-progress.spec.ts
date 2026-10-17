import { expect, test, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

/**
 * 工单 06：公开过程集中为一行，默认收起，展开后查看公开步骤、来源说明
 * 与本轮上下文披露。
 *
 * 历史投影用页面路由替换读取响应，验证服务端投影的呈现边界与敏感信息
 * 负例；真实流式用测试服务端的脚本化聊天适配器，经正式 API、后台执行器、
 * SQLite 与 SSE 链路验证节点/阶段、展开选择保持和终态衔接。
 */

const PASSWORD = "correct-horse-issue06";
const API_PORT = process.env.API_PORT || "8000";
const API_ORIGIN = `http://127.0.0.1:${API_PORT}`;
const NOW = "2026-10-17T08:00:00Z";

interface HistoryMessage {
  message_id: string;
  role: "user" | "assistant";
  attempt_number?: number;
  status?: string;
  content: string;
  thinking?: unknown;
  context_note?: unknown;
  turn_result?: unknown;
  error_message?: string | null;
  duration_ms?: number | null;
  [key: string]: unknown;
}

function userMessage(id: string, content: string): HistoryMessage {
  return {
    message_id: id,
    conversation_id: "conv-issue06",
    role: "user",
    attempt_number: 1,
    status: "done",
    content,
    attachments: [],
    created_at: NOW,
    updated_at: NOW,
  };
}

function assistantMessage(
  id: string,
  content: string,
  overrides: Partial<HistoryMessage> = {}
): HistoryMessage {
  return {
    message_id: id,
    conversation_id: "conv-issue06",
    role: "assistant",
    attempt_number: 1,
    status: "done",
    content,
    attachments: [],
    thinking: null,
    context_note: null,
    turn_result: null,
    error_code: null,
    error_message: null,
    duration_ms: 1200,
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  };
}

function turnResult(
  outcome: string,
  outcomeLabel: string,
  overrides: Record<string, unknown> = {}
) {
  return {
    version: "turn-result-v1",
    outcome,
    outcome_label: outcomeLabel,
    trust: null,
    trust_label: null,
    capability_list: [],
    actual_module_id: null,
    requested_module_id: null,
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

async function mockConversation(
  page: Page,
  conversationId: string,
  messages: HistoryMessage[]
): Promise<void> {
  const history = {
    conversation_id: conversationId,
    title: "公开过程验收",
    mode: "companion",
    pinned: false,
    project_id: null,
    created_at: NOW,
    updated_at: NOW,
    messages,
    mode_events: [],
  };
  await page.route(
    `**/api/chat/conversations/${conversationId}`,
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(history),
      });
    }
  );
}

async function sendFromHome(page: Page, text: string): Promise<string> {
  const composer = page.getByTestId("composer");
  await composer.getByLabel("输入消息").fill(text);
  await composer.getByRole("button", { name: "发送消息" }).click();
  await expect(page).toHaveURL(/\/chat\/[^/]+$/);
  return page.url().split("/chat/")[1].split(/[?#]/)[0];
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

test("历史回放默认收起，键盘展开后只显示本轮公开过程与上下文披露", async ({
  page,
}) => {
  const credentials = uniqueCredentials("i06history");
  await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);
  const conversationId = "conv-issue06-history";
  await mockConversation(page, conversationId, [
    userMessage("u-1", "请总结这轮资料。"),
    assistantMessage("a-old", "旧的部分回答。", {
      attempt_number: 1,
      status: "error",
      error_message: "旧尝试失败。",
      thinking: {
        steps: ["旧轮步骤"],
        evidence: ["旧轮来源"],
        tools: [],
        quality: [],
      },
      context_note: {
        state: "off",
        profile_enabled: false,
        mode: "companion",
        used_at: NOW,
        profile_item_count: 0,
        material_categories: [],
        note: "旧轮未使用背景信息。",
      },
    }),
    assistantMessage("a-1", "本轮回答正文。", {
      attempt_number: 2,
      thinking: {
        steps: ["新轮步骤", "核对引用"],
        evidence: ["采用 2 篇论文"],
        tools: ["arXiv 检索完成"],
        quality: ["引用已核验"],
        raw_chain_of_thought: "隐藏思维链原文",
      },
      duration_ms: 2400,
      context_note: {
        state: "ready",
        profile_enabled: true,
        mode: "companion",
        used_at: NOW,
        profile_item_count: 2,
        material_categories: ["知识库材料"],
        note: "本轮采用了 2 条背景信息，仅用于当前任务。",
        profile_items: [{ content: "敏感画像原文" }],
        raw_reasoning: "内部推理原文",
      },
      turn_result: turnResult("partial", "本轮先交付有效部分，其余未完成。", {
        trust: "evidence_bound",
        trust_label: "结果已附证据。",
        delivered: [
          {
            module_id: "paper",
            label: "论文检索",
            state: "success",
            trust: "qualified",
            detail: "",
          },
        ],
        blocked: [
          {
            module_id: "tieba",
            label: "贴吧搜集",
            state: "error",
            trust: "evidence_bound",
            detail: "接口失败",
          },
        ],
        gaps: ["贴吧来源未读取"],
      }),
    }),
  ]);

  await page.goto(`/chat/${conversationId}`);

  const process = page.getByTestId("public-process");
  await expect(process).toHaveCount(1);
  await expect(page.getByText("旧轮步骤")).toHaveCount(0);
  await expect(page.getByTestId("thinking-summary")).toHaveCount(0);
  await expect(page.getByTestId("context-note-card")).toHaveCount(0);
  await expect(process.getByTestId("public-process-toggle")).toHaveAttribute(
    "aria-expanded",
    "false"
  );
  await expect(process.getByTestId("public-process-details")).toHaveCount(0);
  await expect(process.getByTestId("public-process-label")).toHaveText(
    "回答已生成 · 用时 2 秒"
  );
  await expect(page.getByText("此问题的前 1 次尝试")).toBeVisible();
  await expect(page.getByTestId("turn-result")).toContainText(
    "本轮先交付有效部分"
  );
  await expect(page.getByTestId("turn-result-blocked")).toContainText(
    "接口失败"
  );

  const toggle = process.getByTestId("public-process-toggle");
  await toggle.focus();
  await page.keyboard.press("Enter");
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await expect(toggle).toBeFocused();

  const details = process.getByTestId("public-process-details");
  await expect(details).toContainText("公开步骤");
  await expect(details).toContainText("新轮步骤");
  await expect(details).toContainText("来源说明");
  await expect(details).toContainText("采用 2 篇论文");
  await expect(details).toContainText("工具进度");
  await expect(details).toContainText("质量检查");
  await expect(details).toContainText("引用已核验");
  await expect(details).toContainText("本次上下文说明");
  await expect(details).toContainText("仅用于当前任务");
  await expect(details).toContainText("知识库材料");
  await expect(details).not.toContainText("旧轮步骤");
  await expect(details).not.toContainText("旧轮来源");
  await expect(details).not.toContainText("敏感画像原文");
  await expect(details).not.toContainText("内部推理原文");
  await expect(details).not.toContainText("隐藏思维链原文");
  await expect(
    details.getByRole("button", { name: "这次回答不合适" })
  ).toBeVisible();

  await page.keyboard.press("Enter");
  await expect(process.getByTestId("public-process-details")).toHaveCount(0);
});

test("真实流式：单行真实进度、展开选择保持、终态与历史一致", async ({
  page,
}) => {
  const credentials = uniqueCredentials("i06stream");
  await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);
  const match = "公开过程验收";
  await configureScript(page, {
    match,
    chunks: ["第一段：公开过程。", "第二段：真实进度。", "最后一段。"],
    delay_ms: 900,
  });

  const conversationId = await sendFromHome(page, match);
  const thread = page.getByTestId("chat-thread");

  const process = page.getByTestId("public-process").last();
  await expect(process).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("thinking-summary")).toHaveCount(0);
  await expect(page.getByTestId("context-note-card")).toHaveCount(0);
  await expect(process.getByTestId("public-process-toggle")).toHaveAttribute(
    "aria-expanded",
    "false"
  );
  await expect(process.getByTestId("public-process-details")).toHaveCount(0);
  // 订阅可能晚于已落库的节点/阶段事件，此时必须使用诚实的通用状态；
  // 实际节点/阶段映射由 chat-run 与 PublicProcess 单测覆盖。
  await expect(process.getByTestId("public-process-label")).toHaveText(
    /^(校验回合|编译上下文|选择模块|生成回答|生成回答中|检索本地资料|搜索公开来源|核验引用与质量|修复与重试|整理收尾|正在生成回答)…$/
  );

  const composerInput = page.getByTestId("composer").getByLabel("输入消息");
  await composerInput.focus();
  await expect(thread).toContainText("第一段：公开过程。", { timeout: 30_000 });
  await expect(composerInput).toBeFocused();

  const toggle = process.getByTestId("public-process-toggle");
  await toggle.focus();
  await page.keyboard.press("Enter");
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await expect(process.getByTestId("public-process-details")).toContainText(
    "回答完成后可查看本轮上下文说明"
  );

  await expect(thread).toContainText("最后一段。", { timeout: 30_000 });
  await expect
    .poll(
      async () => {
        const response = await page.request.get(
          `/api/chat/conversations/${conversationId}`
        );
        expect(response.ok()).toBeTruthy();
        const conversation = (await response.json()) as {
          messages: Array<{ role: string; status: string }>;
        };
        return conversation.messages.filter((item) => item.role === "assistant").at(-1)
          ?.status;
      },
      { timeout: 30_000 }
    )
    .toBe("done");

  // 用户已经展开的选择不被终态历史接管重置。
  await expect(process.getByTestId("public-process-details")).toBeVisible();
  await expect(process.getByTestId("public-process-label")).toHaveText(
    /回答已生成/
  );
  await expect(process.getByTestId("context-note-details")).not.toContainText(
    "回答完成后可查看本轮上下文说明",
    { timeout: 15_000 }
  );
  await expect(process.getByTestId("context-note-details")).toContainText(
    /已使用授权用户背景信息|未匹配到相关授权用户背景信息|本轮未使用授权用户背景信息|暂时无法整理授权用户背景信息|本轮没有上下文披露/,
    { timeout: 15_000 }
  );
  await expect(process.getByTestId("context-note-details")).not.toContainText(
    "加载中"
  );

  // 历史回放重新默认收起，且仍只有一处公开过程入口。
  await page.reload();
  const reloaded = page.getByTestId("public-process");
  await expect(reloaded).toHaveCount(1);
  await expect(reloaded.getByTestId("public-process-toggle")).toHaveAttribute(
    "aria-expanded",
    "false"
  );
  await expect(reloaded.getByTestId("public-process-details")).toHaveCount(0);
  await expect(reloaded.getByTestId("public-process-label")).toHaveText(
    /回答已生成/
  );
  await reloaded.getByTestId("public-process-toggle").click();
  await expect(reloaded.getByTestId("public-process-details")).toContainText(
    "本次上下文说明"
  );
  await expect(reloaded.getByTestId("context-note-details")).not.toContainText(
    "加载中"
  );
});

test("停止、失败与历史无披露都有终态及上下文处理", async ({ page }) => {
  const credentials = uniqueCredentials("i06states");
  await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);
  const conversationId = "conv-issue06-states";
  await mockConversation(page, conversationId, [
    userMessage("u-stop", "停止这一轮。"),
    assistantMessage("a-stop", "停止前已生成的部分。", {
      status: "stopped",
      thinking: {
        steps: ["停止前步骤"],
        evidence: [],
        tools: [],
        quality: ["已停止生成，保留已生成内容。"],
      },
      context_note: {
        state: "off",
        profile_enabled: false,
        mode: "companion",
        used_at: NOW,
        profile_item_count: 0,
        material_categories: [],
        note: "本轮未使用用户背景信息。",
      },
      turn_result: turnResult("cancelled", "本轮已停止，不会自动继续。"),
    }),
    userMessage("u-error", "这一轮失败。"),
    assistantMessage("a-error", "", {
      status: "error",
      error_message: "生成服务暂时不可用。",
      thinking: {
        steps: ["失败前步骤"],
        evidence: [],
        tools: [],
        quality: ["生成失败，已保留已完成部分。"],
      },
      context_note: {
        state: "error",
        profile_enabled: true,
        mode: "companion",
        used_at: NOW,
        profile_item_count: 0,
        material_categories: [],
        note: "上下文整理失败，本轮未注入用户背景信息。",
      },
      turn_result: turnResult("failed", "本轮未完成。"),
    }),
    userMessage("u-none", "没有披露的一轮。"),
    assistantMessage("a-none", "普通回答。", {
      thinking: null,
      context_note: null,
    }),
  ]);

  await page.goto(`/chat/${conversationId}`);

  const processes = page.getByTestId("public-process");
  await expect(processes).toHaveCount(3);
  await expect(processes.nth(0).getByTestId("public-process-label")).toHaveText(
    "本轮已停止"
  );
  await expect(processes.nth(1).getByTestId("public-process-label")).toHaveText(
    "本轮生成失败"
  );
  await expect(processes.nth(2).getByTestId("public-process-label")).toHaveText(
    "回答已生成"
  );
  await expect(
    page.getByTestId("chat-thread").getByRole("alert")
  ).toContainText("生成服务暂时不可用");
  await expect(page.locator('[data-outcome="cancelled"]')).toContainText(
    "不会自动继续"
  );
  await expect(page.locator('[data-outcome="failed"]')).toContainText(
    "本轮未完成"
  );

  for (const index of [0, 1, 2]) {
    await expect(processes.nth(index).getByTestId("public-process-details")).toHaveCount(
      0
    );
  }

  await processes.nth(0).getByTestId("public-process-toggle").click();
  await expect(
    processes.nth(0).getByTestId("context-note-details")
  ).toContainText("本轮未使用授权用户背景信息");
  await processes.nth(1).getByTestId("public-process-toggle").click();
  await expect(
    processes.nth(1).getByTestId("context-note-details")
  ).toContainText("暂时无法整理授权用户背景信息");
  await processes.nth(2).getByTestId("public-process-toggle").click();
  await expect(
    processes.nth(2).getByTestId("context-note-details")
  ).toContainText("本轮没有上下文披露");
  await expect(processes.nth(2).getByTestId("context-note-details")).not.toContainText(
    "加载中"
  );
});
