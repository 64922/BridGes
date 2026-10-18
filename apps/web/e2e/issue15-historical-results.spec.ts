import { expect, test, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

/**
 * Issue 15：退役日常模块与旧表达结果的历史只读呈现。
 *
 * 历史消息用协议级替身构造；正式 API 的读取与禁止重试由
 * tests/chat/test_issue15_historical_results.py 覆盖。这里验证界面：
 * 结果只出现一次、空正文如实说明、旧尝试正文保留、外部链接安全、
 * 打开/刷新不创建消息、不重试、不重新执行。
 */

const NOW = "2026-10-18T00:00:00Z";

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
  learning_resources?: unknown;
  career_plan?: unknown;
  github_projects?: unknown;
  humanizer?: unknown;
  skill?: unknown;
}

function userMessage(): MockMessage {
  return {
    message_id: "u-15",
    conversation_id: "mock-15",
    role: "user",
    attempt_number: 1,
    status: "done",
    content: "找学习资料、职业规划和 GitHub 项目",
    error_code: null,
    error_message: null,
    duration_ms: null,
    model_id: null,
    run_lock_id: null,
    created_at: NOW,
    updated_at: NOW,
  };
}

function previousAttempt(): MockMessage {
  return {
    ...userMessage(),
    message_id: "a-15-old",
    role: "assistant",
    attempt_number: 1,
    status: "error",
    content: "第一次尝试已经保存的半截正文",
    error_code: "generation_failed",
    error_message: "当时生成中断。",
  };
}

function retiredModulesAssistant(): MockMessage {
  return {
    ...previousAttempt(),
    message_id: "a-15-latest",
    attempt_number: 2,
    status: "done",
    content: "",
    error_code: null,
    error_message: null,
    learning_resources: {
      status: "success",
      original_phrase: "深度学习",
      normalized_term: "深度学习",
      expansions: ["deep learning"],
      confidence: 0.8,
      goal: null,
      level_label: "零基础",
      level_basis: "用户自述零基础",
      queries: [
        {
          source: "douban",
          query: "深度学习",
          status: "success",
          evidence_count: 1,
          retrieved_at: NOW,
          retryable: false,
        },
      ],
      final_query: "深度学习",
      items: [
        {
          order: 1,
          kind: "book",
          title: "深度学习",
          creator: "Ian Goodfellow",
          year: 2016,
          source: "douban",
          url: "https://example.com/book",
          stage: "入门",
          reason_zh: "系统建立基础。",
          match_basis: "标题命中「深度学习」。",
          role: "main",
          purpose_zh: "建立整体框架。",
          evidence_level: "title",
          read_scope: "目录",
          content_covered: false,
          suitability_basis: "目录覆盖基础概念。",
          unverified: ["未读正文"],
        },
      ],
      requested_books: 1,
      requested_videos: 0,
      needs_practice_project: false,
      path_verified: false,
      parallel_limit: 2,
      evidence_notes: [],
      pending: null,
      searched_at: NOW,
      error_code: null,
      error_message: null,
      retryable: false,
    },
    career_plan: {
      status: "success",
      topic: "后端开发",
      original_request: "我想找 Java 后端开发工作。",
      branch: "job_intel",
      job_terms: ["Java 后端开发"],
      family_title: "后端开发",
      stage: "应届生",
      graduation_year: 2026,
      cities: ["南昌"],
      constraints: [],
      plan: [],
      queries: [],
      samples: [
        {
          url: "https://example.com/job",
          source: "boss",
          source_label: "公开招聘职位",
          title: "Java 后端开发工程师",
          company: "某某科技",
          city: "南昌",
          salary_raw: "15-25K",
        },
      ],
      candidate_links: [],
      rejected: [],
      analysis: null,
      advices: [
        {
          kind: "action",
          title: "补一个项目",
          detail: "用真实项目证明后端能力。",
          inference: false,
          priority: 1,
        },
      ],
      adjacent_suggestions: [],
      evidence_boundary: [],
      empty_reason: null,
      retryable: false,
      error_code: null,
      error_message: null,
      completed_at: NOW,
      pending: null,
    },
    github_projects: {
      status: "metadata_only",
      scenario: "校园二手书交换",
      original_request: "帮我找校园二手书交换项目。",
      features: ["发布想卖的书"],
      tech_terms: [],
      whole_idea: true,
      component_terms: [],
      context_source: null,
      queries: [],
      recommendations: [
        {
          rank: 1,
          full_name: "demo/bookswap",
          html_url: "https://github.com/demo/bookswap",
          coverage: "whole",
          covers_parts: [],
          coverage_note: "覆盖了必要功能，按整体项目呈现。",
          description: "校园二手书交换平台。",
          topics: [],
          language: "Python",
          feature_matches: [],
          matched_feature_count: 1,
          required_feature_count: 2,
          required_supported_count: 1,
          runtime_verified: false,
          version: null,
          matrix_note: "仅静态读取。",
          evidence_kinds: ["metadata"],
        },
      ],
      rejected: [],
      rate_limit: { limited: false, note: null },
      evidence_boundary: [],
      empty_reason: null,
      retryable: false,
      error_code: null,
      error_message: null,
      completed_at: NOW,
      pending: null,
    },
  };
}

