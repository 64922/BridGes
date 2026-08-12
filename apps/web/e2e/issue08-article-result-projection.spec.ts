import { expect, test, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

/**
 * Issue 08 — 正文优先的文章交付界面（协议级替身）。
 *
 * 通过 mock 历史消息携带 humanizer 结果投影，验证：成功无风险时首屏
 * 只有最终正文与复制入口（无固定前言/空事实核查段）、审计折叠按需展开
 * 带计数、硬门失败正文区域明确「未交付」且不显示违规稿、legacy 旧结果
 * 标注「旧版结果」、刷新后结果卡恢复。
 *
 * 真实本地 API 改写任务由显式启用的 smoke 覆盖（见
 * tests/humanizer/test_article_real_smoke.py）。
 */

const NOW = "2026-08-12T00:00:00Z";

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
  skill?: unknown;
  humanizer?: unknown;
}

const articleBase = {
  projection_version: "1",
  audit_version: "1",
  material_state: "sufficient",
};

function mockUser(id: string, content: string): MockMessage {
  return {
    message_id: id,
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
}

function cleanAssistant(): MockMessage {
  const finalText = "光合作用指的是植物把光能转化为化学能的过程，研究显示这一过程需要叶绿素吸收光子。";
  return {
    message_id: "a-clean",
    conversation_id: "mock-1",
    role: "assistant",
    attempt_number: 1,
    status: "done",
    content: finalText,
    error_code: null,
    error_message: null,
    duration_ms: 120,
    model_id: "qwen3.6-flash",
    run_lock_id: "lock-mock",
    created_at: NOW,
    updated_at: NOW,
    humanizer: {
      task_id: "a-clean",
      skill_id: "bridges-humanizer",
      skill_version: "1.0.0",
      path: "rewrite",
      genre: "popular_science",
      contract: { path: "rewrite", source_text: "原文" },
      status: "done",
      output: {
        final_text: finalText,
        edits: [],
        fact_check: [],
        open_questions: [],
        quality_status: "ok",
      },
      article: {
        ...articleBase,
        delivery_status: "delivered",
        final_text: finalText,
        fidelity: {
          passed: true,
          blocking_count: 0,
          needs_confirmation_count: 0,
          items: [],
        },
        style_review: {
          finding_count: 0,
          warning_count: 0,
          suggestion_count: 0,
          items: [],
        },
        revision: null,
        evidence: [],
        confirmations: [],
      },
    },
  };
}

function failedAssistant(): MockMessage {
  const assistant = cleanAssistant();
  assistant.message_id = "a-failed";
  assistant.status = "error";
  // skill 列非空（人味化投影存在于 skill 列）：正文区不走通用错误横幅，
  // 而由 Issue 08 的「未交付」分支接管（AC 4）。
  assistant.skill = {};
  assistant.content = "草稿正文：光合作用……（未交付候选）";
  assistant.error_code = "fidelity_gate_conflict";
  assistant.error_message = "来源保真硬门未通过，已停止交付：新增结论没有来源绑定。恢复方式：删除或修正无来源的新增内容后重试。";
  assistant.humanizer = {
    ...(assistant.humanizer as Record<string, unknown>),
    status: "error",
    output: null,
    error_code: "fidelity_gate_conflict",
    error_message: assistant.error_message,
    article: {
      ...articleBase,
      delivery_status: "failed",
      final_text: null,
      fidelity: {
        passed: false,
        blocking_count: 1,
        needs_confirmation_count: 0,
        items: [
          {
            code: "unattributed_claim",
            severity: "blocking",
            category: "无来源新增 claim",
            note: "新增结论没有来源绑定。",
          },
        ],
      },
      style_review: null,
      revision: null,
      evidence: [],
      confirmations: [],
    },
  };
  return assistant;
}

function legacyAssistant(): MockMessage {
  const assistant = cleanAssistant();
  assistant.message_id = "a-legacy";
  assistant.humanizer = {
    task_id: "a-legacy",
    skill_id: "bridges-humanizer",
    skill_version: "1.0.0",
    path: "rewrite",
    genre: null,
    contract: { path: "rewrite", source_text: "原文" },
    status: "done",
    output: {
      final_text: "旧版正文。",
      edits: [
        {
          edit_id: "ed-1",
          kind: "rewrite",
          original: "旧原文",
          revised: "旧改后",
          reason: "旧版理由",
        },
      ],
      fact_check: [{ item: "来源保真检查", result: "已核实", evidence: "账本通过" }],
      open_questions: [],
    },
  };
  return assistant;
}

async function installMockHistory(page: Page, assistant: MockMessage): Promise<void> {
  const history = {
    conversation_id: "mock-1",
    title: "人味化测试",
    mode: "companion",
    created_at: NOW,
    updated_at: NOW,
    messages: [mockUser("u-1", "请改写这段原文"), assistant],
  };
  void page.route("**/api/chat/conversations", async (route) => {
    if (route.request().method() === "POST") {
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify(history),
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
            title: "人味化测试",
            mode: "companion",
            message_count: 2,
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
      body: JSON.stringify(history),
    });
  });
  // Issue 08：投影前端事件端点（遥测）
  await page.route("**/api/chat/humanizer/events", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true }),
    });
  });
}

