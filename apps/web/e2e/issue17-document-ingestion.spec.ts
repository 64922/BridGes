import { expect, test, type Page } from "@playwright/test";

const NOW = "2026-08-04T00:00:00Z";

const TEST_ACCOUNT = {
  id: "account-issue17",
  username: "Issue 17",
  qq_email: "171717@qq.com",
  avatar_choice: "initials",
  has_uploaded_avatar: false,
  avatar_updated_at: null,
  created_at: NOW,
  updated_at: NOW,
};

type Attachment = {
  object_id: string;
  original_filename: string;
  media_type: string;
  content_length: number;
  content_hash: string;
  conversation_id: string;
  message_id: string;
  status: "uploaded" | "bound";
  ingestion_status: string;
  ingestion_error: string | null;
  created_at: string;
  updated_at: string;
};

function attachment(overrides: Partial<Attachment> = {}): Attachment {
  return {
    object_id: "obj-notes",
    original_filename: "课程笔记.txt",
    media_type: "text/plain",
    content_length: 128,
    content_hash: "abc123",
    conversation_id: "conv-issue17",
    message_id: "msg-user-1",
    status: "bound",
    ingestion_status: "queued",
    ingestion_error: null,
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  };
}

function ingestionDetail(overrides: Record<string, unknown> = {}) {
  return {
    document_id: "doc-obj-notes",
    object_id: "obj-notes",
    conversation_id: "conv-issue17",
    status: "ready",
    parser_version: "text-utf8-v1",
    content_hash: "abc123",
    title: "课程笔记",
    page_count: 0,
    section_count: 0,
    chunk_count: 2,
    vector_enabled: true,
    vector_indexed: true,
    failure_stage: null,
    failure_reason: null,
    retry_count: 1,
    index_rebuilding: false,
    vector_unavailable_reason: null,
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  };
}

async function installConversationApi(page: Page, list: Attachment[]) {
  const attachments = new Map(list.map((item) => [item.object_id, item]));

  await page.route("**/api/chat/conversations/*", async (route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        conversation_id: "conv-issue17",
        title: "Issue 17 对话",
        mode: "companion",
        pinned: false,
        project_id: null,
        created_at: NOW,
        updated_at: NOW,
        messages: [
          {
            message_id: "msg-user-1",
            conversation_id: "conv-issue17",
            role: "user",
            attempt_number: 1,
            status: "done",
            content: "请阅读这份材料",
            attachments: Array.from(attachments.values()),
            thinking: null,
            error_code: null,
            error_message: null,
            duration_ms: null,
            model_id: null,
            run_lock_id: null,
            created_at: NOW,
            updated_at: NOW,
          },
        ],
        mode_events: [],
      }),
    });
  });

  await page.route("**/api/chat/conversations/*/attachments/*/ingestion", async (route) => {
    const url = new URL(route.request().url());
    const segments = url.pathname.split("/");
    const objectId = segments.at(-2) ?? "";
    const item = attachments.get(objectId);
    if (!item) {
      await route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify({ detail: { error: "attachment_not_found", message: "附件不存在或没有访问权限。" } }),
      });
      return;
    }
    if (route.request().method() === "POST") {
      item.ingestion_status = "queued";
      item.ingestion_error = null;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(
        ingestionDetail({
          status: item.ingestion_status,
          failure_reason: item.ingestion_error,
        })
      ),
    });
  });

  // 重试端点（POST .../ingestion/retry）：重新入队并返回最新投影
  await page.route("**/api/chat/conversations/*/attachments/*/ingestion/retry", async (route) => {
    const url = new URL(route.request().url());
    // /api/chat/conversations/{conv}/attachments/{object}/ingestion/retry
    const objectId = url.pathname.split("/").at(-3) ?? "";
    const item = attachments.get(objectId);
    if (!item) {
      await route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify({ detail: { error: "attachment_not_found", message: "附件不存在或没有访问权限。" } }),
      });
      return;
    }
    item.ingestion_status = "queued";
    item.ingestion_error = null;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(
        ingestionDetail({ status: "queued", failure_reason: null })
      ),
    });
  });
}

