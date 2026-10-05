import { expect, test, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

/**
 * 工单 38：正式界面展示真实进度、可信结果与恢复操作。
 *
 * 两个纵切：
 * - 终态历史（真实 API 读取语义，页面 route mock 只替换读取响应）：展示
 *   请求模块与实际能力分开、已交付/被阻塞结果块、可信状态与真实恢复；
 * - 真实 API + 后台执行器 + SSE（零 mock）：确定性脚本注入分块正文，
 *   观察真实节点进度，键盘 Esc 停止后不自动续跑，刷新与落库一致。
 *
 * 三个既有桌面验收视口全部覆盖。
 */

const PASSWORD = "correct-horse-issue38";
const API_PORT = process.env.API_PORT || "8000";
const API_ORIGIN = `http://127.0.0.1:${API_PORT}`;
const CONVERSATION_ID = "conv-issue38";
const NOW = "2026-10-05T08:00:00Z";

const VIEWPORTS = [
  { width: 1280, height: 720 },
  { width: 1440, height: 900 },
  { width: 1920, height: 1080 },
] as const;

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
    title: "工单 38 结果投影",
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

for (const viewport of VIEWPORTS) {
  test.describe(`工单 38 · ${viewport.width}×${viewport.height}`, () => {
    test.use({ viewport });
    test.describe.configure({ mode: "serial" });

    test("终态历史：实际能力、交付/阻塞与真实恢复如实呈现", async ({ page }) => {
      const credentials = uniqueCredentials(`i38m${viewport.width}`);
      await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);
      await mockHistory(page, [
        message("u-1", "user", "帮我找 Java 岗位并配学习资料和练手项目"),
        message(
          "a-1",
          "assistant",
          "先交付岗位分析与资料清单；GitHub 分支本轮失败。",
          {
            turn_result: turnResult({
              outcome: "partial",
              outcome_label: "本轮先交付有效部分，其余未完成。",
              trust: "evidence_bound",
              trust_label: "结果已附证据，仍有未完成核验的范围。",
              requested_module_id: "career",
              actual_module_id: "career",
              capability_list: ["career", "resources", "github"],
              route_source: "body_intent",
              delivered: [
                { module_id: "career", label: "职业规划", state: "success", trust: "qualified", detail: "" },
                { module_id: "resources", label: "学习资料", state: "links_only", trust: "evidence_bound", detail: "" },
              ],
              blocked: [
                { module_id: "github", label: "GitHub 项目推荐", state: "error", trust: "evidence_bound", detail: "GitHub 接口限流" },
              ],
              gaps: ["GitHub 接口限流"],
            }),
          }
        ),
        message("u-2", "user", "那先重试一下联网搜索"),
        message("a-2", "assistant", "本轮未完成。", {
          status: "error",
          error_message: "搜索请求过于频繁。",
          turn_result: turnResult({
            outcome: "failed",
            outcome_label: "本轮未完成。",
            trust: null,
            trust_label: null,
            capability_list: [],
            route_source: null,
            recovery: {
              action: "wait",
              label: "请稍后重试。",
              retryable: true,
              available_after: "2026-10-05T12:00:00+00:00",
            },
          }),
        }),
      ]);

      await page.goto(`/chat/${CONVERSATION_ID}`);

      const cards = page.getByTestId("turn-result");
      await expect(cards).toHaveCount(2);

      const partial = page.locator('[data-outcome="partial"]');
      await expect(partial).toBeVisible();
      await expect(partial.getByTestId("turn-result-outcome")).toContainText(
        "本轮先交付有效部分"
      );
      await expect(partial.getByTestId("turn-result-trust")).toContainText(
        "结果已附证据"
      );
      // 请求模块提示与实际执行分开；多能力时如实列出。
      await expect(partial.getByTestId("turn-result-capability")).toContainText(
        "实际执行：职业规划"
      );
      await expect(partial.getByTestId("turn-result-delivered")).toContainText(
        "职业规划（已完成）"
      );
      await expect(partial.getByTestId("turn-result-delivered")).toContainText(
        "学习资料（仅有链接）"
      );
      await expect(partial.getByTestId("turn-result-blocked")).toContainText(
        "GitHub 项目推荐（失败）：GitHub 接口限流"
      );
      await expect(partial.getByTestId("turn-result-gaps")).toContainText(
        "GitHub 接口限流"
      );
      // 部分交付不伪造可点击恢复。
      await expect(partial.getByTestId("turn-result-retry")).toHaveCount(0);

      // 可重试失败：真实恢复文案 + 用户时区可用时间 + 键盘可达的继续入口。
      const failed = page.locator('[data-outcome="failed"]');
      await expect(failed).toBeVisible();
      const recovery = failed.getByTestId("turn-result-recovery");
      await expect(recovery).toContainText("请稍后重试。");
      await expect(recovery).toContainText("可在此时间后重试：");
      const retryButton = failed.getByTestId("turn-result-retry");
      await expect(retryButton).toBeVisible();
      await retryButton.focus();
      await expect(retryButton).toBeFocused();

      // 不泄露内部细节：页面不出现待核验草稿/思维链/评分依据文案。
      await expect(page.getByText("私有评分依据")).toHaveCount(0);

      // 三视口布局约束：结果卡不造成横向溢出。
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - window.innerWidth
      );
      expect(overflow).toBeLessThanOrEqual(1);
    });

    test("真实流式：观察到真实进度，Esc 停止后不自动续跑且刷新一致", async ({
      page,
    }) => {
      const credentials = uniqueCredentials(`i38r${viewport.width}`);
      await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);

      await configureScript(page, {
        match: "甲 88 ms",
        chunks: [
          "第一段：甲 8",
          "8 ms；",
          "第二段：乙 99 ms。",
          "结尾不应出现。",
        ],
        delay_ms: 1200,
      });
      const conversationId = await sendFromHome(
        page,
        "按原文整理，不要改数值：甲 88 ms；乙 99 ms。"
      );

      const thread = page.getByTestId("chat-thread");
      // 真实节点/阶段进度在流式期间可见（排队中/校验回合/生成回答等）。
      await expect(
        thread.getByText(/排队中|校验回合|编译上下文|生成回答|搜索公开来源|核验引用与质量/).first()
      ).toBeVisible({ timeout: 30_000 });

      const stopButton = page.getByRole("button", { name: "停止生成" });
      await expect(stopButton).toBeVisible({ timeout: 15_000 });
      await expect(thread).toContainText("第一段：甲 8", { timeout: 30_000 });

      // 键盘停止：输入区 Esc 触发与停止按钮同一路径。
      await page.getByTestId("composer").getByLabel("输入消息").focus();
      await page.keyboard.press("Escape");

      await expect
        .poll(async () => (await getLastAssistant(page, conversationId)).status, {
          timeout: 30_000,
        })
        .toBe("stopped");
      const stopped = await getLastAssistant(page, conversationId);
      expect(stopped.content).not.toContain("结尾不应出现");
      // 服务端读取投影：取消有正式分类与停止文案（不进行下次自动调用）。
      expect(stopped.turn_result?.outcome).toBe("cancelled");
      expect(stopped.turn_result?.outcome_label).toContain("不会自动继续");

      // 停止后无新调用：等待迟到分块窗口后仍是同一尝试、同一助手消息。
      await page.waitForTimeout(2500);
      const after = await getConversation(page, conversationId);
      const assistants = after.messages.filter(
        (item) => item.role === "assistant"
      );
      expect(assistants).toHaveLength(1);
      expect(assistants[0].attempt_number).toBe(1);

      // 刷新后终态一致：停止说明与取消分类可读，正文保留部分内容。
      await page.reload();
      const stoppedLine = page.getByTestId("message-stopped");
      await expect(stoppedLine).toBeVisible({ timeout: 15_000 });
      await expect(stoppedLine).toContainText("不会自动继续");
      const card = page.locator('[data-outcome="cancelled"]');
      await expect(card).toBeVisible();
      await expect(thread).toContainText("第一段：甲 8");
      await expect(thread).not.toContainText("结尾不应出现");

      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - window.innerWidth
      );
      expect(overflow).toBeLessThanOrEqual(1);
    });
  });
}
