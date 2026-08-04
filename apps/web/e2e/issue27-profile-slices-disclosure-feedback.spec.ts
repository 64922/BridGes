import { expect, test, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

/**
 * Issue 27 — 交付最小画像切片、披露与反馈闭环。
 *
 * 真实后端（API 进程内 SQLite）：画像记录播种、回答反馈提交/修正记录
 * 真实落库；聊天 SSE 协议级替身提供「本次上下文说明」披露（ready 含
 * 画像类别/用途/来源/材料类别，off/empty/error 各态）。
 *
 * 覆盖：披露卡展开与内容、发送前关闭画像（请求与披露均不含画像）、
 * 画像有误→修正记录→下一轮披露使用新版本、回答反馈失败不丢失可重试、
 * 编译失败披露 error 态且回答照常。
 */

const PASSWORD = "correct-horse-27";
const NOW = "2026-08-05T00:00:00Z";

async function freshAccount(page: Page, prefix: string) {
  const creds = uniqueCredentials(prefix);
  await signUp(page, creds.username, creds.qqEmail, PASSWORD);
  return creds;
}

/** 真实后端播种一条用户手动画像断言，返回断言 ID。 */
async function seedAssertion(
  page: Page,
  dimension: string,
  value: string,
  scenes: string[] = ["companion", "study"]
): Promise<string> {
  const res = await page.request.post("/api/profiles/assertions/manual", {
    data: {
      dimension,
      value_or_rule: value,
      applicable_scenes: scenes,
      sensitivity_class: "preference",
      authorization_scope: "general",
      source_note: "E2E 播种：用户手动记录",
    },
  });
  expect(res.ok()).toBeTruthy();
  const body = await res.json();
  return body.assertion_id as string;
}

// ---------------------------------------------------------------------------
// 协议级替身：聊天 SSE + 上下文说明披露
// ---------------------------------------------------------------------------

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
  context_note: Record<string, unknown> | null;
  thinking: Record<string, unknown> | null;
}

const READY_NOTE = {
  state: "ready",
  profile_enabled: true,
  mode: "companion",
  used_at: NOW,
  profile_items: [
    {
      assertion_id: "assertion-seeded-1",
      dimension: "expression_habit",
      dimension_label: "表达习惯",
      value_summary: "喜欢简洁、先给结论的回答",
      inclusion_reason: "与companion模式相关且已授权的最小切片（类别：表达习惯）。",
      used_at: NOW,
      status: "active",
      version: 1,
      applicable_scenes: ["companion", "study"],
    },
  ],
  material_categories: ["当前附件", "公网搜索"],
  excluded_count: 2,
  note: "本轮回答使用了 1 条画像记录（表达习惯），仅包含与当前任务相关的最小切片。",
};

const OFF_NOTE = {
  state: "off",
  profile_enabled: false,
  mode: "companion",
  used_at: NOW,
  profile_items: [],
  material_categories: [],
  excluded_count: 0,
  note: "本轮未使用你的画像记录（发送前已关闭）。回答不基于任何画像信息。",
};

const EMPTY_NOTE = {
  state: "empty",
  profile_enabled: true,
  mode: "companion",
  used_at: NOW,
  profile_items: [],
  material_categories: [],
  excluded_count: 0,
  note: "本轮没有与你当前任务相关的画像记录，因此没有使用画像信息。",
};

const ERROR_NOTE = {
  state: "error",
  profile_enabled: true,
  mode: "companion",
  used_at: NOW,
  profile_items: [],
  material_categories: [],
  excluded_count: 0,
  note: "本轮画像切片编译失败，回答已在不使用画像的情况下正常生成。",
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
    context_note: null,
    thinking: null,
  };
}

function mockAssistant(
  id: string,
  content: string,
  contextNote: Record<string, unknown> | null
): MockMessage {
  return {
    message_id: id,
    conversation_id: "mock-1",
    role: "assistant",
    attempt_number: 1,
    status: "done",
    content,
    error_code: null,
    error_message: null,
    duration_ms: 1200,
    model_id: "qwen3.7-plus-2026-05-26",
    run_lock_id: "lock-mock",
    created_at: NOW,
    updated_at: NOW,
    context_note: contextNote,
    thinking: {
      steps: ["理解你的问题与当前语境", "组织并生成回答"],
      evidence: [],
      tools: ["已使用 1 条画像记录（最小切片，仅限当前任务）"],
      quality: ["回答已完整生成并保存"],
    },
  };
}