function humanizerAssistant(failed = false): MockMessage {
  const finalText = "历史人味化最终正文。";
  return {
    ...previousAttempt(),
    message_id: "a-15-humanizer",
    attempt_number: 1,
    status: failed ? "error" : "done",
    content: failed ? "违规候选草稿，不应显示。" : finalText,
    // 历史人味化消息的 skill 列非空；避免正文区落到通用错误横幅。
    skill: {},
    error_code: failed ? "fidelity_gate_conflict" : null,
    error_message: failed ? "来源保真硬门未通过，已停止交付。" : null,
    humanizer: {
      task_id: "task-15",
      skill_id: "bridges-humanizer",
      skill_version: "1.0.0",
      path: "rewrite",
      genre: null,
      contract: { path: "rewrite", source_text: "原文" },
      status: failed ? "error" : "done",
      output: failed
        ? null
        : {
            final_text: finalText,
            edits: [],
            fact_check: [],
            open_questions: [],
            quality_status: "ok",
          },
      error_code: failed ? "fidelity_gate_conflict" : null,
      error_message: failed ? "来源保真硬门未通过，已停止交付。" : null,
      article: {
        projection_version: "1",
        audit_version: "1",
        delivery_status: failed ? "failed" : "delivered",
        material_state: "sufficient",
        final_text: failed ? null : finalText,
        fidelity: {
          passed: !failed,
          blocking_count: failed ? 1 : 0,
          needs_confirmation_count: 0,
          items: failed
            ? [
                {
                  code: "unattributed_claim",
                  severity: "blocking",
                  category: "无来源新增",
                  note: "新增结论没有来源绑定。",
                },
              ]
            : [],
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

async function installMockHistory(page: Page, messages: MockMessage[]): Promise<void> {
  const history = {
    conversation_id: "mock-15",
    title: "历史结果测试",
    mode: "companion",
    created_at: NOW,
    updated_at: NOW,
    messages,
  };
  await page.route("**/api/chat/conversations", async (route) => {
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
            conversation_id: "mock-15",
            title: "历史结果测试",
            mode: "companion",
            message_count: messages.length,
            created_at: NOW,
            updated_at: NOW,
          },
        ],
      }),
    });
  });
  await page.route("**/api/chat/conversations/mock-15", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(history),
    });
  });
}

