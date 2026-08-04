import { execFileSync } from "node:child_process";
import path from "node:path";

import { expect, test, type Page } from "@playwright/test";

import { signOut, signUp, uniqueCredentials } from "./helpers/auth";

/**
 * Issue 24 — 跨内容统一桌面搜索。
 *
 * 播种策略（真实后端，与 issue19 同一策略）：会话、学习项目、知识库材料
 * （图片/Markdown/PDF，触发真实摄取与索引）全部走 API；唯一例外是聊天消息
 * ——没有真实 LLM Key 时发送被能力预检拦截、用户消息不落库（见 issue19
 * 注释），因此用户消息由 Python 直接写入 .e2e-data 的 SQLite 库（搜索服务
 * 每次查询直接读库，无进程内缓存，插入即可被检索）。
 */

const PASSWORD = "correct-horse-24";
const KEYWORD = "光合作用";

// 文档摄取由独立 worker 进程处理（playwright 只起 API + Web），这里用
// BackgroundExecutor.run_tick() 单轮处理，与 `BridGes worker` 同一实现。
function runIngestionTick(): void {
  const webRoot = path.join(__dirname, "..");
  execFileSync(
    "python",
    [
      "-c",
      "from bridges.config import get_settings\n" +
        "from bridges.runtime.executor import BackgroundExecutor\n" +
        "print(BackgroundExecutor(get_settings()).run_tick())",
    ],
    {
      cwd: webRoot,
      env: {
        ...process.env,
        BRIDGES_DATABASE_URL: "sqlite:///./.e2e-data/bridges.db",
        BRIDGES_SECRET_KEY: "e2e-chat-test-secret-key",
      },
    }
  );
}

/** 反复执行摄取轮次，直到给定材料全部进入终态（ready/empty/error）。 */
async function processIngestion(page: Page, objectIds: string[]): Promise<void> {
  await expect
    .poll(
      async () => {
        runIngestionTick();
        const res = await page.request.get("/api/knowledge-base/materials");
        if (!res.ok()) return false;
        const materials = (await res.json()) as { object_id: string; status: string }[];
        return materials
          .filter((material) => objectIds.includes(material.object_id))
          .every((material) => ["ready", "empty", "error"].includes(material.status));
      },
      { timeout: 120_000, intervals: [1_000] }
    )
    .toBe(true);
}

// 1×1 透明 PNG（真实图片字节，经内容嗅探识别为 image/png）。
const PNG_1PX = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
  "base64"
);

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

async function createConversation(page: Page, title: string): Promise<string> {
  const res = await page.request.post("/api/chat/conversations", { data: { title } });
  expect(res.ok()).toBeTruthy();
  return ((await res.json()) as { conversation_id: string }).conversation_id;
}

async function createProject(page: Page, name: string): Promise<string> {
  const res = await page.request.post("/api/learning-projects", {
    data: { name, description: null },
  });
  expect(res.ok()).toBeTruthy();
  return ((await res.json()) as { project_id: string }).project_id;
}

/** 直接落库一条用户消息（见文件头注释：无 LLM Key 时 API 发送不落库）。 */
function seedUserMessage(
  accountId: string,
  conversationId: string,
  messageId: string,
  content: string
): void {
  const dbPath = path.join(__dirname, "..", ".e2e-data", "bridges.db");
  const script = [
    "import sqlite3, sys",
    "from datetime import datetime, timezone",
    "now = datetime.now(timezone.utc).isoformat()",
    "db = sqlite3.connect(sys.argv[1], timeout=30)",
    "db.execute(\"INSERT INTO messages (message_id, conversation_id, account_id, role, attempt_number, status, content, created_at, updated_at) VALUES (?,?,?,'user',1,'done',?,?,?)\", (sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], now, now))",
    "db.commit()",
    "db.close()",
  ].join("\n");
  execFileSync("python", ["-c", script, dbPath, messageId, conversationId, accountId, content]);
}

async function uploadMaterial(
  page: Page,
  filename: string,
  mediaType: string,
  content: Buffer | string
): Promise<{ object_id: string; status: string }> {
  const res = await page.request.post("/api/knowledge-base/materials", {
    data: content,
    headers: {
      "Content-Type": mediaType,
      "X-Bridges-Filename": encodeURIComponent(filename),
      "X-Bridges-Upload-Id": `e2e-i24-${Date.now()}-${Math.floor(Math.random() * 1e6)}`,
    },
  });
  expect(res.ok()).toBeTruthy();
  return (await res.json()) as { object_id: string; status: string };
}

