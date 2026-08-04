import { expect, test, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

/**
 * Issue 26 — 交付画像候选与分级许可更新。
 *
 * 真实后端（API 进程内 SQLite 画像仓库）：许可开关默认关闭、开启/关闭后
 * 刷新保持；候选箱展示为何提出/来源消息/适用范围，编辑后确认进入画像记录；
 * 批量确认与批量拒绝；通知空态。
 *
 * 协议级替身（聊天 SSE）：画像通知卡片即时展示（自动写入/单次情绪），
 * 一键撤回先失败后重试成功（错误恢复不重复写入），关闭标记已读。
 */

const PASSWORD = "correct-horse-26";
const NOW = "2026-08-05T00:00:00Z";

async function freshAccount(page: Page, prefix: string) {
  const creds = uniqueCredentials(prefix);
  await signUp(page, creds.username, creds.qqEmail, PASSWORD);
  return creds;
}

async function getAccountId(page: Page): Promise<string> {
  const res = await page.request.get("/api/auth/session");
  expect(res.ok()).toBeTruthy();
  const body = await res.json();
  return body.account.id as string;
}

/** 通过 API 播种一条敏感类别观察 + 候选（与 Issue 25 播种策略一致）。 */
async function seedSensitiveCandidate(
  page: Page,
  accountId: string,
  content: string
): Promise<string> {
  const observation = await page.request.post("/api/profiles/observations", {
    data: {
      owner_account_id: accountId,
      source_type: "explicit_statement",
      source_ref: "e2e-conversation-26",
      source_span_or_event: "user-message-26",
      scene: "companion",
      purpose: "emotion_trend",
      observed_content: content,
      signal_kind: "other",
      extractor_and_version: "e2e-seed-1",
      reliability_factors: ["explicit_statement"],
      sensitivity_class: "sensitive",
      retention_policy: "account_lifetime",
    },
  });
  expect(observation.ok()).toBeTruthy();
  const observationBody = await observation.json();
  const candidate = await page.request.post("/api/profiles/candidates", {
    data: {
      owner_account_id: accountId,
      canonical_dimension: "emotion_trend",
      value_or_rule: content,
      applicable_scenes: ["companion"],
      supporting_observation_ids: [observationBody.observation_id],
      evidence_summary: "E2E 播种：情绪类敏感候选需确认后生效",
      authorization_scope: "general",
      sensitivity_class: "sensitive",
    },
  });
  expect(candidate.ok()).toBeTruthy();
  const candidateBody = await candidate.json();
  return candidateBody.candidate_id as string;
}

async function openProfileCenter(page: Page) {
  await page.goto("/account/profile");
  await expect(page.getByRole("heading", { name: "数字分身画像" })).toBeVisible();
}

// ---------------------------------------------------------------------------
// 许可开关（真实后端：默认关闭、开启/关闭持久化）
// ---------------------------------------------------------------------------

test("许可默认关闭；开启与关闭后刷新保持且可追溯", async ({ page }) => {
  await freshAccount(page, "i26-perm");
  await openProfileCenter(page);

  const panel = page.getByTestId("profile-permissions");
  await expect(panel).toBeVisible();
  // 六个开关（3 类 × 2 场景）默认全部关闭
  for (const label of [
    "阶段目标·日常陪伴自动更新",
    "阶段目标·学习模式自动更新",
    "兴趣偏好·日常陪伴自动更新",
    "兴趣偏好·学习模式自动更新",
    "表达习惯·日常陪伴自动更新",
    "表达习惯·学习模式自动更新",
  ]) {
    await expect(page.getByLabel(label)).not.toBeChecked();
  }

  // 开启兴趣偏好·日常陪伴：成功反馈
  await page.getByLabel("兴趣偏好·日常陪伴自动更新").check();
  await expect(page.getByRole("status").filter({ hasText: "已开启" })).toBeVisible();

  // 刷新后保持开启（SQLite 持久化）
  await page.reload();
  await expect(page.getByLabel("兴趣偏好·日常陪伴自动更新")).toBeChecked();
  await expect(page.getByLabel("兴趣偏好·学习模式自动更新")).not.toBeChecked();

  // 关闭：停止未来自动更新，成功反馈
  await page.getByLabel("兴趣偏好·日常陪伴自动更新").uncheck();
  await expect(page.getByRole("status").filter({ hasText: "已关闭" })).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("兴趣偏好·日常陪伴自动更新")).not.toBeChecked();
});

// ---------------------------------------------------------------------------
// 候选箱增强（真实后端：为何提出 / 来源消息 / 适用范围 / 编辑后确认）
// ---------------------------------------------------------------------------

