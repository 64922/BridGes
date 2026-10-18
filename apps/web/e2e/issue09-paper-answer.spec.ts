import { expect, test, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

const CONVERSATION_ID = "conv-issue09-paper";
const OTHER_CONVERSATION_ID = "conv-issue09-other";
const NOW = "2026-10-18T00:00:00Z";
const PASSWORD = "correct-horse-issue09";

type JsonRecord = Record<string, unknown>;

interface MockMessage {
  message_id: string;
  conversation_id: string;
  role: "user" | "assistant";
  attempt_number: number;
  status: string;
  content: string;
  attachments: unknown[];
  retrieval: unknown;
  paper_search?: JsonRecord | null;
  turn_result?: JsonRecord | null;
  error_code: string | null;
  error_message: string | null;
  duration_ms: number | null;
  model_id: string | null;
  run_lock_id: string | null;
  created_at: string;
  updated_at: string;
}

function message(
  id: string,
  role: "user" | "assistant",
  content: string,
  overrides: Partial<MockMessage> = {}
): MockMessage {
  return {
    message_id: id,
    conversation_id: CONVERSATION_ID,
    role,
    attempt_number: 1,
    status: "done",
    content,
    attachments: [],
    retrieval: null,
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
    candidate_id: "arxiv:1706.03762",
    arxiv_id: "1706.03762",
    doi: null,
    title: "Attention Is All You Need",
    authors: ["Ashish Vaswani"],
    published_year: 2017,
    source: "arxiv",
    sources: ["arxiv"],
    source_ids: ["1706.03762"],
    venue: null,
    cited_by_count: 1000,
    abs_url: "https://arxiv.org/abs/1706.03762",
    pdf_url: null,
    full_text_url: null,
    primary_category: "cs.CL",
    full_text_available: false,
    read_scope: "abstract",
    role: "foundation",
    relevance: "direct",
    research_group: "Transformer 注意力",
    reason_zh: "奠基工作，先读这一篇理解注意力机制的基本结构。",
    match_basis: "标题与摘要命中 transformer 与 attention。",
    match_evidence: [
      {
        requirement: "Transformer",
        source: "title",
        quote: "Attention Is All You Need",
      },
    ],
    supported_claims: [],
    summary_zh: "这篇论文提出完全依赖注意力机制的 Transformer 结构。",
    summary_evidence: {
      requirement: "source_summary",
      source: "abstract",
      quote: "a model relying entirely on attention",
    },
    source_abstract: "The Transformer is a model relying entirely on attention.",
    read_evidence: [],
    unverified: ["未通读全文"],
    ...overrides,
  };
}

function paperSearch(overrides: JsonRecord = {}): JsonRecord {
  return {
    status: "success",
    original_phrase: "Transformer",
    normalized_term: "Transformer",
    expansions: ["attention"],
    confidence: 0.9,
    context_label: "机器学习中的 Transformer 结构",
    queries: [
      {
        source: "arxiv",
        query: "transformer attention",
        status: "success",
        evidence_count: 2,
        retrieved_at: NOW,
        retryable: false,
      },
      {
        source: "openalex",
        query: "transformer citations",
        status: "timeout",
        evidence_count: 0,
        retrieved_at: NOW,
        retryable: true,
        error_message: "OpenAlex 超时",
      },
    ],
    final_query: "transformer attention",
    papers: [
      paper(),
      paper({
        order: 2,
        candidate_id: "arxiv:2005.14165",
        arxiv_id: "2005.14165",
        title: "Language Models are Few-Shot Learners",
        authors: ["Tom B. Brown"],
        published_year: 2020,
        reason_zh: "较新研究，用来了解注意力机制之后的语言模型进展。",
        match_basis: "摘要命中语言模型与注意力语境。",
        summary_zh: "这篇论文训练大规模语言模型并评估少样本能力。",
        source_abstract: "We train a large language model for few-shot learning.",
        role: "recent",
        relevance: "partial",
        research_group: "语言模型",
        cited_by_count: 500,
      }),
    ],
    candidates: [
      {
        candidate_id: "arxiv:1706.03762",
        arxiv_id: "1706.03762",
        doi: null,
        title: "Attention Is All You Need",
        authors: ["Ashish Vaswani"],
        published_year: 2017,
        primary_source: "arxiv",
        sources: ["arxiv"],
        source_ids: ["1706.03762"],
        abs_url: "https://arxiv.org/abs/1706.03762",
        pdf_url: null,
        full_text_url: null,
        abstract: "The Transformer is a model relying entirely on attention.",
        relevance: "direct",
        research_group: "Transformer 注意力",
        match_evidence: [
          {
            requirement: "Transformer",
            source: "abstract",
            quote: "relying entirely on attention",
          },
        ],
        discovery_paths: ["arxiv:1706.03762"],
      },
      {
        candidate_id: "openalex:W123",
        arxiv_id: null,
        doi: "10.5555/seed",
        title: "A seed paper cited by the recommendation",
        authors: [],
        published_year: 2016,
        primary_source: "openalex",
        sources: ["openalex"],
        source_ids: ["W123"],
        abs_url: "https://openalex.org/W123",
        pdf_url: null,
        full_text_url: null,
        abstract: "",
        relevance: "exploratory",
        research_group: "",
        match_evidence: [],
        discovery_paths: ["citation:doi:10.5555/seed"],
      },
    ],
    citation_edges: [
      {
        citing_candidate_id: "arxiv:1706.03762",
        cited_candidate_id: "doi:10.5555/seed",
        citing_title: "Attention Is All You Need",
        cited_title: "A seed paper cited by the recommendation",
        source: "openalex",
      },
    ],
    research_question: "Transformer 注意力机制如何支持序列建模",
    selected_sources: ["arxiv"],
    condition_sources: [
      { kind: "year", value: "2023:", source_phrase: "近三年" },
      { kind: "source", value: "arxiv", source_phrase: "只看 arXiv" },
    ],
    selected: [],
    requested_count: 5,
    artifacts: {},
    expression_policy_version: null,
    evidence_notes: [
      "本轮实际返回 2 篇（目标 5 篇），不凑满篇数。",
      "OpenAlex 引用扩展超时，未取得 1 条引用边。",
    ],
    pending: null,
    searched_at: NOW,
    error_code: null,
    error_message: null,
    retryable: false,
    ...overrides,
  };
}

function successContent(): string {
  return [
    "已按论文模块检索 arXiv，主要查询概念：transformer attention。",
    "语境：机器学习中的 Transformer 结构。",
    "",
    "研究问题：Transformer 注意力机制如何支持序列建模。",
    "本轮概览：核验 2 篇候选，重点推荐 2 篇；仅反映本轮取得的证据。",
    "共 2 篇，按本轮证据组织阅读顺序：",
    "",
    "1. 《Attention Is All You Need》（2017 年，奠基工作）",
    "   选择理由：奠基工作，先读这一篇理解注意力机制的基本结构。",
    "   中文概述：这篇论文提出完全依赖注意力机制的 Transformer 结构。",
    "   来源：https://arxiv.org/abs/1706.03762",
    "   全文链接：来源未返回或未核实。",
    "   本轮只读取标题与摘要。",
    "",
    "2. 《Language Models are Few-Shot Learners》（2020 年，较新研究）",
    "   选择理由：较新研究，用来了解注意力机制之后的语言模型进展。",
    "   中文概述：这篇论文训练大规模语言模型并评估少样本能力。",
    "   来源：https://arxiv.org/abs/2005.14165",
    "   全文链接：来源未返回或未核实。",
    "   本轮只读取标题与摘要。",
    "",
    "证据边界：",
    "- 本轮实际返回 2 篇（目标 5 篇），不凑满篇数。",
    "- OpenAlex 引用扩展超时，未取得 1 条引用边。",
  ].join("\n");
}

function emptyContent(): string {
  return [
    "已按论文模块检索 arXiv，主要查询概念：quantum error correction。",
    "本轮没有返回可推荐的论文，我不会用记忆补造条目。",
    "- arXiv 返回 0 条记录。",
    "可以补充英文术语、放宽年份或换个相近方向后重试。",
  ].join("\n");
}

function history(messages: MockMessage[]) {
  return {
    conversation_id: CONVERSATION_ID,
    title: "Issue 09 论文推荐",
    mode: "companion",
    mode_locked: true,
    pinned: false,
    created_at: NOW,
    updated_at: NOW,
    messages,
    mode_events: [],
  };
}

const sseStarted = (messageId: string, userMessageId: string) =>
  `event: started\ndata: ${JSON.stringify({
    kind: "started",
    conversation_id: CONVERSATION_ID,
    user_message_id: userMessageId,
    message_id: messageId,
    attempt_number: 1,
  })}\n\n`;

const sseDone = (value: MockMessage) =>
  `event: done\ndata: ${JSON.stringify({
    kind: "done",
    message_id: value.message_id,
    message: value,
  })}\n\n`;

async function installChatApi(page: Page, initialMessages: MockMessage[]) {
  const state = { messages: initialMessages, counter: 20 };
  const eventStreams = new Map<string, string>();

  await page.route("**/api/chat/conversations", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        conversations: [
          {
            conversation_id: CONVERSATION_ID,
            title: "Issue 09 论文推荐",
            mode: "companion",
            message_count: state.messages.length,
            created_at: NOW,
            updated_at: NOW,
          },
          {
            conversation_id: OTHER_CONVERSATION_ID,
            title: "另一个会话",
            mode: "companion",
            message_count: 0,
            created_at: NOW,
            updated_at: NOW,
          },
        ],
      }),
    });
  });

  await page.route(`**/api/chat/conversations/${CONVERSATION_ID}`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(history(state.messages)),
    });
  });

  await page.route(`**/api/chat/conversations/${OTHER_CONVERSATION_ID}`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        conversation_id: OTHER_CONVERSATION_ID,
        title: "另一个会话",
        mode: "companion",
        created_at: NOW,
        updated_at: NOW,
        messages: [],
        mode_events: [],
      }),
    });
  });

  await page.route(
    `**/api/chat/conversations/${CONVERSATION_ID}/messages`,
    async (route) => {
      if (route.request().method() !== "POST") return;
      state.counter += 1;
      const userMessage = message(`u-follow-${state.counter}`, "user", "继续追问这篇论文");
      const doneMessage = message("a-follow-1", "assistant", "后续回答已收到。", {
        paper_search: paperSearch({
          papers: [
            paper({
              order: 1,
              candidate_id: "arxiv:2401.00001",
              arxiv_id: "2401.00001",
              title: "A brand new follow-up candidate",
              published_year: 2024,
              reason_zh: "下一轮候选，不应替换已打开的论文资料。",
            }),
          ],
          candidates: [],
          citation_edges: [],
          evidence_notes: [],
        }),
      });
      state.messages.push(userMessage, doneMessage);
      eventStreams.set(
        doneMessage.message_id,
        `${sseStarted(doneMessage.message_id, userMessage.message_id)}${sseDone(doneMessage)}`
      );
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          run_id: "run-follow-1",
          cursor: 1,
          user_message: userMessage,
          assistant_message: { ...doneMessage, status: "streaming", content: "" },
        }),
      });
    }
  );

  await page.route(
    `**/api/chat/conversations/${CONVERSATION_ID}/messages/*/events**`,
    async (route) => {
      const messageId = route.request().url().split("/messages/")[1].split("/")[0];
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        headers: { "Cache-Control": "no-cache", "X-Accel-Buffering": "no" },
        body: eventStreams.get(messageId) ?? "",
      });
    }
  );

  return state;
}

