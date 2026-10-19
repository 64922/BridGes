import { expect, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./auth";

export const CONVERSATION_ID = "conv-issue13-delivery";
export const NOW = "2026-10-18T08:00:00Z";

const PASSWORD = "correct-horse-issue13";

function timeFilter() {
  return {
    requirement: null,
    year: null,
    applied: false,
    eligible_count: 0,
    context_only_count: 0,
    excluded_count: 0,
    history_supplemented: false,
    recent_count: 0,
    historical_count: 0,
    unknown_count: 0,
    note: "未提出时间条件。",
  };
}

export function citation(overrides: Record<string, unknown> = {}) {
  return {
    citation_id: "E1",
    url: "https://tieba.baidu.com/p/8123456789",
    title: "南区宿舍到底怎么样",
    reply_id: "reply-3",
    parent_reply_id: null,
    floor: 3,
    posted_at: "2026-09-20 21:14",
    author: "吧友甲",
    nested: false,
    quote: "南区六人间，有空调，热水要刷卡。",
    ...overrides,
  };
}

export function reply(overrides: Record<string, unknown> = {}) {
  return {
    floor: 3,
    reply_id: "reply-3",
    parent_reply_id: null,
    temporal_scope: "unknown",
    context_only: false,
    posted_at: "2026-09-20 21:14",
    author: "吧友甲",
    is_original_poster: false,
    content: "南区六人间，有空调，热水要刷卡。",
    nested: false,
    nested_preview: false,
    ...overrides,
  };
}

export function post(overrides: Record<string, unknown> = {}) {
  return {
    thread_id: "8123456789",
    url: "https://tieba.baidu.com/p/8123456789",
    title: "南区宿舍到底怎么样",
    affiliation_evidence: "页面吧头显示「华东交通大学吧」。",
    read_status: "partial",
    pages_read: 1,
    pages_limit: 2,
    pages_complete: false,
    scroll_rounds: 0,
    scroll_rounds_limit: 0,
    total_pages: 3,
    floor_min: 1,
    floor_max: 12,
    replies_obtained: true,
    nested_complete: false,
    nested_incomplete_count: 0,
    replies: [
      reply(),
      reply({
        floor: 4,
        reply_id: "reply-4",
        posted_at: "2026-08-01 10:00",
        author: "父楼层作者",
        content: "北区热水十一点就停了。",
      }),
      reply({
        floor: 4,
        reply_id: "n5",
        parent_reply_id: "reply-4",
        posted_at: "2026-08-05 00:17",
        author: "纠正者",
        content: "更正：北区是十一点半停水，不是十一点。",
        nested: true,
      }),
    ],
    read_error_code: null,
    read_error_message: "本轮只读取到部分楼层。",
    retrieved_at: "2026-09-25T02:00:02Z",
    ...overrides,
  };
}

export function tieba(overrides: Record<string, unknown> = {}) {
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
    recovery: null,
    ...overrides,
  };
}

export function turnResult(overrides: Record<string, unknown> = {}) {
  return {
    version: "turn-result-v1",
    outcome: "complete",
    outcome_label: "本轮已完成。",
    trust: "qualified",
    trust_label: "结果已通过核验。",
    requested_module_id: "tieba",
    actual_module_id: "tieba",
    capability_list: ["tieba"],
    route_source: "body_intent",
    delivered: [
      {
        module_id: "tieba",
        label: "贴吧",
        state: "success",
        trust: "qualified",
        detail: "",
      },
    ],
    blocked: [],
    gaps: [],
    recovery: null,
    wait_reason: null,
    task_id: null,
    task_version: null,
    ...overrides,
  };
}

export function message(
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
    duration_ms: role === "assistant" ? 4200 : null,
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

export async function openConversation(
  page: Page,
  messages: unknown[]
): Promise<void> {
  const credentials = uniqueCredentials("issue13");
  await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);

  const history = {
    conversation_id: CONVERSATION_ID,
    title: "贴吧交付与历史",
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
  await page.route(
    `**/api/chat/conversations/${CONVERSATION_ID}`,
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(history),
      });
    }
  );

  await page.goto(`/chat/${CONVERSATION_ID}`);
  await expect(page.getByTestId("composer")).toBeVisible();
}

export async function installClipboardProbe(page: Page): Promise<void> {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      get: () => ({
        writeText: async (text: string) => {
          (window as unknown as { __copiedText?: string }).__copiedText = text;
        },
      }),
    });
  });
}

export function assistantCard(page: Page, text: string) {
  return page.getByTestId("assistant-message").filter({ hasText: text });
}