test("候选卡展示为何提出与来源消息；编辑后确认进入画像记录", async ({ page }) => {
  const accountId = await freshAccount(page, "i26-cand").then(() => getAccountId(page));
  await seedSensitiveCandidate(page, accountId, "考前容易紧张");
  await openProfileCenter(page);

  const candidateSection = page.getByRole("heading", { name: "待确认候选" });
  await expect(candidateSection).toBeVisible();
  await expect(page.getByText("考前容易紧张", { exact: true })).toBeVisible();
  // 为何提出 + 来源消息 + 将适用范围
  await expect(
    page.getByText("为何提出：E2E 播种：情绪类敏感候选需确认后生效")
  ).toBeVisible();
  await expect(page.getByText("来源消息：“考前容易紧张”")).toBeVisible();
  await expect(page.getByText("将适用范围：companion")).toBeVisible();

  // 编辑后确认：修改值后以 MODIFY 决策提交
  await page.getByRole("button", { name: "编辑后确认" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("候选内容").fill("考前适度紧张是正常的");
  await dialog.getByRole("button", { name: "确认" }).click();

  // 候选消失，记录进入「情绪变化趋势」分区
  await expect(candidateSection).toBeHidden();
  await page.getByRole("tab", { name: /情绪变化趋势/ }).click();
  await expect(page.getByText("考前适度紧张是正常的")).toBeVisible();
});

// ---------------------------------------------------------------------------
// 批量处理（真实后端：勾选 → 批量拒绝，幂等不重复写入）
// ---------------------------------------------------------------------------

test("候选批量拒绝：勾选多条后批量处理，空态恢复", async ({ page }) => {
  const accountId = await freshAccount(page, "i26-batch").then(() => getAccountId(page));
  await seedSensitiveCandidate(page, accountId, "备考压力大");
  await seedSensitiveCandidate(page, accountId, "容易分心");
  await openProfileCenter(page);

  await expect(page.getByRole("heading", { name: "待确认候选" })).toBeVisible();
  await page.getByLabel("选择候选：备考压力大").check();
  await page.getByLabel("选择候选：容易分心").check();
  await expect(page.getByText("已选 2 条")).toBeVisible();

  await page.getByRole("button", { name: "批量拒绝" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("操作原因").fill("E2E 批量拒绝");
  await dialog.getByRole("button", { name: "批量拒绝" }).click();

  // 候选区整体消失（空态），画像记录仍为空（未被写入）
  await expect(page.getByRole("heading", { name: "待确认候选" })).toBeHidden();
  await expect(page.getByRole("tab", { name: /情绪变化趋势/ })).toBeVisible();
});

// ---------------------------------------------------------------------------
// 通知：空态（真实后端）+ 聊天内通知卡片与一键撤回（协议替身）
// ---------------------------------------------------------------------------

test("新账户通知空态；聊天内自动写入通知可一键撤回并错误恢复", async ({ page }) => {
  await freshAccount(page, "i26-notice");

  // 空态：还没有任何画像通知
  await openProfileCenter(page);
  await expect(page.getByText("暂无画像通知")).toBeVisible();

  // 协议级替身：聊天 SSE 携带 auto_write 画像通知
  await installMockChatApi(page);
  await page.goto("/chat/mock-1");
  await expect(page.getByTestId("chat-thread")).toBeVisible();

  // 撤回 API：第一次失败（500），重试成功 —— 失败可安全重试且不重复写入
  let recallAttempts = 0;
  await page.route("**/api/profiles/notifications/*/recall", async (route) => {
    recallAttempts += 1;
    if (recallAttempts === 1) {
      await route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ error: "internal_error", message: "撤回失败，请稍后重试。" }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ...AUTO_WRITE_NOTIFICATION,
        recalled_at: NOW,
      }),
    });
  });
  await page.route("**/api/profiles/notifications/*/read", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(AUTO_WRITE_NOTIFICATION),
    });
  });

  const composer = page.getByTestId("composer");
  await composer.getByLabel("输入消息").fill("我喜欢蓝色");
  await composer.getByRole("button", { name: "发送消息" }).click();

  // 通知卡片即时展示：标题、正文与来源消息
  const cards = page.getByTestId("chat-profile-notifications");
  await expect(cards).toBeVisible();
  await expect(page.getByRole("heading", { name: "已自动记录" })).toBeVisible();
  await expect(page.getByText("来源消息：“我喜欢蓝色”")).toBeVisible();

  // 第一次撤回失败：行内错误，可安全重试
  await page.getByRole("button", { name: "一键撤回" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "撤回失败" })).toBeVisible();
  // 重试成功：卡片显示已撤回状态
  await page.getByRole("button", { name: "一键撤回" }).click();
  await expect(page.getByText("已撤回，该记录不再用于回答且不会再被自动写入。")).toBeVisible();
  expect(recallAttempts).toBe(2);

  // 关闭卡片 = 标记已读并移除
  await page.getByRole("button", { name: "关闭并标记已读" }).click();
  await expect(cards).toBeHidden();
});

