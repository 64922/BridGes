import { expect, test, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";
import { installRunEventsRoutes, runCreated } from "./helpers/chat-mock";

/**
 * 工单 04：原始论文搜索旅程的桌面行为验收。
 *
 * 用受控 API 替身固化 Issue 01/02/03 组合后的用户可见合同：
 * - 成功时资料面板展示实际调用过的两个来源与逐条查询记录；
 * - 失败终态不再残留 arXiv loading 卡，重试按钮有真实运行入口；
 * - 用户停止保留实际来源，继续生成是合法恢复路径；
 * - 刷新后终态与实时投影一致，不回到 loading。
 */

const CONVERSATION_ID = "conv-issue04-paper";
const NOW = "2026-10-18T00:00:00Z";
const PASSWORD = "correct-horse-issue04";

type Scenario = "success" | "error" | "stopped";
type JsonRecord = Record<string, unknown>;

interface State { scenario: Scenario; messages: JsonRecord[]; counter: number }

function message(
  id: string,
  role: "user" | "assistant",
  content: string,
  overrides: JsonRecord = {}
): JsonRecord {
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
    web_search: null,
    arxiv_search: null,
    paper_search: null,
    turn_result: null,
    error_code: null,
    error_message: null,
    duration_ms: role === "assistant" ? 1200 : null,
    model_id: role === "assistant" ? "mock-model" : null,
    run_lock_id: role === "assistant" ? "mock-lock" : null,
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  };
}

function paper(overrides: JsonRecord = {}): JsonRecord {
  return {
    order: 1,
    candidate_id: "arxiv:2401.10001",
    arxiv_id: "2401.10001",
    doi: null,
    title: "Machine Learning: An Introduction",
    authors: ["Ada Lovelace"],
    published_year: 2024,
    source: "arxiv",
    sources: ["arxiv"],
    source_ids: ["arxiv:2401.10001"],
    venue: null,
    cited_by_count: null,
    abs_url: "https://arxiv.org/abs/2401.10001",
    pdf_url: "https://arxiv.org/pdf/2401.10001",
    full_text_url: null,
    primary_category: "cs.LG",
    full_text_available: false,
    read_scope: "abstract",
    role: "tutorial",
    relevance: "direct",
    research_group: "机器学习入门",
    reason_zh: "入门教程，先读标题与摘要建立基本概念。",
    match_basis: "标题与摘要逐字命中机器学习与入门。",
    match_evidence: [
      {
        requirement: "Machine Learning",
        source: "title",
        quote: "Machine Learning",
      },
      {
        requirement: "Introduction",
        source: "title",
        quote: "Introduction",
      },
    ],
    supported_claims: [],
    summary_zh: null,
    summary_evidence: null,
    source_abstract:
      "This introduction explains the basic goals and methods of machine learning.",
    read_evidence: [],
    unverified: ["未通读全文"],
    ...overrides,
  };
}
function paperSearch(overrides: JsonRecord = {}): JsonRecord {
  return {
    status: "success",
    original_phrase: "机器学习、入门",
    normalized_term: "机器学习、入门",
    expansions: ["machine learning", "introduction"],
    confidence: 0.9,
    context_label: null,
    queries: [
      {
        source: "arxiv",
        query: 'all:"machine learning" AND all:"introduction"',
        status: "success",
        evidence_count: 1,
        retrieved_at: NOW,
        retryable: false,
      },
      {
        source: "openalex",
        query: "machine learning introduction",
        status: "success",
        evidence_count: 1,
        retrieved_at: NOW,
        retryable: false,
      },
    ],
    final_query: 'all:"machine learning" AND all:"introduction"',
    papers: [
      paper(),
      paper({
        order: 2,
        candidate_id: "openalex:w100",
        arxiv_id: null,
        source: "openalex",
        sources: ["openalex"],
        source_ids: ["openalex:w100"],
        title: "Machine Learning for Beginners",
        abs_url: "https://openalex.org/W100",
        pdf_url: null,
        authors: ["Grace Hopper"],
        published_year: 2023,
        role: "survey",
        match_basis: "标题与摘要逐字命中机器学习与入门。",
        source_abstract:
          "A short introduction to machine learning for new students.",
      }),
    ],
    candidates: [],
    citation_edges: [],
    research_question: "机器学习入门需要阅读哪些论文",
    selected_sources: ["arxiv", "openalex"],
    condition_sources: [],
    selected: [],
    requested_count: 5,
    artifacts: {},
    expression_policy_version: null,
    evidence_notes: ["本轮只做摘要级概述；未通读全文。"],
    pending: null,
    searched_at: NOW,
    error_code: null,
    error_message: null,
    retryable: false,
    ...overrides,
  };
}
function successMessage(id: string, attempt = 1): JsonRecord {
  return message(
    id,
    "assistant",
    "我按入门优先整理了论文：1. Machine Learning: An Introduction（arXiv）。",
    {
      attempt_number: attempt,
      paper_search: paperSearch(),
      turn_result: {
        version: "turn-result-v1",
        outcome: "complete",
        outcome_label: "本轮已完成。",
        trust: "qualified",
        trust_label: "结果已通过核验。",
        requested_module_id: "paper",
        actual_module_id: "paper",
        capability_list: ["paper"],
        delivered: [
          {
            module_id: "paper",
            label: "论文搜索",
            state: "success",
            trust: "qualified",
            detail: "",
          },
        ],
        blocked: [],
        gaps: [],
        recovery: null,
        wait_reason: null,
      },
    }
  );
}
function errorMessage(): JsonRecord {
  return message("a-error", "assistant", "", {
    status: "error",
    error_code: "paper_semantic_evaluation_unavailable",
    error_message:
      "本轮候选未完成语义与证据核验，请重试；未交付未经核验的推荐。",
    paper_search: paperSearch({
      status: "error",
      papers: [],
      error_code: "paper_semantic_evaluation_unavailable",
      error_message:
        "本轮候选未完成语义与证据核验，请重试；未交付未经核验的推荐。",
      retryable: true,
      evidence_notes: [],
    }),
    turn_result: {
      version: "turn-result-v1",
      outcome: "failed",
      outcome_label: "本轮未完成。",
      trust: "evidence_bound",
      trust_label: "证据受限。",
      requested_module_id: "paper",
      actual_module_id: "paper",
      capability_list: ["paper"],
      delivered: [],
      blocked: [
        {
          module_id: "paper",
          label: "论文搜索",
          state: "error",
          trust: "evidence_bound",
          detail: "语义证据核验未通过",
        },
      ],
      gaps: ["语义证据核验未通过"],
      recovery: {
        action: "retry",
        label: "可以重试本轮。",
        retryable: true,
        available_after: null,
      },
      wait_reason: null,
    },
  });
}