async function registerAndOpenChat(page: Page, assistant: MockMessage): Promise<void> {
  const credentials = uniqueCredentials("issue08");
  await signUp(page, credentials.username, credentials.qqEmail, "Passw0rd123!");
  // 注册成功标志：账户菜单按钮可见（登录态就绪）
  await expect(page.getByRole("button", { name: /账户菜单/ })).toBeVisible();
  // headless 下系统剪贴板不可写：mock writeText 并记录写入内容，
  // 供断言「复制只复制最终正文」（剪贴板真实行为由组件单测覆盖）。
  await page.addInitScript(() => {
    let lastCopied = "";
    Object.defineProperty(navigator, "clipboard", {
      value: {
        writeText: async (text: string) => {
          lastCopied = text;
          (window as unknown as { __lastCopied?: string }).__lastCopied = text;
        },
      },
      configurable: true,
    });
  });
  await installMockHistory(page, assistant);
  await page.goto("/chat/mock-1");
}

test.describe("Issue 08 — 正文优先的文章交付界面", () => {
  test("成功无风险：首屏只有最终正文，审计默认折叠，无固定前言", async ({ page }) => {
    await registerAndOpenChat(page, cleanAssistant());
    const thread = page.getByRole("list", { name: "对话消息" });
    const finalText = "光合作用指的是植物把光能转化为化学能的过程，研究显示这一过程需要叶绿素吸收光子。";
    await expect(thread).toContainText(finalText);
    // 无「已完成人味化」「主要修改如下」固定前言与空事实核查段
    await expect(thread.getByText(/已完成人味化/)).toHaveCount(0);
    await expect(thread.getByText(/主要修改如下/)).toHaveCount(0);
    await expect(thread.getByText(/事实核查结果/)).toHaveCount(0);
    // 结果卡状态与复制入口可见（折叠状态不影响复制）
    const card = page.getByTestId("humanizer-result-card");
    await expect(card).toBeVisible();
    await expect(card.getByRole("button", { name: /已交付/ })).toBeVisible();
    await expect(card.getByRole("button", { name: "复制最终正文" })).toBeVisible();
    // 审计分区默认折叠
    await expect(page.getByTestId("humanizer-fidelity")).toHaveCount(0);
  });

  test("展开审计：显示保真通过计数，复制只复制最终正文", async ({ page }) => {
    await registerAndOpenChat(page, cleanAssistant());
    const card = page.getByTestId("humanizer-result-card");
    // 折叠时头部已显示「保真通过」计数
    await expect(card.getByRole("status")).toContainText("保真通过");
    // 展开
    await card.getByRole("button", { name: /已交付/ }).click();
    await expect(card.getByRole("button", { name: /已交付/ })).toHaveAttribute(
      "aria-expanded",
      "true"
    );
    // 成功无风险：不渲染空审计分区（无空事实核查段）
    await expect(page.getByTestId("humanizer-fidelity")).toHaveCount(0);
    // 复制只复制最终正文（不混入审计文案）
    const finalText = "光合作用指的是植物把光能转化为化学能的过程，研究显示这一过程需要叶绿素吸收光子。";
    const copyButton = card.getByRole("button", { name: "复制最终正文" });
    await copyButton.click();
    await expect(copyButton).toContainText(/已复制|复制失败/, { timeout: 10_000 });
    const copied = await page.evaluate(
      () => (window as unknown as { __lastCopied?: string }).__lastCopied
    );
    expect(copied).toBe(finalText);
  });

  test("硬门失败：正文区域明确「未交付」，不显示违规稿", async ({ page }) => {
    await registerAndOpenChat(page, failedAssistant());
    const thread = page.getByRole("list", { name: "对话消息" });
    // 正文区域显示「未交付」提示，不展示草稿候选
    await expect(page.getByTestId("humanizer-not-delivered")).toBeVisible();
    await expect(thread.getByText("光合作用……（未交付候选）")).toHaveCount(0);
    // 结果卡显示「未交付」状态；展开后展示稳定失败原因
    const card = page.getByTestId("humanizer-result-card");
    await expect(card.getByRole("button", { name: /未交付/ })).toBeVisible();
    await card.getByRole("button", { name: /未交付/ }).click();
    await expect(page.getByTestId("humanizer-error-detail")).toContainText(
      "来源保真硬门未通过"
    );
    await expect(page.getByTestId("humanizer-fidelity")).toContainText(
      "无来源新增 claim"
    );
  });

  test("legacy 旧结果标注「旧版结果」，旧字段仍可读取", async ({ page }) => {
    await registerAndOpenChat(page, legacyAssistant());
    const card = page.getByTestId("humanizer-result-card");
    await expect(card.getByTestId("humanizer-legacy-badge")).toContainText("旧版结果");
    await card.getByRole("button", { name: /人味化完成/ }).click();
    await expect(page.getByTestId("humanizer-fact-check")).toContainText("来源保真检查");
  });

  test("刷新后结果卡恢复，正文不重复插入", async ({ page }) => {
    await registerAndOpenChat(page, cleanAssistant());
    const thread = page.getByRole("list", { name: "对话消息" });
    await expect(thread.getByText("光合作用")).toHaveCount(1);
    await page.reload();
    const card = page.getByTestId("humanizer-result-card");
    await expect(card).toBeVisible();
    await expect(thread.getByText("光合作用")).toHaveCount(1);
  });

  test("窄屏下正文可读、折叠不溢出", async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 740 });
    await registerAndOpenChat(page, cleanAssistant());
    const card = page.getByTestId("humanizer-result-card");
    await expect(card).toBeVisible();
    // 复制按钮在窄屏仍可见（正文复制不依赖审计展开）
    await expect(card.getByRole("button", { name: "复制最终正文" })).toBeVisible();
    const body = await page.locator("body").boundingBox();
    expect(body !== null && body.width <= 360 + 1).toBeTruthy();
  });
});