test("聊天内单次情绪提示不写入长期画像", async ({ page }) => {
  await freshAccount(page, "i26-emotion");
  await installMockChatApi(page, "transient");
  await page.goto("/chat/mock-1");
  await expect(page.getByTestId("chat-thread")).toBeVisible();

  const composer = page.getByTestId("composer");
  await composer.getByLabel("输入消息").fill("我今天有点焦虑");
  await composer.getByRole("button", { name: "发送消息" }).click();

  await expect(
    page.getByRole("heading", { name: "情绪仅在本对话中" })
  ).toBeVisible();
  await expect(
    page.getByText("它只作为本次对话的情境信号，不会写入长期画像")
  ).toBeVisible();
});

// ---------------------------------------------------------------------------
// 协议级替身
// ---------------------------------------------------------------------------

const AUTO_WRITE_NOTIFICATION = {
  notification_id: "notif-26-1",
  owner_account_id: "account-26",
  kind: "auto_write",
  title: "已自动记录",
  message: "已自动记录你的「兴趣偏好」：蓝色（来源：本条消息）。如不需要，可一键撤回。",
  source_ref: "mock-1:u-1",
  source_text: "我喜欢蓝色",
  dimension: "interest_preference",
  scene: "companion",
  assertion_id: "assertion-26-1",
  candidate_id: null,
  recallable: true,
  recalled_at: null,
  read_at: null,
  created_at: NOW,
};

const TRANSIENT_NOTIFICATION = {
  notification_id: "notif-26-2",
  owner_account_id: "account-26",
  kind: "transient_emotion",
  title: "情绪仅在本对话中",
  message: "我注意到了你的情绪，它只作为本次对话的情境信号，不会写入长期画像。",
  source_ref: "mock-1:u-1",
  source_text: "我今天有点焦虑",
  dimension: "emotion_trend",
  scene: "companion",
  assertion_id: null,
  candidate_id: null,
  recallable: false,
  recalled_at: null,
  read_at: null,
  created_at: NOW,
};

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
}

const NOW2 = NOW;

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
    created_at: NOW2,
    updated_at: NOW2,
  };
}

function mockAssistant(id: string, content: string): MockMessage {
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
    created_at: NOW2,
    updated_at: NOW2,
  };
}

function sseBlock(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

/** 聊天 SSE 替身：started → profile（画像通知）→ delta → done。 */
async function installMockChatApi(
  page: Page,
  mode: "auto" | "transient" = "auto"
): Promise<void> {
  const state: { messages: MockMessage[]; counter: number } = {
    messages: [],
    counter: 0,
  };
  const notification = mode === "auto" ? AUTO_WRITE_NOTIFICATION : TRANSIENT_NOTIFICATION;

  const history = () => ({
    conversation_id: "mock-1",
    title: "测试对话",
    mode: "companion",
    created_at: NOW2,
    updated_at: NOW2,
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
            created_at: NOW2,
            updated_at: NOW2,
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
    const userMessage = mockUser(`u-${state.counter}`, body.content);
    const assistantMessage = mockAssistant(`a-${state.counter}`, "正在生成");
    state.messages.push(userMessage, { ...assistantMessage, status: "streaming", content: "" });
    const stream =
      sseBlock("started", {
        kind: "started",
        conversation_id: "mock-1",
        user_message_id: userMessage.message_id,
        message_id: assistantMessage.message_id,
        attempt_number: 1,
        thinking: { steps: ["理解你的问题与当前语境", "组织并生成回答"], evidence: [], tools: [], quality: [] },
      }) +
      sseBlock("profile", {
        kind: "profile",
        message_id: userMessage.message_id,
        notifications: [{ ...notification, source_ref: `mock-1:${userMessage.message_id}` }],
      }) +
      sseBlock("delta", { kind: "delta", message_id: assistantMessage.message_id, delta: "正在生成" }) +
      sseBlock("done", {
        kind: "done",
        message_id: assistantMessage.message_id,
        message: { ...assistantMessage, content: "正在生成", status: "done" },
      });
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: stream,
    });
  });
}