/** 含一段英文正文的最小合法 PDF（PyMuPDF 可解析出第 1 页文本）。 */
function minimalPdf(text: string): Buffer {
  const stream = `BT /F1 12 Tf 72 720 Td (${text}) Tj ET`;
  const objects = [
    "<</Type/Catalog/Pages 2 0 R>>",
    "<</Type/Pages/Kids[3 0 R]/Count 1>>",
    "<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
    `<</Length ${stream.length}>>stream\n${stream}\nendstream`,
    "<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
  ];
  let pdf = "%PDF-1.4\n";
  const offsets: number[] = [];
  objects.forEach((body, index) => {
    offsets.push(Buffer.byteLength(pdf, "latin1"));
    pdf += `${index + 1} 0 obj\n${body}\nendobj\n`;
  });
  const xrefPos = Buffer.byteLength(pdf, "latin1");
  pdf += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  for (const offset of offsets) {
    pdf += `${String(offset).padStart(10, "0")} 00000 n \n`;
  }
  pdf += `trailer\n<</Size ${objects.length + 1}/Root 1 0 R>>\nstartxref\n${xrefPos}\n%%EOF`;
  return Buffer.from(pdf, "latin1");
}

/** 轮询真实搜索 API，直到摄取/索引推进到期望结果数。 */
async function waitForSearchResults(page: Page, query: string, minCount: number): Promise<void> {
  await expect
    .poll(
      async () => {
        const res = await page.request.get(`/api/search?q=${encodeURIComponent(query)}`);
        if (!res.ok()) return 0;
        const body = (await res.json()) as { results?: unknown[] };
        return body.results?.length ?? 0;
      },
      { timeout: 90_000, intervals: [1_000, 2_000, 3_000] }
    )
    .toBeGreaterThanOrEqual(minCount);
}

interface SeedResult {
  accountId: string;
  conversationId: string;
  messageId: string;
  projectId: string;
  imageObjectId: string;
  documentObjectId: string;
}

/** 播种四类内容：会话标题+消息（聊天）、PNG（图片）、Markdown（文档）、项目。 */
async function seedFourTypes(page: Page): Promise<SeedResult> {
  const accountId = await getAccountId(page);
  const conversationId = await createConversation(page, `${KEYWORD}讨论`);
  const messageId = `msg-i24-${Date.now()}`;
  seedUserMessage(accountId, conversationId, messageId, `我们一起学习${KEYWORD}的过程`);
  const projectId = await createProject(page, `${KEYWORD}项目`);
  const move = await page.request.patch(`/api/chat/conversations/${conversationId}`, {
    data: { project_id: projectId },
  });
  expect(move.ok()).toBeTruthy();
  const image = await uploadMaterial(page, `${KEYWORD}示意图.png`, "image/png", PNG_1PX);
  const document = await uploadMaterial(
    page,
    `${KEYWORD}讲义.md`,
    "text/markdown",
    `# ${KEYWORD}讲义\n\n${KEYWORD}是植物把光能转化为化学能的过程。\n`
  );
  await waitForSearchResults(page, KEYWORD, 5);
  return {
    accountId,
    conversationId,
    messageId,
    projectId,
    imageObjectId: image.object_id,
    documentObjectId: document.object_id,
  };
}

