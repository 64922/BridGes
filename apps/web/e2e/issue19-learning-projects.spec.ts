import { expect, test, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

/**
 * Issue 19 — 文件夹式学习项目。
 *
 * 覆盖：空列表与创建（含校验）、改名、项目文件上传/下载/移除与作用域说明、
 * 侧栏会话移动/移出与历史保留、「+」菜单选择学习项目（chip + 持久化）、
 * 从项目新建学习对话（学习模式 + chip）、删除项目（保留对话 / 一并删除）、
 * 404 状态与键盘路径。
 *
 * 项目、对话生命周期断言走真实后端（与 issue12 同一策略）；唯一例外是
 * 「会话移动」用例的消息发送——没有真实 LLM Key 时用户消息不落库，
 * 因此该用例用协议级替身模拟聊天接口（与 issue13/15 同一策略），
 * 学习项目接口仍然走真实后端。
 */

const NOW = "2026-08-03T00:00:00Z";
const PASSWORD = "correct-horse-12";

async function freshAccount(page: Page, prefix: string) {
  const creds = uniqueCredentials(prefix);
  await signUp(page, creds.username, creds.qqEmail, PASSWORD);
  return creds;
}

/** 学习项目服务未启用的 API 实例（reuseExistingServer 场景）跳过相关用例。 */
async function skipIfLearningProjectsDisabled(page: Page) {
  const ready = await page.request.get("/api/learning-projects");
  if (ready.status() === 503) {
    test.skip(true, "当前 API 实例未启用学习项目服务（BRIDGES_DATABASE_URL），跳过该用例。");
  }
}

async function createLearningProjectViaApi(
  page: Page,
  name: string,
  description?: string
): Promise<string> {
  const res = await page.request.post("/api/learning-projects", {
    data: { name, description: description ?? null },
  });
  expect(res.ok()).toBeTruthy();
  const body = await res.json();
  return body.project_id as string;
}

async function createConversationViaApi(page: Page, title: string | null): Promise<string> {
  const res = await page.request.post("/api/chat/conversations", { data: { title } });
  expect(res.ok()).toBeTruthy();
  const body = await res.json();
  return body.conversation_id as string;
}

async function attachConversationToProject(
  page: Page,
  conversationId: string,
  projectId: string
): Promise<void> {
  const res = await page.request.patch(`/api/chat/conversations/${conversationId}`, {
    data: { project_id: projectId },
  });
  expect(res.ok()).toBeTruthy();
}

function conversationIdFromUrl(page: Page): string {
  const match = page.url().match(/\/chat\/([A-Za-z0-9_-]+)/);
  if (!match) throw new Error(`Failed to extract conversation id from ${page.url()}`);
  return match[1];
}

// ---------------------------------------------------------------------------
// 聊天接口协议级替身（仅「会话移动」用例）：创建、列表、历史、发送 SSE、
// PATCH（含 project_id）、停止。投影形状与后端契约一致。
// ---------------------------------------------------------------------------

type MockMessage = {
  message_id: string;
  conversation_id: string;
  role: string;
  attempt_number: number;
  status: string;
  content: string;
  error_code: null;
  error_message: null;
  duration_ms: number | null;
  model_id: string | null;
  run_lock_id: string | null;
  created_at: string;
  updated_at: string;
};

type MockConversation = {
  conversation_id: string;
  title: string;
  mode: "companion" | "study";
  pinned: boolean;
  project_id: string | null;
  created_at: string;
  updated_at: string;
  messages: MockMessage[];
};

function sseStream(messageId: string, userMessageId: string, delta: string) {
  const started = `event: started\ndata: ${JSON.stringify({
    kind: "started",
    conversation_id: "mock-1",
    user_message_id: userMessageId,
    message_id: messageId,
    attempt_number: 1,
  })}\n\n`;
  const deltaEvent = `event: delta\ndata: ${JSON.stringify({ kind: "delta", message_id: messageId, delta })}\n\n`;
  const doneEvent = `event: done\ndata: ${JSON.stringify({
    kind: "done",
    message_id: messageId,
    message: {
      message_id: messageId,
      conversation_id: "mock-1",
      role: "assistant",
      attempt_number: 1,
      status: "done",
      content: delta,
      error_code: null,
      error_message: null,
      duration_ms: 90,
      model_id: "qwen3.7-plus-2026-05-26",
      run_lock_id: "lock-mock",
      created_at: NOW,
      updated_at: NOW,
    },
  })}\n\n`;
  return `${started}${deltaEvent}${doneEvent}`;
}

async function installMockChatApi(page: Page) {
  const conversations = new Map<string, MockConversation>();

  const summary = (conversation: MockConversation) => ({
    conversation_id: conversation.conversation_id,
    title: conversation.title,
    mode: conversation.mode,
    pinned: conversation.pinned,
    project_id: conversation.project_id,
    message_count: conversation.messages.length,
    created_at: conversation.created_at,
    updated_at: conversation.updated_at,
  });
  const history = (conversation: MockConversation) => ({
    ...summary(conversation),
    messages: conversation.messages,
    mode_events: [],
  });
  const json = (status: number, body: unknown) => ({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });

  await page.route("**/api/chat/conversations", async (route) => {
    const request = route.request();
    if (request.method() === "GET") {
      await route.fulfill(
        json(200, { conversations: Array.from(conversations.values()).map(summary) })
      );
      return;
    }
    if (request.method() === "POST") {
      const body = JSON.parse(request.postData() ?? "{}");
      const conversation: MockConversation = {
        conversation_id: "mock-1",
        title: typeof body.title === "string" ? body.title.trim() : "",
        mode: body.mode === "study" ? "study" : "companion",
        pinned: false,
        project_id: body.project_id ?? null,
        created_at: NOW,
        updated_at: NOW,
        messages: [],
      };
      conversations.set(conversation.conversation_id, conversation);
      await route.fulfill(json(201, history(conversation)));
      return;
    }
    await route.continue();
  });

  await page.route("**/api/chat/conversations/**", async (route) => {
    const request = route.request();
    const parts = new URL(request.url()).pathname.split("/").filter(Boolean);
    // parts: ["api", "chat", "conversations", id, "messages"?, messageId?, "stop"?]
    const conversation = conversations.get(parts[3] ?? "");
    if (!conversation) {
      await route.fulfill(json(404, { detail: {} }));
      return;
    }
    if (parts.length === 4) {
      if (request.method() === "GET") {
        await route.fulfill(json(200, history(conversation)));
        return;
      }
      if (request.method() === "PATCH") {
        const body = JSON.parse(request.postData() ?? "{}");
        if (body.title !== undefined) conversation.title = body.title;
        if (body.pinned !== undefined) conversation.pinned = body.pinned;
        if (body.project_id !== undefined) conversation.project_id = body.project_id;
        await route.fulfill(json(200, history(conversation)));
        return;
      }
      if (request.method() === "DELETE") {
        conversations.delete(conversation.conversation_id);
        await route.fulfill({ status: 204, body: "" });
        return;
      }
    }
    if (parts.length === 5 && parts[4] === "messages" && request.method() === "POST") {
      const body = JSON.parse(request.postData() ?? "{}");
      const userMessage: MockMessage = {
        message_id: "u-1",
        conversation_id: conversation.conversation_id,
        role: "user",
        attempt_number: 1,
        status: "done",
        content: body.content,
        error_code: null,
        error_message: null,
        duration_ms: null,
        model_id: null,
        run_lock_id: null,
        created_at: NOW,
        updated_at: NOW,
      };
      const assistantMessage: MockMessage = {
        ...userMessage,
        message_id: "a-1",
        role: "assistant",
        content: "这是替身生成的回答。",
      };
      conversation.messages.push(userMessage, assistantMessage);
      // 模拟服务端首条消息自动命名
      if (!conversation.title) {
        conversation.title = String(body.content).slice(0, 20);
      }
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: sseStream("a-1", "u-1", "这是替身生成的回答。"),
      });
      return;
    }
    if (parts.length === 7 && parts[4] === "messages" && parts[6] === "stop") {
      const target = conversation.messages.find((m) => m.message_id === parts[5]);
      await route.fulfill(json(200, { message: target ?? {} }));
      return;
    }
    await route.continue();
  });
}