function stoppedMessage(): JsonRecord {
  return message("a-stopped", "assistant", "论文检索已停止，已调用来源记录保留。", {
    status: "stopped",
    paper_search: paperSearch({
      status: "stopped",
      papers: [],
      evidence_notes: ["用户停止了本轮检索，未生成的步骤不会补做。"],
    }),
    turn_result: {
      version: "turn-result-v1",
      outcome: "cancelled",
      outcome_label: "本轮已停止。",
      trust: null,
      trust_label: null,
      requested_module_id: "paper",
      actual_module_id: "paper",
      capability_list: ["paper"],
      delivered: [],
      blocked: [],
      gaps: [],
      recovery: null,
      wait_reason: null,
    },
  });
}

function sseStarted(messageId: string, userMessageId: string): string {
  return `event: started\ndata: ${JSON.stringify({
    kind: "started",
    conversation_id: CONVERSATION_ID,
    user_message_id: userMessageId,
    message_id: messageId,
    attempt_number: 1,
    arxiv_search: null,
  })}\n\n`;
}

function sseDone(value: JsonRecord): string {
  return `event: done\ndata: ${JSON.stringify({
    kind: "done",
    message_id: value.message_id,
    message: value,
  })}\n\n`;
}

async function installChatApi(page: Page, scenario: Scenario): Promise<State> {
  const state: State = {
    scenario,
    messages: [
      message("u-1", "user", "帮我找几篇机器学习入门论文"),
      scenario === "success"
        ? successMessage("a-1")
        : scenario === "error"
          ? errorMessage()
          : stoppedMessage(),
    ],
    counter: 1,
  };
  const eventStreams = new Map<string, string>();
  const history = () => ({
    conversation_id: CONVERSATION_ID,
    title: "工单 04 论文搜索",
    mode: "companion",
    pinned: false,
    project_id: null,
    created_at: NOW,
    updated_at: NOW,
    messages: state.messages,
    mode_events: [],
  });

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
      body: JSON.stringify({ conversations: [] }),
    });
  });
  await page.route(`**/api/chat/conversations/${CONVERSATION_ID}`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(history()),
    });
  });
  await page.route(
    `**/api/chat/conversations/${CONVERSATION_ID}/messages/*/retry`,
    async (route) => {
      state.counter += 1;
      const assistant = successMessage(`a-retry-${state.counter}`, state.counter);
      state.messages.push(assistant);
      state.scenario = "success";
      eventStreams.set(
        String(assistant.message_id),
        `${sseStarted(String(assistant.message_id), "u-1")}${sseDone(assistant)}`
      );
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(
          runCreated(
            `run-retry-${state.counter}`,
            1,
            state.messages[0],
            assistant
          )
        ),
      });
    }
  );
  installRunEventsRoutes(page, eventStreams, CONVERSATION_ID);
  return state;
}