async function openChat(page: Page, messages: MockMessage[]): Promise<string[]> {
  const requests: string[] = [];
  page.on("request", (request) => {
    const url = request.url();
    if (
      request.method() === "POST" &&
      (/\/messages$/.test(url) ||
        /\/retry$/.test(url) ||
        /\/start$/.test(url) ||
        /\/messages\/[^/]+\/events/.test(url))
    ) {
      requests.push(`${request.method()} ${url}`);
    }
  });
  const credentials = uniqueCredentials("issue15");
  await signUp(page, credentials.username, credentials.qqEmail, "Passw0rd123!");
  await expect(page.getByRole("button", { name: /账户菜单/ })).toBeVisible();
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
  await installMockHistory(page, messages);
  await page.goto("/chat/mock-15");
  return requests;
}

test.describe("Issue 15 历史结果只读呈现", () => {
  test("退役模块历史结果只出现一次，空正文如实说明，旧尝试正文保留", async ({
    page,
  }) => {
    const requests = await openChat(page, [
      userMessage(),
      previousAttempt(),
      retiredModulesAssistant(),
    ]);

    const thread = page.getByRole("list", { name: "对话消息" });
    await expect(page.getByTestId("historical-resources")).toBeVisible();
    await expect(page.getByTestId("historical-career-plan")).toBeVisible();
    await expect(page.getByTestId("historical-github-projects")).toBeVisible();
    await expect(page.getByTestId("historical-empty-body-note")).toContainText(
      "没有保存可读的自然语言正文"
    );
    await expect(page.getByTestId("historical-resources-items")).toContainText(
      "深度学习"
    );
    await expect(page.getByTestId("historical-career-samples")).toContainText(
      "Java 后端开发工程师"
    );
    await expect(page.getByTestId("historical-github-recommendations")).toContainText(
      "demo/bookswap"
    );
    await expect(page.getByTestId("historical-github-projects-status")).toContainText(
      "仅取得元数据"
    );

    await page.getByTestId("attempt-history").getByText("此问题的前 1 次尝试").click();
    await expect(page.getByTestId("attempt-content-1")).toContainText(
      "第一次尝试已经保存的半截正文"
    );
    await expect(thread.getByRole("button", { name: /重试|重新生成|开始/ })).toHaveCount(0);

    await page.reload();
    await expect(page.getByTestId("historical-resources")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("historical-empty-body-note")).toBeVisible();
    expect(requests).toEqual([]);
  });

  test("旧表达已交付正文不重复，复制取得历史最终正文", async ({ page }) => {
    const requests = await openChat(page, [userMessage(), humanizerAssistant()]);
    const thread = page.getByRole("list", { name: "对话消息" });
    await expect(thread.getByText("历史人味化最终正文。")).toHaveCount(1);
    const card = page.getByTestId("humanizer-result-card");
    await expect(card.getByTestId("humanizer-delivery-status")).toContainText(
      "当时已交付"
    );
    await expect(card.getByRole("button", { name: /重试|重新生成/ })).toHaveCount(0);

    await card.getByTestId("humanizer-copy-text").click();
    const copied = await page.evaluate(
      () => (window as unknown as { __lastCopied?: string }).__lastCopied
    );
    expect(copied).toBe("历史人味化最终正文。");

    await page.reload();
    await expect(thread.getByText("历史人味化最终正文。")).toHaveCount(1, {
      timeout: 15_000,
    });
    expect(requests).toEqual([]);
  });

  test("失败旧表达不恢复候选稿，复制禁用且不自动重跑", async ({ page }) => {
    const requests = await openChat(page, [userMessage(), humanizerAssistant(true)]);
    await expect(page.getByTestId("humanizer-not-delivered")).toContainText(
      "来源保真硬门未通过"
    );
    await expect(page.getByText("违规候选草稿，不应显示。")).toHaveCount(0);
    const card = page.getByTestId("humanizer-result-card");
    await expect(card.getByTestId("humanizer-delivery-status")).toContainText(
      "当时未交付正文"
    );
    await expect(card.getByTestId("humanizer-copy-text")).toBeDisabled();
    await expect(card.getByTestId("humanizer-error-detail")).toContainText(
      "来源保真硬门未通过"
    );
    await expect(page.getByRole("button", { name: /重试|重新生成/ })).toHaveCount(0);
    expect(requests).toEqual([]);
  });
});
