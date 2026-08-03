import { expect, test, type Page } from "@playwright/test";

/**
 * Issue 18 — 全局本地知识库桌面页。
 *
 * 业务接口全部用 page.route 模拟（投影形状与后端契约一致），
 * 认证会话沿用 issue17 的 mock 模式，账户切换沿用 issue15 的双账户模式。
 */

const NOW = "2026-08-04T00:00:00Z";

const ACCOUNT_ALICE = {
  id: "account-i18-alice",
  username: "Alice",
  qq_email: "181818@qq.com",
  avatar_choice: "initials",
  has_uploaded_avatar: false,
  avatar_updated_at: null,
  created_at: NOW,
  updated_at: NOW,
};

const ACCOUNT_BOB = { ...ACCOUNT_ALICE, id: "account-i18-bob", username: "Bob", qq_email: "282828@qq.com" };

type Material = {
  document_id: string;
  object_id: string;
  filename: string;
  media_type: string;
  content_length: number;
  content_hash: string;
  content_hash_summary: string;
  source: string;
  status: string;
  title: string | null;
  chunk_count: number;
  failure_stage: string | null;
  failure_reason: string | null;
  retry_count: number;
  vector_enabled: boolean;
  vector_indexed: boolean;
  embedding_available: boolean;
  vector_unavailable_reason: string | null;
  index_version_id: string | null;
  index_rebuilding: boolean;
  usable_for_chat: boolean;
  created_at: string;
  updated_at: string;
};

function material(overrides: Partial<Material> = {}): Material {
  return {
    document_id: "doc-obj-1",
    object_id: "obj-1",
    filename: "课程笔记.pdf",
    media_type: "application/pdf",
    content_length: 2048,
    content_hash: "abcdef0123456789fedcba9876543210",
    content_hash_summary: "abcdef012345",
    source: "本地上传",
    status: "ready",
    title: null,
    chunk_count: 4,
    failure_stage: null,
    failure_reason: null,
    retry_count: 0,
    vector_enabled: true,
    vector_indexed: true,
    embedding_available: true,
    vector_unavailable_reason: null,
    index_version_id: "idx-version-0001",
    index_rebuilding: false,
    usable_for_chat: true,
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  };
}

function notFoundBody() {
  return { detail: { error: "material_not_found", message: "材料不存在或没有访问权限。" } };
}

/** 安装知识库业务接口 mock；items 为可变数组，测试可随时改写。 */
async function installKnowledgeBaseApi(page: Page, items: Material[]) {
  await page.route("**/api/knowledge-base/materials", async (route) => {
    const request = route.request();
    if (request.method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(items),
      });
      return;
    }
    if (request.method() === "POST") {
      const filename = decodeURIComponent(request.headers()["x-bridges-filename"] ?? "未命名.txt");
      const created = material({
        object_id: `obj-${items.length + 1}`,
        document_id: `doc-obj-${items.length + 1}`,
        filename,
        media_type: "text/plain",
        status: "queued",
        vector_indexed: false,
        usable_for_chat: false,
        index_version_id: null,
        chunk_count: 0,
      });
      items.unshift(created);
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify(created),
      });
      return;
    }
    await route.continue();
  });

  await page.route("**/api/knowledge-base/materials/*/retry", async (route) => {
    const objectId = new URL(route.request().url()).pathname.split("/").at(-2) ?? "";
    const item = items.find((entry) => entry.object_id === objectId);
    if (!item) {
      await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify(notFoundBody()) });
      return;
    }
    item.status = "queued";
    item.failure_stage = null;
    item.failure_reason = null;
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(item) });
  });

  await page.route("**/api/knowledge-base/materials/*/rebuild", async (route) => {
    const objectId = new URL(route.request().url()).pathname.split("/").at(-2) ?? "";
    const item = items.find((entry) => entry.object_id === objectId);
    if (!item) {
      await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify(notFoundBody()) });
      return;
    }
    item.index_rebuilding = true;
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(item) });
  });

  await page.route("**/api/knowledge-base/materials/*/download", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/octet-stream",
      headers: { "Content-Disposition": "attachment; filename*=UTF-8''material.bin" },
      body: "mock-bytes",
    });
  });

  await page.route("**/api/knowledge-base/materials/*", async (route) => {
    const objectId = new URL(route.request().url()).pathname.split("/").at(-1) ?? "";
    const item = items.find((entry) => entry.object_id === objectId);
    if (!item) {
      await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify(notFoundBody()) });
      return;
    }
    if (route.request().method() === "DELETE") {
      items.splice(items.indexOf(item), 1);
      await route.fulfill({ status: 204 });
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(item) });
  });
}