async function openConversation(page: Page, messages: MockMessage[]) {
  const credentials = uniqueCredentials("issue09");
  await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);
  const state = await installChatApi(page, messages);
  await page.goto(`/chat/${CONVERSATION_ID}`);
  await expect(page.getByTestId("composer")).toBeVisible();
  return state;
}

function successMessages(): MockMessage[] {
  return [
    message("u-paper", "user", "帮我找近三年 Transformer 注意力机制的论文"),
    message("a-paper", "assistant", successContent(), {
      paper_search: paperSearch(),
      turn_result: {
        version: "turn-result-v1",
        outcome: "complete",
        outcome_label: "已完成",
        trust: "qualified",
        trust_label: "已通过核验",
        requested_module_id: "paper",
        actual_module_id: "paper",
        capability_list: ["paper"],
        delivered: [{ module_id: "paper", label: "论文搜索", state: "success", trust: "qualified", detail: "" }],
        blocked: [],
        gaps: [],
        recovery: null,
        wait_reason: null,
      },
    }),
  ];
}

test.describe("Issue 09 — 论文推荐融入正文与按需资料（受控 API 替身）", () => {
  test("正文按阅读顺序呈现推荐，详情显示摘要读取与不可得全文", async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openConversation(page, successMessages());

    await expect(page.getByTestId("paper-answer")).toHaveAttribute(
      "data-paper-status",
      "success"
    );
    await expect(page.getByTestId(/^paper-search-card/)).toHaveCount(0);
    await expect(page.getByTestId("assistant-body")).toContainText(
      "Attention Is All You Need"
    );
    await expect(page.getByTestId("assistant-body")).toContainText(
      "本轮只读取标题与摘要"
    );
    await expect(page.getByTestId("assistant-body")).toContainText(
      "全文链接：来源未返回或未核实"
    );
    await expect(page.getByTestId("paper-source-notice")).toContainText(
      "部分来源未成功返回"
    );

    await page.screenshot({
      path: testInfo.outputPath("paper-success-body.png"),
      fullPage: false,
    });

    await page.getByTestId("paper-details-open").click();
    await expect(page.getByTestId("paper-details-panel")).toBeVisible();
    await expect(page.getByTestId("paper-search-overview")).toContainText(
      "来源 arXiv"
    );
    await expect(page.getByTestId("paper-search-conditions")).toContainText(
      "原话：“近三年”"
    );
    await page.screenshot({
      path: testInfo.outputPath("paper-success-overview.png"),
      fullPage: false,
    });
    await page.getByTestId("paper-panel-select-1").click();
    await expect(page.getByTestId("paper-detail")).toBeVisible();
    await expect(page.getByTestId("paper-detail-read-scope")).toHaveText(
      "本轮只读取标题与摘要"
    );
    await expect(page.getByTestId("paper-detail-read-scope")).not.toContainText(
      "本轮读取了全文"
    );
    await expect(page.getByTestId("paper-detail-no-fulltext")).toContainText(
      "来源未返回或未核实全文链接"
    );
    await expect(page.getByTestId("paper-detail-summary")).toContainText(
      "完全依赖注意力机制"
    );
    await page.getByText("来源返回的原始摘要").click();
    await expect(page.getByTestId("paper-detail-abstract")).toContainText(
      "relying entirely on attention"
    );

    await page.screenshot({
      path: testInfo.outputPath("paper-success-detail.png"),
      fullPage: false,
    });

    await page.locator("#composer-input").fill("继续追问这篇论文");
    await page.locator("#composer-input").press("Enter");
    await expect(page.getByText("后续回答已收到。")).toBeVisible();
    await expect(page.getByTestId("paper-details-panel")).toBeVisible();
    await expect(page.getByTestId("paper-details-title")).toHaveText(
      "Attention Is All You Need"
    );
    await expect(page.getByTestId("paper-details-panel")).not.toContainText(
      "A brand new follow-up candidate"
    );

    await page.keyboard.press("Escape");
    await expect(page.getByTestId("paper-details-panel")).toHaveCount(0);
    await expect(page.getByTestId("paper-details-open").first()).toBeFocused();
  });

  test("零结果保留查询记录，不显示伪造推荐", async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await openConversation(page, [
      message("u-empty", "user", "帮我找量子纠错综述"),
      message("a-empty", "assistant", emptyContent(), {
        paper_search: paperSearch({
          status: "empty",
          original_phrase: "量子纠错",
          normalized_term: "quantum error correction",
          expansions: [],
          context_label: null,
          research_question: "量子纠错综述的研究进展",
          final_query: "quantum error correction",
          papers: [],
          candidates: [],
          citation_edges: [],
          condition_sources: [],
          selected_sources: ["arxiv"],
          requested_count: 5,
          evidence_notes: ["arXiv 返回 0 条记录。"],
          queries: [
            {
              source: "arxiv",
              query: "quantum error correction",
              status: "empty",
              evidence_count: 0,
              retrieved_at: NOW,
              retryable: false,
            },
          ],
        }),
        turn_result: {
          version: "turn-result-v1",
          outcome: "partial",
          outcome_label: "部分完成",
          trust: "evidence_bound",
          trust_label: "证据受限",
          requested_module_id: "paper",
          actual_module_id: "paper",
          capability_list: ["paper"],
          delivered: [],
          blocked: [{ module_id: "paper", label: "论文搜索", state: "empty", trust: "evidence_bound", detail: "本轮没有返回可推荐的论文。" }],
          gaps: ["本轮没有返回可推荐的论文。"],
          recovery: null,
          wait_reason: null,
        },
      }),
    ]);

    await expect(page.getByTestId("paper-answer")).toHaveAttribute(
      "data-paper-status",
      "empty"
    );
    await expect(page.getByTestId(/^paper-search-card/)).toHaveCount(0);
    await expect(page.getByTestId("assistant-body")).toContainText(
      "本轮没有返回可推荐的论文"
    );
    await expect(page.getByTestId("paper-details-entry")).toBeVisible();
    await page.getByTestId("paper-details-open").click();
    await expect(page.getByTestId("paper-search-queries")).toContainText(
      "quantum error correction"
    );
    await expect(page.getByTestId("paper-search-recommendations")).toHaveCount(0);
    await page.screenshot({
      path: testInfo.outputPath("paper-empty.png"),
      fullPage: false,
    });
  });

  test("失败显示真实错误与恢复入口，不出现虚假论文卡", async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await openConversation(page, [
      message("u-error", "user", "帮我找图神经网络论文"),
      message("a-error", "assistant", "", {
        status: "error",
        error_code: "arxiv_timeout",
        error_message: "arXiv 检索超时，请稍后重试。",
        paper_search: paperSearch({
          status: "error",
          original_phrase: "图神经网络",
          normalized_term: "graph neural network",
          expansions: [],
          context_label: null,
          research_question: "图神经网络的研究进展",
          final_query: "graph neural network",
          papers: [],
          candidates: [],
          citation_edges: [],
          condition_sources: [],
          evidence_notes: [],
          error_code: "arxiv_timeout",
          error_message: "arXiv 检索超时，请稍后重试。",
          retryable: true,
          queries: [
            {
              source: "arxiv",
              query: "graph neural network",
              status: "timeout",
              evidence_count: 0,
              retrieved_at: NOW,
              retryable: true,
              error_message: "arXiv 检索超时",
            },
          ],
        }),
        turn_result: {
          version: "turn-result-v1",
          outcome: "failed",
          outcome_label: "未完成",
          trust: "evidence_bound",
          trust_label: "证据受限",
          requested_module_id: "paper",
          actual_module_id: "paper",
          capability_list: ["paper"],
          delivered: [],
          blocked: [{ module_id: "paper", label: "论文搜索", state: "error", trust: "evidence_bound", detail: "arXiv 检索超时" }],
          gaps: ["arXiv 检索超时"],
          recovery: { action: "retry", label: "可以重试本轮。", retryable: true, available_after: null },
          wait_reason: null,
        },
      }),
    ]);

    await expect(page.getByTestId("paper-answer")).toHaveAttribute(
      "data-paper-status",
      "error"
    );
    await expect(page.getByTestId(/^paper-search-card/)).toHaveCount(0);
    await expect(
      page.getByTestId("assistant-message").getByRole("alert")
    ).toContainText("arXiv 检索超时");
    await expect(page.getByTestId("delivery-status")).toContainText("可以重试本轮");
    await expect(page.getByTestId("delivery-retry")).toHaveText("重试本轮");
    await page.screenshot({
      path: testInfo.outputPath("paper-error.png"),
      fullPage: false,
    });
  });

  test("澄清与停止各自保留正文状态与合法操作", async ({ page }) => {
    await openConversation(page, [
      message("u-clarify", "user", "帮我找 Transformer 论文"),
      message(
        "a-clarify",
        "assistant",
        "「Transformer」可能指机器学习结构或电力变压器，你想要哪一类论文？",
        {
          paper_search: paperSearch({
            status: "clarification",
            pending: {
              module_id: "paper",
              kind: "clarification",
              question: "「Transformer」可能指机器学习结构或电力变压器，你想要哪一类论文？",
              origin_message_id: "a-clarify",
              context: {},
              created_at: NOW,
            },
            papers: [],
            candidates: [],
            citation_edges: [],
            condition_sources: [],
            queries: [],
            evidence_notes: [],
          }),
        }
      ),
    ]);

    await expect(page.getByTestId("paper-answer")).toHaveAttribute(
      "data-paper-status",
      "clarification"
    );
    await expect(page.getByText("「Transformer」可能指机器学习结构或电力变压器")).toBeVisible();
    await expect(page.getByTestId("composer-module-chip")).toContainText("论文搜索");

    await page.route(`**/api/chat/conversations/${CONVERSATION_ID}`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(
          history([
            message("u-stop", "user", "帮我找量子纠错论文"),
            message("a-stop", "assistant", "论文检索已停止，实际查询词：quantum error correction。", {
              status: "stopped",
              paper_search: paperSearch({
                status: "stopped",
                papers: [],
                candidates: [],
                citation_edges: [],
                evidence_notes: ["用户停止了本轮检索，未生成的步骤不会补做。"],
                final_query: "quantum error correction",
                queries: [
                  {
                    source: "arxiv",
                    query: "quantum error correction",
                    status: "cancelled",
                    evidence_count: 0,
                    retrieved_at: NOW,
                    retryable: false,
                  },
                ],
              }),
            }),
          ])
        ),
      });
    });
    await page.reload();
    await expect(page.getByTestId("paper-answer")).toHaveAttribute(
      "data-paper-status",
      "stopped"
    );
    await expect(page.getByTestId("assistant-body")).toContainText("论文检索已停止");
    await expect(page.getByTestId("delivery-status")).toContainText("本轮已停止");
    await expect(page.getByTestId("delivery-retry")).toHaveText("继续生成");
  });
});