async function installAuthenticatedSession(page: Page) {
  await page.context().addCookies([
    { name: "bridges_session", value: "issue17-session", url: "http://127.0.0.1:3000" },
  ]);
  await page.route("**/api/auth/session", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ account: TEST_ACCOUNT, session: {}, subject: {} }),
    })
  );
  await page.route("**/api/auth/device/accounts", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        accounts: [],
        current_account: TEST_ACCOUNT,
        current_session_id: "issue17-session",
      }),
    })
  );
  await page.route("**/api/chat/ingestion/index", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        embedding_probed: true,
        embedding_available: true,
        vector_unavailable_reason: null,
        active_version: {
          version_id: "idx-v1",
          contract: {
            model_id: "text-embedding-v4",
            dimensions: 1024,
            normalization: "l2",
            chunker: "hash-chunker-v1",
            schema_version: "index-schema-v1",
            contract_hash: "cafe",
          },
          status: "active",
          expected_chunk_count: 2,
          chunk_count: 2,
          vector_count: 2,
          error_message: null,
          built_at: NOW,
          switched_at: NOW,
          created_at: NOW,
        },
        versions: [],
      }),
    })
  );
}

test.describe("Issue 17 文档摄取状态", () => {
  test("附件卡片显示摄取状态芯片并可展开详情", async ({ page }) => {
    await installAuthenticatedSession(page);
    await installConversationApi(page, [attachment({ ingestion_status: "queued" })]);
    await page.goto("/chat/conv-issue17");

    // queued 状态芯片
    await expect(page.getByTestId("ingestion-status-queued")).toBeVisible();
    await expect(page.getByText("等待解析")).toBeVisible();

    // 展开详情：按需请求摄取详情接口
    await page.getByRole("button", { name: /详情/ }).click();
    await expect(page.getByText("解析器", { exact: false })).toBeVisible();
    await expect(page.getByText("text-utf8-v1")).toBeVisible();
    await expect(page.getByText("分块数", { exact: false })).toBeVisible();
    await expect(page.getByText("已写入")).toBeVisible();
    // 展开按钮的 aria-expanded 状态
    await expect(page.getByRole("button", { name: /详情/ })).toHaveAttribute("aria-expanded", "true");
  });

  test("失败状态显示中文原因与重试，重试后回到等待解析", async ({ page }) => {
    await installAuthenticatedSession(page);
    await installConversationApi(page, [
      attachment({
        ingestion_status: "error",
        ingestion_error: "PDF 解析失败：文件已损坏或不是有效的 PDF。",
      }),
    ]);
    await page.goto("/chat/conv-issue17");

    // error 状态：芯片 + 中文原因 + 重试按钮
    const chip = page.getByTestId("ingestion-status-error");
    await expect(chip).toBeVisible();
    await expect(chip).toContainText("解析失败");
    await expect(page.getByRole("button", { name: "重新解析附件" }).first()).toBeVisible();

    // 详情中可见失败原因与重新解析按钮
    await page.getByRole("button", { name: /详情/ }).click();
    await expect(
      page.locator('[data-testid="attachment-ingestion-detail"]').getByText("PDF 解析失败：文件已损坏或不是有效的 PDF。", { exact: true })
    ).toBeVisible();
    const retryInDetail = page.getByRole("button", { name: "重新解析附件" }).last();
    await expect(retryInDetail).toBeVisible();
    await retryInDetail.click();

    // 重试后：投影刷新为 queued（对话重新加载）
    await expect(page.getByTestId("ingestion-status-queued")).toBeVisible();
    await expect(page.getByText("等待解析")).toBeVisible();
  });

  test("ready 状态显示已可检索且向量已写入", async ({ page }) => {
    await installAuthenticatedSession(page);
    await installConversationApi(page, [attachment({ ingestion_status: "ready" })]);
    await page.goto("/chat/conv-issue17");

    await expect(page.getByTestId("ingestion-status-ready")).toBeVisible();
    await expect(page.getByText("已可检索")).toBeVisible();
  });

  test("权限不足详情接口返回 404 时展示错误而非掩盖", async ({ page }) => {
    await installAuthenticatedSession(page);
    await installConversationApi(page, [
      attachment({ object_id: "obj-other", ingestion_status: "none" }),
    ]);
    // 详情接口对该对象返回 404（模拟越权/不存在）
    await page.route("**/api/chat/conversations/*/attachments/obj-other/ingestion", (route) =>
      route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify({ detail: { error: "attachment_not_found", message: "附件不存在或没有访问权限。" } }),
      })
    );
    await page.goto("/chat/conv-issue17");

    // 未索引芯片正常展示；展开详情时显示 permission 状态与中文说明，
    // 不以空面板掩盖
    await expect(page.getByTestId("ingestion-status-none")).toBeVisible();
    await expect(page.getByText("未索引")).toBeVisible();
    await page.getByRole("button", { name: /详情/ }).click();
    await expect(page.getByTestId("ingestion-status-permission")).toBeVisible();
    await expect(page.getByText("无访问权限")).toBeVisible();
    await expect(
      page.locator('[data-testid="attachment-ingestion-detail"]')
    ).toContainText("附件不存在或没有访问权限");
  });
});