test.describe("Issue 19 — 学习项目列表与创建", () => {
  test("空列表呈现真实空状态与新建入口，不是错误", async ({ page }) => {
    await freshAccount(page, "i19-empty");
    await skipIfLearningProjectsDisabled(page);

    await page.goto("/account/projects");
    await expect(page.getByRole("heading", { name: "学习项目" })).toBeVisible();
    const empty = page.getByTestId("state-empty");
    await expect(empty).toContainText("还没有学习项目");
    await expect(page.getByTestId("state-error")).toHaveCount(0);
    await expect(page.getByTestId("learning-project-create")).toBeVisible();

    // 空态 CTA 打开同一个创建对话框（按钮是 state-empty 的兄弟节点）
    const emptyCta = page.getByRole("button", { name: "新建项目" }).last();
    await expect(emptyCta).toBeVisible();
    await emptyCta.click();
    await expect(page.getByRole("dialog", { name: "新建学习项目" })).toBeVisible();
  });

  test("创建：空名称被拦截，创建成功落在详情页，列表显示计数", async ({ page }) => {
    await freshAccount(page, "i19-create");
    await skipIfLearningProjectsDisabled(page);
    const projectName = `线性代数复习 ${Date.now()}`;

    await page.goto("/account/projects");
    await page.getByTestId("learning-project-create").click();
    const dialog = page.getByRole("dialog", { name: "新建学习项目" });
    await expect(dialog).toBeVisible();

    // 仅空格的名称被自定义校验拦截：对话框不关闭、不发起创建
    await dialog.getByLabel("名称").fill("   ");
    await dialog.getByRole("button", { name: "创建" }).click();
    await expect(dialog.getByRole("alert")).toContainText("请输入项目名称");
    await expect(dialog).toBeVisible();
    await expect(page).toHaveURL(/\/account\/projects$/);

    await dialog.getByLabel("名称").fill(projectName);
    await dialog.getByLabel("说明（可选）").fill("期中考试复习范围");
    await dialog.getByRole("button", { name: "创建" }).click();

    await page.waitForURL(/\/account\/projects\/[A-Za-z0-9_-]+/);
    const projectId = page.url().split("/account/projects/")[1];
    await expect(page.getByRole("heading", { name: projectName })).toBeVisible();
    await expect(page.getByText("期中考试复习范围")).toBeVisible();

    await page.goto("/account/projects");
    const row = page.getByTestId(`learning-project-row-${projectId}`);
    await expect(row).toContainText(projectName);
    await expect(row).toContainText("0 个对话 · 0 个文件");
  });

  test("改名：列表菜单发起，列表与详情页同步更新", async ({ page }) => {
    await freshAccount(page, "i19-rename");
    await skipIfLearningProjectsDisabled(page);
    const projectId = await createLearningProjectViaApi(page, "旧名字");

    await page.goto("/account/projects");
    const row = page.getByTestId(`learning-project-row-${projectId}`);
    await row.getByRole("button", { name: "项目操作：旧名字" }).click();
    await page.getByRole("menuitem", { name: "改名" }).click();

    const dialog = page.getByRole("dialog", { name: "修改学习项目" });
    await expect(dialog.getByLabel("名称")).toHaveValue("旧名字");
    await dialog.getByLabel("名称").fill("新名字");
    await dialog.getByRole("button", { name: "保存" }).click();

    await expect(row).toContainText("新名字");
    await row.getByRole("link", { name: "打开学习项目 新名字" }).click();
    await page.waitForURL(`/account/projects/${projectId}`);
    await expect(page.getByRole("heading", { name: "新名字" })).toBeVisible();
  });

  test("未知项目深链呈现 404 状态并可返回列表", async ({ page }) => {
    await freshAccount(page, "i19-notfound");
    await skipIfLearningProjectsDisabled(page);

    await page.goto("/account/projects/not-a-real-project");
    await expect(page.getByTestId("state-empty")).toContainText("学习项目不存在");
    await page.getByRole("button", { name: "返回学习项目列表" }).click();
    await page.waitForURL("/account/projects");
    await expect(page.getByRole("heading", { name: "学习项目" })).toBeVisible();
  });
});

