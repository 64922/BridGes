import { expect, test, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

/**
 * 工单 10：贴吧总结正文与按需帖子依据。
 *
 * 页面读取使用真实的历史消息投影结构；只替换网络数据，不改变组件路径。
 * 覆盖完整总结、引用面板、仅帖链、局部回复失败、等待认证、官方未核实
 * 与重开历史后仍有同样边界。
 */

const CONVERSATION_ID = "conv-issue10";
const PASSWORD = "correct-horse-issue10";
const NOW = "2026-10-18T08:00:00Z";

const citation = {
  citation_id: "E1",
  url: "https://tieba.baidu.com/p/8123456789",
  title: "南区宿舍到底怎么样",
  reply_id: "reply-3",
  parent_reply_id: null,
  floor: 3,
  posted_at: "2026-09-20 21:14",
  quote: "南区六人间，有空调，热水要刷卡。",
};

const post = {
  thread_id: "8123456789",
  url: "https://tieba.baidu.com/p/8123456789",
  title: "南区宿舍到底怎么样",
  affiliation_evidence: "页面吧头显示「华东交通大学吧」。",
  read_status: "read",
  pages_read: 1,
  pages_limit: 2,
  scroll_rounds: 0,
  scroll_rounds_limit: 0,
  total_pages: 3,
  floor_min: 1,
  floor_max: 12,
  replies_obtained: true,
  replies: [
    {
      floor: 3,
      reply_id: "reply-3",
      parent_reply_id: null,
      temporal_scope: "unknown",
      posted_at: "2026-09-20 21:14",
      is_original_poster: false,
      content: "南区六人间，有空调，热水要刷卡。",
    },
  ],
  read_error_code: null,
  read_error_message: null,
  retrieved_at: "2026-09-25T02:00:02Z",
};

function timeFilter(note = "未提出时间条件。") {
  return {
    requirement: null,
    year: null,
    applied: false,
    recent_count: 0,
    historical_count: 0,
    unknown_count: 0,
    note,
  };
}

function tieba(overrides: Record<string, unknown> = {}) {
  return {
    status: "success",
    topic: "宿舍条件",
    original_question: "华东交通大学吧里最近的宿舍条件怎么样",
    topic_terms: ["宿舍", "条件"],
    place_or_event: [],
    question_kind: "experience",
    parallel_evidence: false,
    time_filter: timeFilter(),
    queries: [],
    forum: "华东交通大学吧",
    confirmed_posts: [],
    candidate_links: [],
    rejected_candidates: [],
    official_check_requested: false,
    official_checks: [],
    sections: [],
    evidence_boundary: [],
    empty_reason: null,
    retryable: false,
    error_code: null,
    error_message: null,
    completed_at: "2026-09-25T02:00:05Z",
    pending: null,
    ...overrides,
  };
}

function message(
  id: string,
  role: "user" | "assistant",
  content: string,
  tiebaResearch: Record<string, unknown> | null = null,
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
    model_id: role === "assistant" ? "mock-model" : null,
    run_lock_id: null,
    active_run: null,
    module_id: null,
    paper_search: null,
    module_suggestion: null,
    tieba_research: tiebaResearch,
    career_plan: null,
    learning_resources: null,
    commute_route: null,
    github_projects: null,
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  };
}

async function openConversation(page: Page, messages: unknown[]): Promise<void> {
  const credentials = uniqueCredentials("issue10");
  await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);

  const history = {
    conversation_id: CONVERSATION_ID,
    title: "贴吧宿舍条件",
    mode: "companion",
    mode_locked: true,
    pinned: false,
    created_at: NOW,
    updated_at: NOW,
    messages,
    mode_events: [],
  };

  await page.route("**/api/chat/conversations", async (route) => {
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
      body: JSON.stringify(history),
    });
  });

  await page.goto(`/chat/${CONVERSATION_ID}`);
  await expect(page.getByTestId("composer")).toBeVisible();
}