async function openConversation(page: Page, scenario: Scenario): Promise<State> {
  const credentials = uniqueCredentials("issue04");
  await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);
  const state = await installChatApi(page, scenario);
  await page.goto(`/chat/${CONVERSATION_ID}`);
  await expect(page.getByTestId("composer")).toBeVisible();
  return state;
}

const VIEWPORTS = [
  { width: 1280, height: 720 },
  { width: 1440, height: 900 },
  { width: 1920, height: 1080 },
];

for (const viewport of VIEWPORTS) {
  test.describe(`工单 04 · ${viewport.width}×${viewport.height}`, () => {
    test("成功旅程展示两个实际来源、逐字证据与重载一致", async ({
      page,
    }, testInfo) => {
      await page.setViewportSize(viewport);
      await openConversation(page, "success");

      const paper = page.getByTestId("paper-answer");
      await expect(paper).toHaveAttribute("data-paper-status", "success");
      await expect(page.getByTestId(/arxiv-search-card/)).toHaveCount(0);
      await expect(page.getByTestId("assistant-body")).toContainText(
        "Machine Learning: An Introduction"
      );
      await page.getByTestId("paper-details-open").click();
      await expect(page.getByTestId("paper-details-panel")).toBeVisible();
      await expect(page.getByTestId("paper-search-overview")).toContainText(
        "实际调用来源：arXiv、OpenAlex"
      );
      await expect(page.getByTestId("paper-search-queries")).toContainText(
        'all:"machine learning" AND all:"introduction"'
      );
      await expect(page.getByTestId("paper-search-queries")).toContainText(
        "machine learning introduction"
      );
      await expect(page.getByTestId("paper-search-recommendations")).toContainText(
        "Machine Learning: An Introduction"
      );
      await expect(page.getByTestId("paper-panel-select-1")).toBeVisible();
      await page.screenshot({
        path: testInfo.outputPath("paper-success.png"),
        fullPage: false,
      });

      await page.reload();
      await expect(page.getByTestId("paper-answer")).toHaveAttribute(
        "data-paper-status",
        "success"
      );
      await expect(page.getByTestId(/arxiv-search-card/)).toHaveCount(0);
    });
  });
}

test("失败终态停止转圈、保留实际来源且重试有真实运行入口", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await openConversation(page, "error");

  await expect(page.getByTestId("paper-answer")).toHaveAttribute(
    "data-paper-status",
    "error"
  );
  await expect(page.getByTestId(/arxiv-search-card/)).toHaveCount(0);
  await expect(page.locator(".bg-spinner")).toHaveCount(0);
  await expect(
    page.getByTestId("assistant-message").getByRole("alert")
  ).toContainText("语义与证据核验");
  await expect(page.getByTestId("delivery-retry")).toHaveText("重试本轮");

  await page.getByTestId("paper-details-open").click();
  await expect(page.getByTestId("paper-search-overview")).toContainText(
    "实际调用来源：arXiv、OpenAlex"
  );
  await expect(page.getByTestId("paper-search-queries")).toContainText(
    'all:"machine learning" AND all:"introduction"'
  );
  await page.screenshot({
    path: testInfo.outputPath("paper-error.png"),
    fullPage: false,
  });
  await page.keyboard.press("Escape");

  await page.reload();
  await expect(page.getByTestId("paper-answer")).toHaveAttribute(
    "data-paper-status",
    "error"
  );
  await expect(page.getByTestId(/arxiv-search-card/)).toHaveCount(0);
  await expect(page.locator(".bg-spinner")).toHaveCount(0);

  await page.getByTestId("delivery-retry").click();
  await expect(page.getByTestId("paper-answer").last()).toHaveAttribute(
    "data-paper-status",
    "success"
  );
  await expect(page.getByTestId(/arxiv-search-card/)).toHaveCount(0);
  await expect(page.locator(".bg-spinner")).toHaveCount(0);
});

test("用户停止保留实际来源，继续生成是实际可执行的恢复路径", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openConversation(page, "stopped");

  await expect(page.getByTestId("paper-answer")).toHaveAttribute(
    "data-paper-status",
    "stopped"
  );
  await expect(page.getByTestId("delivery-status")).toContainText("本轮已停止");
  await expect(page.getByTestId("delivery-retry")).toHaveText("继续生成");
  await expect(page.getByTestId(/arxiv-search-card/)).toHaveCount(0);
  await page.getByTestId("paper-details-open").click();
  await expect(page.getByTestId("paper-search-overview")).toContainText(
    "实际调用来源：arXiv、OpenAlex"
  );

  await page.getByTestId("delivery-retry").click();
  await expect(page.getByTestId("paper-answer").last()).toHaveAttribute(
    "data-paper-status",
    "success"
  );
});