test.describe("Issue 19 — 项目文件", () => {
  test("上传显示中文状态，可下载、可移除，作用域说明可见", async ({ page }) => {
    await freshAccount(page, "i19-files");
    await skipIfLearningProjectsDisabled(page);
    const projectId = await createLearningProjectViaApi(page, "文件项目");

    await page.goto(`/account/projects/${projectId}`);
    // 作用域说明：项目文件仅归属于该学习项目，仅本项目的对话可使用
    await expect(page.getByText(/项目文件仅归属于该学习项目，仅本项目的对话可使用/)).toBeVisible();

    await page.getByTestId("project-file-input").setInputFiles({
      name: "讲义.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("光合作作用于将光能转化为化学能。"),
    });

    // 行出现并带中文摄取状态；e2e 的 API 进程只入队（后台执行器不参与），
    // 状态稳定为「等待解析」，此时下载与移除均无租约冲突。
    const row = page.getByTestId("project-file-row");
    await expect(row).toHaveCount(1);
    await expect(row).toContainText("讲义.txt");
    await expect(page.getByTestId("ingestion-status-queued")).toBeVisible();
    await expect(row).toContainText("等待解析");

    // 下载：真实字节流，文件名保持
    const downloadPromise = page.waitForEvent("download");
    await row.getByRole("button", { name: "文件操作：讲义.txt" }).click();
    await page.getByRole("menuitem", { name: "下载" }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toBe("讲义.txt");
    await expect(page.getByRole("alert").filter({ hasText: "下载失败" })).toHaveCount(0);

    // 移除：确认对话框后行消失，回到空文件提示
    await row.getByRole("button", { name: "文件操作：讲义.txt" }).click();
    await page.getByRole("menuitem", { name: "移除" }).click();
    const dialog = page.getByRole("dialog", { name: "移除项目文件？" });
    await expect(dialog).toContainText("讲义.txt");
    await dialog.getByRole("button", { name: "确认移除" }).click();
    await expect(page.getByTestId("project-file-row")).toHaveCount(0);
    await expect(page.getByText("还没有项目文件")).toBeVisible();
  });
});

test.describe("Issue 19 — 对话归属", () => {
  test("侧栏移动会话到学习项目再移出，徽标随动，历史消息保留", async ({ page }) => {
    await freshAccount(page, "i19-move");
    await skipIfLearningProjectsDisabled(page);
    const projectName = "光合作用专题";
    const projectId = await createLearningProjectViaApi(page, projectName);
    await installMockChatApi(page);

    // 通过新聊天发送流程创建会话（替身消息流）
    await page.goto("/");
    const input = page.getByTestId("composer").getByLabel("输入消息");
    await input.fill("光合作用是什么？");
    await input.press("Enter");
    await page.waitForURL(/\/chat\/mock-1/);
    const thread = page.getByRole("list", { name: "对话消息" });
    await expect(thread).toContainText("光合作用是什么？");
    await expect(thread).toContainText("这是替身生成的回答。");

    const sidebar = page.getByTestId("app-sidebar");
    const item = page.getByTestId("conversation-item-mock-1");
    await expect(item).toBeVisible();

    // 移动到学习项目：选择器列出真实项目，徽标显示项目名
    await sidebar.getByRole("button", { name: "会话操作：光合作用是什么？" }).click();
    await page.getByRole("menuitem", { name: "移动到学习项目…" }).click();
    const picker = page.getByRole("dialog", { name: "选择学习项目" });
    await expect(picker).toBeVisible();
    await page.getByTestId(`learning-project-option-${projectId}`).click();
    await expect(item).toContainText(projectName);

    // 移出学习项目：徽标消失
    await sidebar.getByRole("button", { name: "会话操作：光合作用是什么？" }).click();
    await page.getByRole("menuitem", { name: "移出学习项目" }).click();
    const removeDialog = page.getByRole("dialog", { name: "移出学习项目？" });
    await expect(removeDialog).toContainText("历史消息与附件不会被修改");
    await removeDialog.getByRole("button", { name: "确认移出" }).click();
    await expect(item).not.toContainText(projectName);

    // 历史消息保留：重新打开会话，消息仍在
    await item.getByRole("link").first().click();
    await page.waitForURL(/\/chat\/mock-1/);
    await expect(page.getByRole("list", { name: "对话消息" })).toContainText("光合作用是什么？");
  });

  test("「+」菜单选择学习项目：chip 显示、发送后归属持久化、清除后写回 null", async ({ page }) => {
    await freshAccount(page, "i19-picker");
    await skipIfLearningProjectsDisabled(page);
    const projectName = "线性代数复习";
    const projectId = await createLearningProjectViaApi(page, projectName);

    await page.goto("/");
    const composer = page.getByTestId("composer");
    await composer.getByRole("button", { name: "更多功能" }).click();
    await page
      .getByRole("menu", { name: "更多功能" })
      .getByRole("menuitem", { name: "选择学习项目" })
      .click();
    const picker = page.getByRole("dialog", { name: "选择学习项目" });
    await expect(picker).toBeVisible();
    await page.getByTestId(`learning-project-option-${projectId}`).click();

    const chip = composer.getByTestId("composer-learning-project-chip");
    await expect(chip).toContainText(projectName);

    // 发送创建对话（无 Key 的预检拦截属于既有行为，不影响归属断言）
    await composer.getByLabel("输入消息").fill("帮我梳理矩阵乘法");
    await composer.getByLabel("输入消息").press("Enter");
    await page.waitForURL(/\/chat\/[A-Za-z0-9_-]+$/);
    const conversationId = conversationIdFromUrl(page);

    // chip 反映服务端持久化的项目归属
    // （会话尚无标题与消息——无 Key 预检拦截发送——按服务端契约不进入最近对话
    // 列表，见 chat/service.py 空草稿过滤，因此此处不断言侧栏条目）
    const chatChip = page.getByTestId("composer-learning-project-chip");
    await expect(chatChip).toContainText(projectName);

    // × 清除选择：PATCH null 并立即隐藏
    await page.getByRole("button", { name: "清除学习项目选择" }).click();
    await expect(chatChip).toHaveCount(0);

    // 刷新后仍然无 chip（null 已持久化），服务端投影一致
    await page.reload();
    await expect(page.getByTestId("composer-learning-project-chip")).toHaveCount(0);
    const projection = await (
      await page.request.get(`/api/chat/conversations/${conversationId}`)
    ).json();
    expect(projection.project_id).toBeNull();
  });

  test("从项目新建学习对话：学习模式 + chip，详情页可移出该对话", async ({ page }) => {
    await freshAccount(page, "i19-newchat");
    await skipIfLearningProjectsDisabled(page);
    const projectName = "概率论冲刺";
    const projectId = await createLearningProjectViaApi(page, projectName);

    await page.goto(`/account/projects/${projectId}`);
    await expect(page.getByText("还没有关联的对话")).toBeVisible();
    await page.getByTestId("learning-project-new-chat").click();

    await page.waitForURL(/\/chat\/[A-Za-z0-9_-]+$/);
    const conversationId = conversationIdFromUrl(page);
    const toggle = page.getByTestId("mode-toggle");
    await expect(toggle.getByRole("button", { name: "学习模式" })).toHaveAttribute(
      "aria-pressed",
      "true"
    );
    await expect(page.getByTestId("composer-learning-project-chip")).toContainText(projectName);

    // 详情页「相关对话」列出该对话，可移出项目（历史保留为独立对话）
    await page.goto(`/account/projects/${projectId}`);
    const row = page.getByTestId(`project-conversation-row-${conversationId}`);
    await expect(row).toContainText("未命名对话");
    await expect(row).toContainText("学习模式");
    await row.getByRole("button", { name: "对话操作：未命名对话" }).click();
    await page.getByRole("menuitem", { name: "移出学习项目" }).click();
    const dialog = page.getByRole("dialog", { name: "移出学习项目？" });
    await dialog.getByRole("button", { name: "确认移出" }).click();
    await expect(page.getByTestId(`project-conversation-row-${conversationId}`)).toHaveCount(0);
    await expect(page.getByText("还没有关联的对话")).toBeVisible();
  });
});

test.describe("Issue 19 — 删除学习项目", () => {
  test("保留对话：项目消失，对话留在侧栏且无徽标，仍可打开", async ({ page }) => {
    await freshAccount(page, "i19-del-keep");
    await skipIfLearningProjectsDisabled(page);
    const projectName = "历史专题";
    const projectId = await createLearningProjectViaApi(page, projectName);
    const conversationId = await createConversationViaApi(page, "保留下来的会话");
    await attachConversationToProject(page, conversationId, projectId);

    await page.goto("/account/projects");
    const row = page.getByTestId(`learning-project-row-${projectId}`);
    await expect(row).toContainText("1 个对话");
    await row.getByRole("button", { name: `项目操作：${projectName}` }).click();
    await page.getByRole("menuitem", { name: "删除" }).click();

    const dialog = page.getByRole("dialog", { name: "删除学习项目？" });
    await expect(dialog).toContainText(projectName);
    await expect(dialog.getByRole("radio", { name: /保留对话/ })).toBeChecked();
    await dialog.getByRole("button", { name: "删除项目，保留对话" }).click();

    // 列表回到真实空状态
    await expect(page.getByTestId("state-empty")).toContainText("还没有学习项目");

    // 对话保留为独立对话：侧栏仍在、无项目徽标、可正常打开
    const item = page.getByTestId(`conversation-item-${conversationId}`);
    await expect(item).toBeVisible();
    await expect(item).not.toContainText(projectName);
    await item.getByRole("link").first().click();
    await page.waitForURL(`/chat/${conversationId}`);
    await expect(page.getByTestId("composer")).toBeVisible();
    await expect(page.getByTestId("state-error")).toHaveCount(0);
  });

  test("一并删除：项目与对话同时消失", async ({ page }) => {
    await freshAccount(page, "i19-del-all");
    await skipIfLearningProjectsDisabled(page);
    const projectName = "地理专题";
    const projectId = await createLearningProjectViaApi(page, projectName);
    const conversationId = await createConversationViaApi(page, "将被一并删除的会话");
    await attachConversationToProject(page, conversationId, projectId);

    await page.goto("/account/projects");
    const row = page.getByTestId(`learning-project-row-${projectId}`);
    await row.getByRole("button", { name: `项目操作：${projectName}` }).click();
    await page.getByRole("menuitem", { name: "删除" }).click();

    const dialog = page.getByRole("dialog", { name: "删除学习项目？" });
    await dialog.getByRole("radio", { name: /一并删除/ }).check();
    await expect(dialog.getByRole("radio", { name: /一并删除/ })).toBeChecked();
    await dialog.getByRole("button", { name: "全部删除" }).click();

    await expect(page.getByTestId("state-empty")).toContainText("还没有学习项目");
    await expect(page.getByTestId(`conversation-item-${conversationId}`)).toHaveCount(0);
    const gone = await page.request.get(`/api/chat/conversations/${conversationId}`);
    expect(gone.status()).toBe(404);
  });
});

test.describe("Issue 19 — 键盘路径", () => {
  test("键盘打开创建对话框并提交，Esc 关闭菜单与对话框并归还焦点", async ({ page }) => {
    await freshAccount(page, "i19-keyboard");
    await skipIfLearningProjectsDisabled(page);

    await page.goto("/account/projects");
    const createButton = page.getByTestId("learning-project-create");
    await createButton.focus();
    await page.keyboard.press("Enter");

    // 对话框打开，焦点落入名称字段，键盘输入后 Enter 提交
    const dialog = page.getByRole("dialog", { name: "新建学习项目" });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByLabel("名称")).toBeFocused();
    await page.keyboard.type("键盘项目");
    await page.keyboard.press("Enter");
    await page.waitForURL(/\/account\/projects\/[A-Za-z0-9_-]+/);
    const projectId = page.url().split("/account/projects/")[1];
    await expect(page.getByRole("heading", { name: "键盘项目" })).toBeVisible();

    // Esc 关闭对话框并把焦点归还触发按钮
    await page.goto("/account/projects");
    const createButtonAgain = page.getByTestId("learning-project-create");
    await createButtonAgain.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("dialog", { name: "新建学习项目" })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(createButtonAgain).toBeFocused();

    // Esc 关闭行菜单并把焦点归还触发按钮
    const menuTrigger = page.getByRole("button", { name: "项目操作：键盘项目" });
    await menuTrigger.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("menu")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("menu")).toHaveCount(0);
    await expect(menuTrigger).toBeFocused();
    await expect(page.getByTestId(`learning-project-row-${projectId}`)).toBeVisible();
  });
});