function sseBlock(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

/**
 * 聊天 SSE 替身：started → delta → done。
 * ``noteFor`` 决定本轮助手消息携带的披露快照（ready/off/empty/error）。
 */
async function installMockChatApi(
  page: Page,
  noteFor: (turn: number, requestBody: Record<string, unknown>) => Record<string, unknown> | null
): Promise<{ turns: { useProfile: boolean }[] }> {
  const state: { messages: MockMessage[]; counter: number } = {
    messages: [],
    counter: 0,
  };
  const turns: { useProfile: boolean }[] = [];

  const history = () => ({
    conversation_id: "mock-1",
    title: "测试对话",
    mode: "companion",
    created_at: NOW,
    updated_at: NOW,
    messages: state.messages,
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
      body: JSON.stringify({
        conversations: [
          {
            conversation_id: "mock-1",
            title: "测试对话",
            mode: "companion",
            message_count: state.messages.length,
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
      body: JSON.stringify(history()),
    });
  });

  await page.route("**/api/chat/conversations/mock-1/messages", async (route) => {
    if (route.request().method() !== "POST") return;
    const body = JSON.parse(route.request().postData() ?? "{}");
    state.counter += 1;
    const turn = state.counter;
    turns.push({ useProfile: body.use_profile !== false });
    const userMessage = mockUser(`u-${turn}`, String(body.content ?? ""));
    const assistantMessage = mockAssistant(
      `a-${turn}`,
      "这是测试回答。",
      noteFor(turn, body)
    );
    state.messages.push(userMessage, assistantMessage);
    const stream =
      sseBlock("started", {
        kind: "started",
        conversation_id: "mock-1",
        user_message_id: userMessage.message_id,
        message_id: assistantMessage.message_id,
        attempt_number: 1,
        thinking: assistantMessage.thinking,
      }) +
      sseBlock("delta", {
        kind: "delta",
        message_id: assistantMessage.message_id,
        delta: "这是测试回答。",
      }) +
      sseBlock("done", {
        kind: "done",
        message_id: assistantMessage.message_id,
        message: assistantMessage,
      });
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: stream,
    });
  });

  return { turns };
}

async function openChat(page: Page) {
  await page.goto("/chat/mock-1");
  await expect(page.getByTestId("chat-thread")).toBeVisible();
}

// ---------------------------------------------------------------------------
// 披露卡：ready / off / empty / error 各态
// ---------------------------------------------------------------------------

test("上下文说明卡展示画像类别、用途、来源链接与材料类别；可展开", async ({ page }) => {
  await freshAccount(page, "i27-ready");
  await installMockChatApi(page, () => READY_NOTE);
  await openChat(page);

  const composer = page.getByTestId("composer");
  await composer.getByLabel("输入消息").fill("给我讲个故事");
  await composer.getByRole("button", { name: "发送消息" }).click();

  // 披露卡折叠态：标题与状态
  const card = page.getByTestId("context-note-card");
  await expect(card).toBeVisible();
  await expect(card.getByText("本次上下文说明")).toBeVisible();
  await expect(card.getByText("已使用画像切片")).toBeVisible();

  // 展开：画像类别、值摘要、用途、材料类别、排除数、使用时间
  await card.getByRole("button").click();
  await expect(card.getByText("表达习惯").first()).toBeVisible();
  await expect(page.getByText("喜欢简洁、先给结论的回答")).toBeVisible();
  await expect(page.getByText(/用途：/)).toBeVisible();
  await expect(card.getByText("当前附件")).toBeVisible();
  await expect(card.getByText("公网搜索")).toBeVisible();
  await expect(card.getByText(/另有 2 条画像记录/)).toBeVisible();
  // 来源记录链接（深链到具体记录）
  const recordLink = page.getByRole("link", { name: "查看记录" });
  await expect(recordLink).toBeVisible();
  await expect(recordLink).toHaveAttribute(
    "href",
    `/account/profile?assertion=${encodeURIComponent("assertion-seeded-1")}`
  );
});

test("发送前关闭画像：请求不含画像且披露为关闭态", async ({ page }) => {
  await freshAccount(page, "i27-off");
  const { turns } = await installMockChatApi(page, () => OFF_NOTE);
  await openChat(page);

  // 发送前关闭「使用画像」开关
  await page.getByTestId("profile-usage").click();
  await expect(page.getByTestId("profile-usage")).toHaveAttribute("aria-checked", "false");

  const composer = page.getByTestId("composer");
  await composer.getByLabel("输入消息").fill("你好");
  await composer.getByRole("button", { name: "发送消息" }).click();

  // 请求体不含画像内容（use_profile=false）
  await expect.poll(() => turns.length).toBe(1);
  expect(turns[0].useProfile).toBe(false);

  // 披露为 off 态（permission：画像使用未授权），不含任何画像条目
  const card = page.getByTestId("context-note-card");
  await expect(card).toBeVisible();
  await expect(card.getByText("画像使用未授权")).toBeVisible();
  await card.getByRole("button").click();
  await expect(page.getByText("本轮未使用你的画像记录（发送前已关闭）")).toBeVisible();
  await expect(page.getByTestId("context-note-item")).toHaveCount(0);
});

test("启用画像但没有匹配记录：披露为空态（合法空态）", async ({ page }) => {
  await freshAccount(page, "i27-empty");
  await installMockChatApi(page, () => EMPTY_NOTE);
  await openChat(page);

  const composer = page.getByTestId("composer");
  await composer.getByLabel("输入消息").fill("你好");
  await composer.getByRole("button", { name: "发送消息" }).click();

  const card = page.getByTestId("context-note-card");
  await expect(card.getByText("本轮未使用画像记录")).toBeVisible();
  await card.getByRole("button").click();
  await expect(page.getByText("本轮没有与你当前任务相关的画像记录")).toBeVisible();
  await expect(page.getByTestId("context-note-item")).toHaveCount(0);
});

test("切片编译失败：披露 error 态，回答照常生成", async ({ page }) => {
  await freshAccount(page, "i27-error");
  await installMockChatApi(page, () => ERROR_NOTE);
  await openChat(page);

  const composer = page.getByTestId("composer");
  await composer.getByLabel("输入消息").fill("你好");
  await composer.getByRole("button", { name: "发送消息" }).click();

  const card = page.getByTestId("context-note-card");
  await expect(card.getByText("上下文说明生成失败")).toBeVisible();
  await card.getByRole("button").click();
  await expect(page.getByText("本轮画像切片编译失败，回答已在不使用画像的情况下正常生成。")).toBeVisible();
  // 回答正文照常
  await expect(page.getByText("这是测试回答。")).toBeVisible();
});

// ---------------------------------------------------------------------------
// 反馈闭环：回答反馈 / 画像修正 / 失败恢复
// ---------------------------------------------------------------------------

test("回答反馈提交成功；先失败后重试不丢失反馈", async ({ page }) => {
  await freshAccount(page, "i27-feedback");
  await installMockChatApi(page, () => READY_NOTE);
  await openChat(page);

  const composer = page.getByTestId("composer");
  await composer.getByLabel("输入消息").fill("给我讲个故事");
  await composer.getByRole("button", { name: "发送消息" }).click();

  const card = page.getByTestId("context-note-card");
  await card.getByRole("button").click();

  // 第一次提交失败：错误可见、输入保留、可重试
  let feedbackAttempts = 0;
  await page.route("**/api/chat/conversations/mock-1/messages/a-1/feedback", async (route) => {
    feedbackAttempts += 1;
    if (feedbackAttempts === 1) {
      await route.fulfill({ status: 500, body: "server error" });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        feedback_id: "feedback-27-1",
        account_id: "account-27",
        conversation_id: "mock-1",
        message_id: "a-1",
        kind: "answer_inappropriate",
        feedback_text: "太长了，我只需要结论",
        preference: "先给结论再给理由",
        assertion_id: null,
        status: "submitted",
        resolution_note: null,
        created_at: NOW,
        updated_at: NOW,
      }),
    });
  });

  await page.getByRole("button", { name: "这次回答不合适" }).click();
  await page.getByPlaceholder("例如：太长了，我只需要结论").fill("太长了，我只需要结论");
  await page.getByPlaceholder("例如：先给结论，再给一句理由").fill("先给结论再给理由");
  await page.getByRole("button", { name: "提交反馈" }).click();

  await expect(card.getByRole("alert")).toContainText("失败");
  // 失败后输入保留（不丢失用户反馈）
  await expect(page.getByPlaceholder("例如：太长了，我只需要结论")).toHaveValue("太长了，我只需要结论");

  // 重试成功：显示已记录
  await page.getByRole("button", { name: "提交反馈" }).click();
  await expect(page.getByTestId("answer-feedback-submitted")).toBeVisible();
  expect(feedbackAttempts).toBe(2);
});