/** 认证会话 + 侧栏最近对话的空 mock（与 issue17 同模式）。 */
async function installAuthenticatedSession(page: Page, account = ACCOUNT_ALICE) {
  await page.context().addCookies([
    { name: "bridges_session", value: "issue18-session", url: "http://127.0.0.1:3000" },
  ]);
  await page.route("**/api/auth/session", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ account, session: {}, subject: {} }),
    })
  );
  await page.route("**/api/auth/device/accounts", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        accounts: [],
        current_account: account,
        current_session_id: "issue18-session",
      }),
    })
  );
  await page.route("**/api/chat/conversations", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ conversations: [] }),
    })
  );
}

test.describe("Issue 18 — 全局本地知识库", () => {
  test("首次使用呈现真实空状态，返回新聊天是真实导航", async ({ page }) => {
    await installAuthenticatedSession(page);
    await installKnowledgeBaseApi(page, []);
    await page.goto("/knowledge-base");

    await expect(page.getByRole("heading", { name: "本地知识库" })).toBeVisible();
    const empty = page.getByTestId("state-empty");
    await expect(empty).toContainText("当前账户还没有知识库材料");
    await expect(page.locator("body")).not.toContainText("将在这里呈现");
    // 空状态提供上传引导与返回新聊天两条真实路径
    await expect(empty.getByRole("button", { name: "返回新聊天" })).toBeVisible();

    await empty.getByRole("button", { name: "返回新聊天" }).click();
    await page.waitForURL("/");
    await expect(page.getByRole("heading", { name: "有什么可以帮你的？" })).toBeVisible();
  });

  test("上传材料后行进入处理状态，轮询直到就绪", async ({ page }) => {
    const items: Material[] = [];
    await installAuthenticatedSession(page);
    await installKnowledgeBaseApi(page, items);
    await page.goto("/knowledge-base");

    await page.getByTestId("kb-file-input").setInputFiles({
      name: "讲义.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("光合作作用于将光能转化为化学能。"),
    });

    // 上传完成 → 列表出现新材料（等待解析），随后进入轮询
    await expect(page.getByTestId("kb-material-row")).toHaveCount(1);
    await expect(page.getByTestId("ingestion-status-queued")).toBeVisible();

    // 后台处理推进：processing → ready（轮询间隔约 2.5s）
    items[0].status = "processing";
    await expect(page.getByTestId("ingestion-status-processing")).toBeVisible({ timeout: 10_000 });

    items[0].status = "ready";
    items[0].chunk_count = 2;
    items[0].usable_for_chat = true;
    await expect(page.getByTestId("ingestion-status-ready")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText("讲义.txt")).toBeVisible();
  });

  test("就绪材料列表呈现完整元数据，详情对话框展示逐阶段状态", async ({ page }) => {
    await installAuthenticatedSession(page);
    await installKnowledgeBaseApi(page, [material()]);
    await page.goto("/knowledge-base");

    const row = page.getByTestId("kb-material-row");
    await expect(row).toHaveCount(1);
    await expect(row).toContainText("课程笔记.pdf");
    await expect(row).toContainText("PDF 文档");
    await expect(row).toContainText("2.0 KB");
    await expect(row).toContainText("本地上传");
    await expect(row).toContainText("abcdef012345");
    await expect(row).toContainText("索引 idx-vers");
    await expect(row).toContainText("可用于对话");
    await expect(page.getByTestId("ingestion-status-ready")).toBeVisible();

    await page.getByRole("button", { name: "材料操作：课程笔记.pdf" }).click();
    await page.getByRole("menuitem", { name: "查看详情" }).click();

    const dialog = page.getByTestId("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText("abcdef012345");
    await expect(dialog).toContainText("application/pdf");
    await expect(dialog).toContainText("本地上传");
    await expect(dialog).toContainText("分块数");
    // 四个阶段全部成功
    await expect(dialog.getByText("成功")).toHaveCount(5); // 4 阶段 + 可用于对话
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
  });

  test("失败材料显示中文原因，重试后回到等待解析", async ({ page }) => {
    const failed = material({
      status: "error",
      failure_stage: "parse",
      failure_reason: "PDF 解析失败：文件已损坏或不是有效的 PDF。",
      retry_count: 1,
      usable_for_chat: false,
      vector_indexed: false,
    });
    await installAuthenticatedSession(page);
    await installKnowledgeBaseApi(page, [failed]);
    await page.goto("/knowledge-base");

    const row = page.getByTestId("kb-material-row");
    await expect(page.getByTestId("ingestion-status-error")).toBeVisible();
    await expect(
      row.getByText("解析阶段失败：PDF 解析失败：文件已损坏或不是有效的 PDF。")
    ).toBeVisible();

    await page.getByRole("button", { name: "材料操作：课程笔记.pdf" }).click();
    await page.getByRole("menuitem", { name: "重试" }).click();

    await expect(page.getByTestId("ingestion-status-queued")).toBeVisible();
    // 恢复稳定状态，停止轮询
    failed.status = "ready";
    failed.usable_for_chat = true;
    await expect(page.getByTestId("ingestion-status-ready")).toBeVisible({ timeout: 10_000 });
  });

  test("删除需确认，Esc 取消，确认后移除该材料", async ({ page }) => {
    await installAuthenticatedSession(page);
    await installKnowledgeBaseApi(page, [
      material(),
      material({ object_id: "obj-2", document_id: "doc-obj-2", filename: "参考图片.png", media_type: "image/png" }),
    ]);
    await page.goto("/knowledge-base");
    await expect(page.getByTestId("kb-material-row")).toHaveCount(2);

    await page.getByRole("button", { name: "材料操作：课程笔记.pdf" }).click();
    await page.getByRole("menuitem", { name: "删除" }).click();

    const dialog = page.getByTestId("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText("操作不可撤销");
    await expect(dialog).toContainText("对话历史不会被修改");

    // Esc 关闭：材料仍在
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(page.getByTestId("kb-material-row")).toHaveCount(2);

    // 确认删除：行被移除
    await page.getByRole("button", { name: "材料操作：课程笔记.pdf" }).click();
    await page.getByRole("menuitem", { name: "删除" }).click();
    await page.getByRole("button", { name: "确认删除" }).click();
    await expect(page.getByTestId("kb-material-row")).toHaveCount(1);
    await expect(page.getByText("课程笔记.pdf")).toHaveCount(0);
    await expect(page.getByText("参考图片.png")).toBeVisible();
  });

  test("处理中的材料删除返回 409 时展示可恢复中文提示，材料保留", async ({ page }) => {
    const processing = material({ status: "processing", usable_for_chat: false, vector_indexed: false });
    await installAuthenticatedSession(page);
    await installKnowledgeBaseApi(page, [processing]);
    await page.route("**/api/knowledge-base/materials/obj-1", async (route) => {
      if (route.request().method() === "DELETE") {
        await route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({
            detail: {
              error: "material_processing",
              message: "材料正在被后台任务处理，暂时无法删除，请稍后重试。",
            },
          }),
        });
        return;
      }
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(processing) });
    });
    await page.goto("/knowledge-base");

    await page.getByRole("button", { name: "材料操作：课程笔记.pdf" }).click();
    await page.getByRole("menuitem", { name: "删除" }).click();
    await page.getByRole("button", { name: "确认删除" }).click();

    // 对话框保持打开，role=alert 展示后端中文原因，材料仍在列表中
    await expect(page.getByTestId("dialog")).toBeVisible();
    await expect(page.getByTestId("kb-dialog-error")).toContainText(
      "材料正在被后台任务处理，暂时无法删除，请稍后重试。"
    );
    await expect(page.getByTestId("kb-material-row")).toHaveCount(1);

    await page.getByRole("button", { name: "取消" }).click();
    await expect(page.getByTestId("dialog")).toHaveCount(0);
    // 处理结束，停止轮询
    processing.status = "ready";
  });

  test("向量降级时展示页面级横幅与行级徽标，不伪装为整体可用", async ({ page }) => {
    const degraded = material({
      embedding_available: false,
      vector_indexed: false,
      vector_unavailable_reason: "Embedding 服务未配置。",
    });
    await installAuthenticatedSession(page);
    await installKnowledgeBaseApi(page, [degraded]);
    await page.goto("/knowledge-base");

    const banner = page.getByTestId("kb-vector-banner");
    await expect(banner).toBeVisible();
    await expect(banner).toContainText("向量检索当前不可用");
    await expect(banner).toContainText("Embedding 服务未配置。");
    await expect(banner).toContainText("全文检索不受影响");

    const row = page.getByTestId("kb-material-row");
    await expect(row).toContainText("向量不可用");
    // 材料本身仍已就绪、可用于对话（全文检索）
    await expect(page.getByTestId("ingestion-status-ready")).toBeVisible();
    await expect(row).toContainText("可用于对话");
  });

  test("筛选无结果呈现独立空态，清除筛选恢复列表", async ({ page }) => {
    await installAuthenticatedSession(page);
    await installKnowledgeBaseApi(page, [material()]);
    await page.goto("/knowledge-base");
    await expect(page.getByTestId("kb-material-row")).toHaveCount(1);

    await page.getByTestId("kb-search-input").fill("绝不存在的关键词xyz");
    const empty = page.getByTestId("state-empty");
    await expect(empty).toContainText("没有匹配的材料");
    await expect(page.getByText("当前账户还没有知识库材料")).toHaveCount(0);

    await empty.getByRole("button", { name: "清除筛选" }).click();
    await expect(page.getByTestId("kb-material-row")).toHaveCount(1);

    // 状态筛选同样生效
    await page.getByTestId("kb-filter-failed").click();
    await expect(page.getByTestId("state-empty")).toContainText("没有匹配的材料");
    await page.getByTestId("kb-filter-all").click();
    await expect(page.getByTestId("kb-material-row")).toHaveCount(1);
  });

  test("切换账户后列表只呈现另一账户的材料", async ({ page }) => {
    let current = ACCOUNT_ALICE;
    const aliceItems = [material({ filename: "Alice 的材料.pdf" })];
    const bobItems = [
      material({ object_id: "obj-bob", document_id: "doc-obj-bob", filename: "Bob 的材料.txt", media_type: "text/plain" }),
    ];

    await page.context().addCookies([
      { name: "bridges_session", value: "issue18-session", url: "http://127.0.0.1:3000" },
    ]);
    await page.route("**/api/auth/session", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ account: current, session: {}, subject: {} }),
      })
    );
    await page.route("**/api/auth/device/accounts", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          accounts: [
            { session_id: "s-alice", username: "Alice", masked_qq_email: "18***@qq.com", avatar_choice: "initials", has_uploaded_avatar: false, status: "active", is_current: current.id === ACCOUNT_ALICE.id },
            { session_id: "s-bob", username: "Bob", masked_qq_email: "28***@qq.com", avatar_choice: "initials", has_uploaded_avatar: false, status: "active", is_current: current.id === ACCOUNT_BOB.id },
          ],
          current_account: current,
          current_session_id: current.id === ACCOUNT_ALICE.id ? "s-alice" : "s-bob",
        }),
      })
    );
    await page.route("**/api/auth/device/switch", async (route) => {
      current = ACCOUNT_BOB;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ current_account: ACCOUNT_BOB, current_session_id: "s-bob" }),
      });
    });
    await page.route("**/api/chat/conversations", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ conversations: [] }) })
    );
    await page.route("**/api/knowledge-base/materials", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(current.id === ACCOUNT_ALICE.id ? aliceItems : bobItems),
      })
    );

    await page.goto("/knowledge-base");
    await expect(page.getByText("Alice 的材料.pdf")).toBeVisible();

    await page.getByRole("button", { name: /账户菜单：Alice/ }).click();
    await page.getByRole("menuitem", { name: "切换账号" }).click();
    await page.getByRole("button", { name: /Bob，28\*\*\*@qq.com/ }).click();

    await expect(page.getByRole("button", { name: /账户菜单：Bob/ })).toBeVisible();
    await expect(page.getByText("Alice 的材料.pdf")).toHaveCount(0);
    await expect(page.getByText("Bob 的材料.txt")).toBeVisible();
  });
});