test.describe("Issue 24 — 统一桌面搜索", () => {
  // 播种含真实摄取与索引构建（按需驱动 worker 轮次），放宽整例超时。
  test.setTimeout(180_000);

  test("Ctrl+K 从聊天页打开搜索并聚焦输入框，Esc 返回原上下文", async ({ page }) => {
    await freshAccount(page, "i24-kbd");
    const accountId = await getAccountId(page);
    const conversationId = await createConversation(page, "键盘导航对话");
    const messageId = `msg-i24-kbd-${Date.now()}`;
    seedUserMessage(accountId, conversationId, messageId, "锚点测试消息");

    await page.goto(`/chat/${conversationId}`);
    await expect(page.getByText("锚点测试消息")).toBeVisible();

    await page.keyboard.press("Control+k");
    await page.waitForURL("**/search");
    await expect(page.getByTestId("search-input")).toBeFocused();

    await page.keyboard.press("Escape");
    await page.waitForURL(`**/chat/${conversationId}`);
  });

  test("单次查询返回四类结果、高亮命中片段，类型/项目筛选与清除筛选", async ({ page }) => {
    await freshAccount(page, "i24-types");
    await seedFourTypes(page);

    await page.goto("/search");
    await page.getByTestId("search-input").fill(KEYWORD);

    // 四类分组齐全：聊天（标题+消息 2 条）、图片、文档、项目各 1 条
    await expect(page.getByTestId("search-group-chat")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("search-group-image")).toBeVisible();
    await expect(page.getByTestId("search-group-document")).toBeVisible();
    await expect(page.getByTestId("search-group-project")).toBeVisible();
    await expect(
      page.getByTestId("search-group-chat").getByTestId("search-result-row")
    ).toHaveCount(2);

    // 命中片段以 <mark> 高亮
    await expect(page.locator("mark").first()).toContainText(KEYWORD);

    // 类型 tab 计数（标题+消息=2 条聊天命中）
    await expect(page.getByTestId("search-tab-all")).toContainText("5");
    await expect(page.getByTestId("search-tab-chat")).toContainText("2");
    await expect(page.getByTestId("search-tab-image")).toContainText("1");
    await expect(page.getByTestId("search-tab-document")).toContainText("1");
    await expect(page.getByTestId("search-tab-project")).toContainText("1");

    // 类型筛选：只看图片
    await page.getByTestId("search-tab-image").click();
    await expect(page.getByTestId("search-group-image")).toBeVisible();
    await expect(page.getByTestId("search-group-chat")).toHaveCount(0);
    await page.getByTestId("search-tab-all").click();
    await expect(page.getByTestId("search-group-chat")).toBeVisible();

    // 项目筛选：会话已移入项目，图片/文档不在项目内
    await page
      .getByTestId("search-project-filter")
      .selectOption({ label: `${KEYWORD}项目` });
    await expect(page.getByTestId("search-group-chat")).toBeVisible();
    await expect(page.getByTestId("search-group-image")).toHaveCount(0);
    await expect(page.getByTestId("search-group-document")).toHaveCount(0);

    // 清除筛选：恢复全部结果，查询词保留
    await page.getByTestId("search-clear-filters").click();
    await expect(page.getByTestId("search-group-image")).toBeVisible();
    await expect(page.getByTestId("search-group-document")).toBeVisible();
    await expect(page.getByTestId("search-input")).toHaveValue(KEYWORD);
    await expect(page.getByTestId("search-project-filter")).toHaveValue("");
  });

  test("键盘 ↓+Enter 跳转聊天结果，URL 带 message 锚点且消息元素可见", async ({ page }) => {
    await freshAccount(page, "i24-anchor");
    const accountId = await getAccountId(page);
    const conversationId = await createConversation(page, "暗反应讨论");
    const messageId = `msg-i24-anchor-${Date.now()}`;
    seedUserMessage(accountId, conversationId, messageId, "暗反应阶段固定二氧化碳");

    await page.goto("/search");
    await page.getByTestId("search-input").fill("暗反应");
    await expect(page.getByTestId("search-result-row").first()).toBeVisible({ timeout: 15_000 });

    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("Enter");
    await page.waitForURL(`**/chat/${conversationId}?message=${messageId}`);
    await expect(page.locator(`#msg-${messageId}`)).toBeVisible();
    await expect(page.locator(`#msg-${messageId}`)).toContainText("暗反应阶段固定二氧化碳");
  });

  test("文档跳转知识库详情并展示页码锚点，项目进入详情，图片就地预览", async ({ page }) => {
    await freshAccount(page, "i24-jump");
    const seed = await seedFourTypes(page);
    // 额外播种 PDF：正文命中带页码锚点（驱动 worker 轮次完成真实摄取）
    const pdf = await uploadMaterial(
      page,
      "photosynthesis-notes.pdf",
      "application/pdf",
      minimalPdf("photosynthesis calvin cycle")
    );
    await processIngestion(page, [pdf.object_id]);
    await expect
      .poll(
        async () => {
          const res = await page.request.get("/api/search?q=photosynthesis");
          if (!res.ok()) return null;
          const body = await res.json();
          const doc = (body.results ?? []).find(
            (item: { result_type: string }) => item.result_type === "document"
          );
          return doc?.page_number ?? null;
        },
        { timeout: 90_000, intervals: [1_000, 2_000, 3_000] }
      )
      .toBe(1);

    // 文档结果 → 知识库详情对话框 + 页码锚点说明
    await page.goto("/search");
    await page.getByTestId("search-input").fill("photosynthesis");
    const documentRow = page.locator('[data-result-type="document"]');
    await expect(documentRow).toBeVisible({ timeout: 15_000 });
    await documentRow.click();
    await page.waitForURL("**/knowledge-base?material=*&page=1");
    const dialog = page.getByTestId("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText("photosynthesis-notes.pdf");
    await expect(page.getByTestId("kb-detail-anchor")).toContainText("第 1 页");
    await page.keyboard.press("Escape");

    // 项目结果 → 项目详情
    await page.goto("/search");
    await page.getByTestId("search-input").fill(KEYWORD);
    const projectRow = page.locator('[data-result-type="project"]');
    await expect(projectRow).toBeVisible({ timeout: 15_000 });
    await projectRow.click();
    await page.waitForURL(`**/account/projects/${seed.projectId}`);

    // 图片结果 → 就地预览对话框（经材料下载通道构造 object URL）
    await page.goto("/search");
    await page.getByTestId("search-input").fill(KEYWORD);
    const imageRow = page.locator('[data-result-type="image"]');
    await expect(imageRow).toBeVisible({ timeout: 15_000 });
    await imageRow.click();
    await expect(page.getByTestId("dialog")).toBeVisible();
    await expect(page.getByTestId("search-image-preview")).toBeVisible();
  });

  test("空查询引导与无结果态相互区分，无结果给出建议", async ({ page }) => {
    await freshAccount(page, "i24-states");
    await page.goto("/search");

    await expect(page.getByTestId("state-empty")).toContainText(
      "输入关键词，搜索聊天、图片、文档和学习项目"
    );

    await page.getByTestId("search-input").fill("绝不存在的关键词xyz");
    await expect(page.getByTestId("state-empty")).toContainText("没有找到", {
      timeout: 15_000,
    });
    await expect(page.getByTestId("state-empty")).toContainText("换个关键词试试");
  });

  test("搜索失败展示错误态，重试保留查询与筛选条件", async ({ page }) => {
    await freshAccount(page, "i24-retry");
    let fail = true;
    await page.route("**/api/search**", async (route) => {
      if (fail) {
        await route.fulfill({
          status: 500,
          contentType: "application/json",
          body: JSON.stringify({ detail: { message: "搜索服务暂时不可用，请稍后重试。" } }),
        });
        return;
      }
      await route.continue();
    });

    await page.goto("/search");
    await page.getByTestId("search-input").fill("重试关键词");
    await expect(page.getByTestId("state-error")).toContainText("搜索失败", { timeout: 15_000 });

    // 错误后调整筛选（此刻请求仍失败），随后恢复后端并点重试
    await page.getByTestId("search-tab-chat").click();
    await expect(page.getByTestId("state-error")).toBeVisible();
    fail = false;
    await page.getByRole("button", { name: "重试" }).click();

    // 重试成功（真实后端无内容 → 无结果态），查询词与类型筛选均保留
    await expect(page.getByTestId("state-empty")).toContainText("没有找到", { timeout: 15_000 });
    await expect(page.getByTestId("search-input")).toHaveValue("重试关键词");
    await expect(page.getByTestId("search-tab-chat")).toHaveAttribute("aria-pressed", "true");
  });

  test("双账户隔离：账户 B 搜不到账户 A 的同名内容", async ({ page }) => {
    await freshAccount(page, "i24-alice");
    await createProject(page, "隔离关键词项目");
    await createConversation(page, "隔离关键词会话");
    await signOut(page);

    await freshAccount(page, "i24-bob");
    await page.goto("/search");
    await page.getByTestId("search-input").fill("隔离关键词");
    await expect(page.getByTestId("state-empty")).toContainText("没有找到", {
      timeout: 15_000,
    });

    const res = await page.request.get(`/api/search?q=${encodeURIComponent("隔离关键词")}`);
    expect(res.ok()).toBeTruthy();
    const body = (await res.json()) as { results?: unknown[] };
    expect(body.results ?? []).toHaveLength(0);
  });
});