test("画像有误：修正记录并提交反馈；下一轮披露使用新版本", async ({ page }) => {
  await freshAccount(page, "i27-correct");
  // 真实后端播种一条表达习惯断言；替身第一轮披露它，第二轮披露修正后的新版本
  const assertionId = await seedAssertion(page, "expression_habit", "喜欢简洁回答");
  const notes = new Map<number, Record<string, unknown>>([
    [
      1,
      {
        ...READY_NOTE,
        profile_items: [
          {
            ...READY_NOTE.profile_items[0],
            assertion_id: assertionId,
            value_summary: "喜欢简洁回答",
            version: 1,
          },
        ],
      },
    ],
    [
      2,
      {
        ...READY_NOTE,
        profile_items: [
          {
            ...READY_NOTE.profile_items[0],
            assertion_id: assertionId,
            value_summary: "喜欢详细、带例子的回答",
            version: 2,
          },
        ],
      },
    ],
  ]);
  // 反馈提交针对 mock 消息（不在真实后端库中）：由协议级替身应答，
  // 画像修正动作走真实后端治理 API（断言真实存在）。
  await page.route("**/api/chat/conversations/mock-1/messages/a-1/feedback", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        feedback_id: "feedback-27-2",
        account_id: "account-27",
        conversation_id: "mock-1",
        message_id: "a-1",
        kind: "profile_incorrect",
        feedback_text: "表达习惯记录有误：我更希望回答有例子",
        preference: null,
        assertion_id: assertionId,
        status: "submitted",
        resolution_note: null,
        created_at: NOW,
        updated_at: NOW,
      }),
    });
  });
  await installMockChatApi(page, (turn) => notes.get(turn) ?? null);
  await openChat(page);

  const composer = page.getByTestId("composer");
  await composer.getByLabel("输入消息").fill("给我讲个故事");
  await composer.getByRole("button", { name: "发送消息" }).click();

  // 展开披露卡 → 画像有误 → 修正内容
  const card = page.getByTestId("context-note-card");
  await card.getByRole("button").click();
  await expect(page.getByText("喜欢简洁回答")).toBeVisible();
  await page.getByRole("button", { name: "画像有误，修正它" }).click();
  await page.getByPlaceholder("例如：我其实更喜欢详细、带例子的回答").fill("喜欢详细、带例子的回答");
  await page.getByPlaceholder("为什么这条记录不准？（会随反馈一起记录）").fill("我更希望回答有例子");
  await page.getByRole("button", { name: "提交修正" }).click();

  // 修正成功提示（真实后端断言已更新）
  await expect(page.getByText("已更新画像记录，下一轮回答将使用新版本。")).toBeVisible();
  const after = await page.request.get("/api/profiles/assertions");
  expect(after.ok()).toBeTruthy();
  const assertions = (await after.json()) as Array<{ assertion_id: string; value_or_rule: string }>;
  const updated = assertions.find((item) => item.assertion_id === assertionId);
  expect(updated?.value_or_rule).toBe("喜欢详细、带例子的回答");

  // 下一轮：披露使用新版本（version 2）；等待历史重载出两轮消息
  await composer.getByLabel("输入消息").fill("再讲一个");
  await composer.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByTestId("context-note-card")).toHaveCount(2);
  const secondCard = page.getByTestId("context-note-card").last();
  await secondCard.getByRole("button").click();
  await expect(secondCard.getByText("喜欢详细、带例子的回答")).toBeVisible();
  await expect(secondCard.getByText("版本 2")).toBeVisible();
});