test.describe("Issue 10 贴吧总结正文与帖子依据", () => {
  test("默认读到完整总结，引用在右侧打开对应楼层与读取范围", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openConversation(page, [
      message("u-1", "user", "贴吧里最近宿舍条件怎么样"),
      message(
        "a-1",
        "assistant",
        "服务端已提交的同一份总结",
        tieba({
          summary: "已读楼层提到，南区宿舍有空调，热水需要刷卡。",
          summary_citations: [citation],
          summary_claims: [
            {
              kind: "finding",
              text: "已读楼层提到，南区宿舍有空调，热水需要刷卡。",
              citation_ids: ["E1"],
            },
          ],
          confirmed_posts: [post],
          evidence_boundary: ["本轮只读取了部分页面，未读部分不进入总结。"],
        })
      ),
    ]);

    await expect(page.getByTestId("tieba-answer")).toBeVisible();
    await expect(page.getByTestId("tieba-summary")).toContainText(
      "已读楼层提到，南区宿舍有空调"
    );
    await expect(page.getByText("服务端已提交的同一份总结")).toHaveCount(0);
    await expect(page.getByTestId("tieba-evidence-boundary")).toContainText(
      "未读部分不进入总结"
    );

    const trigger = page.getByTestId("tieba-citation-E1");
    await trigger.click();
    const panel = page.getByTestId("tieba-evidence-panel");
    await expect(panel).toBeVisible();
    await expect(page.getByTestId("tieba-evidence-location")).toContainText("第 3 楼");
    await expect(page.getByTestId("tieba-evidence-quote")).toContainText(
      "南区六人间"
    );
    await expect(page.getByTestId("tieba-evidence-scope")).toContainText(
      "已读 1/2 页"
    );
    await expect(page.getByTestId("tieba-evidence-reply-3")).toContainText(
      "热水要刷卡"
    );
    await expect(page.getByTestId("tieba-evidence-open")).toHaveAttribute(
      "href",
      "https://tieba.baidu.com/p/8123456789"
    );

    await page.keyboard.press("Escape");
    await expect(panel).toHaveCount(0);
    await expect(trigger).toBeFocused();

    await page.reload();
    await expect(page.getByTestId("tieba-summary")).toContainText(
      "已读楼层提到，南区宿舍有空调"
    );
    await expect(page.getByTestId("tieba-evidence-panel")).toHaveCount(0);
  });

  test("仅帖链、局部回复失败、等待认证与官方未核实各自保留真实边界", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openConversation(page, [
      message("u-links", "user", "只看帖链的宿舍问题"),
      message(
        "a-links",
        "assistant",
        "仅帖链正文",
        tieba({
          status: "links_only",
          candidate_links: [
            {
              url: "https://tieba.baidu.com/p/7000000001",
              title: "宿舍条件汇总",
              source: "tavily",
              unconfirmed_reason: "页面不可读：访问受限，未绕过",
            },
          ],
          evidence_boundary: ["只取得帖链，未取得回复内容。"],
        })
      ),
      message("u-partial", "user", "部分回复失败的宿舍问题"),
      message(
        "a-partial",
        "assistant",
        "部分回复失败正文",
        tieba({
          summary: "已读楼层提到空调与热水刷卡。",
          summary_citations: [citation],
          confirmed_posts: [
            {
              ...post,
              read_status: "partial",
              read_error_message: "本轮只读取到部分楼层。",
            },
          ],
        })
      ),
      message("u-auth", "user", "需要登录的宿舍问题"),
      message(
        "a-auth",
        "assistant",
        "等待认证正文",
        tieba({
          status: "waiting_auth",
          pending: {
            module_id: "tieba",
            kind: "authentication",
            question: "请完成专用贴吧会话的登录或安全验证。",
            origin_message_id: "a-auth",
            context: {},
            created_at: NOW,
          },
        })
      ),
      message("u-official", "user", "官方未核实的宿舍问题"),
      message(
        "a-official",
        "assistant",
        "官方核验正文",
        tieba({
          status: "empty",
          official_check_requested: true,
          official_checks: [],
          empty_reason: "没有取得相关讨论。",
        })
      ),
    ]);

    await expect(page.getByTestId("tieba-links-only")).toContainText(
      "搜索摘要不是已读帖子，也不能当作吧友结论"
    );
    await expect(page.getByTestId("tieba-candidate-links")).toContainText(
      "页面不可读：访问受限，未绕过"
    );
    await expect(page.getByTestId("tieba-read-boundary")).toContainText(
      "本轮只读取到部分楼层"
    );
    await expect(page.getByTestId("tieba-read-boundary")).toContainText(
      "未读部分不进入结论"
    );
    await expect(page.getByTestId("tieba-pending")).toContainText(
      "请完成专用贴吧会话的登录或安全验证"
    );
    await expect(page.getByTestId("tieba-pending")).toContainText("回复“继续”");
    await expect(page.getByTestId("tieba-official-unverified")).toContainText(
      "官方规定保持未核实"
    );

    await page.reload();
    await expect(page.getByTestId("tieba-links-only")).toContainText(
      "未取得回复内容"
    );
    await expect(page.getByTestId("tieba-read-boundary")).toContainText(
      "未读部分不进入结论"
    );
    await expect(page.getByTestId("tieba-pending")).toContainText("回复“继续”");
    await expect(page.getByTestId("tieba-official-unverified")).toContainText(
      "官方规定保持未核实"
    );
  });
});
