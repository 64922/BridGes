import { expect, test } from "@playwright/test";

// 接口替身仅验证桌面交互；真实识别质量由本票的教材照片人工验收覆盖。

const NOW = "2026-09-26T00:00:00Z";
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=", "base64");

for (const [width, height] of [[1280, 720], [1440, 900], [1920, 1080]]) {
  test(`学习草稿、模式锁与页级证据恢复 ${width}×${height}`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height });
    await page.context().addCookies([{ name: "bridges_session", value: "study-test", url: testInfo.project.use.baseURL! }]);
    const drafts: Record<string, unknown>[] = [];
    const requests: Record<string, unknown>[] = [];
    const account = { id: "study-owner", username: "学习测试", qq_email: "12345@qq.com",
      avatar_choice: "initials", created_at: NOW, updated_at: NOW };
    const conversation = {
      conversation_id: "study-test", title: "本节书页", mode: "study", mode_locked: true,
      pinned: false, created_at: NOW, updated_at: NOW, messages: [], mode_events: [],
      study: { subsection_id: "study-test", stage: "awaiting_pages", wait_reason: "page_order",
        pages: [{ object_id: "photo-b", ordinal: 1, page_number: 12, same_section: true,
          content_hash: "hash-b", model_id: "test", replaced_object_ids: [], unclear: [],
          fragments: [{ fragment_id: "user-1", kind: "formula", position: "中部公式",
            text: "y=ax+b", confidence: 1, source: "user" }] }] },
    };
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      let body: unknown = {};
      let status = 200;
      if (path === "/api/auth/session") body = { account, session: {}, subject: {} };
      else if (path === "/api/auth/device/accounts") body = { accounts: [], current_account: account };
      else if (path === "/api/chat/conversations") body = { conversations: requests.length > 1 ? [conversation] : [] };
      else if (path === "/api/chat/conversations/study-test") body = conversation;
      else if (path.endsWith("/content")) {
        await route.fulfill({ contentType: "image/png", body: PNG });
        return;
      } else if (path === "/api/chat/attachment-drafts") {
        if (route.request().method() === "POST") {
          const filename = decodeURIComponent(route.request().headers()["x-bridges-filename"]);
          const draft = { object_id: filename, original_filename: filename, media_type: "image/png",
            content_length: PNG.length, content_hash: filename, ingestion_status: "none",
            created_at: NOW, updated_at: NOW };
          drafts.push(draft);
          body = draft;
          status = 201;
        } else body = drafts;
      } else if (path === "/api/chat/first-turn") {
        requests.push(route.request().postDataJSON());
        if (requests.length === 1) {
          status = 503;
          body = { detail: { error: "unavailable", message: "发送暂时失败，请重试。" } };
        } else {
          drafts.length = 0;
          status = 201;
          body = { conversation, user_message: { message_id: "u1" }, assistant_message: { message_id: "a1" } };
        }
      }
      await route.fulfill({ status, json: body });
    });
    await page.goto("/");
    await page.getByRole("button", { name: "学习模式", exact: true }).click();
    const input = page.getByRole("textbox", { name: "输入消息" });
    await input.fill("本节书页");
    const send = page.getByRole("button", { name: "发送消息" });
    await expect(send).toBeDisabled();
    await page.locator('input[type="file"]').setInputFiles([
      { name: "a.png", mimeType: "image/png", buffer: PNG },
      { name: "b.png", mimeType: "image/png", buffer: Buffer.concat([PNG, Buffer.from("b")]) },
    ]);
    await page.getByRole("button", { name: "将 b.png 上移" }).click();
    await send.click();
    await expect(page.getByText("发送暂时失败，请重试。")).toBeVisible();
    await expect(input).toHaveValue("本节书页");
    await expect(page.getByRole("button", { name: "学习模式", exact: true })).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByText("b.png", { exact: true })).toBeVisible();
    await send.click();
    await expect(page).toHaveURL(/\/chat\/study-test$/);
    expect(requests[1].attachment_ids).toEqual(["b.png", "a.png"]);
    expect(requests[1].mode).toBe("study");
    await page.reload();
    await expect(page.getByRole("button", { name: "学习模式", exact: true })).toHaveCount(0);
    const progress = page.getByRole("region", { name: "学习阶段" });
    await expect(progress.getByRole("status")).toContainText("调整页序");
    await progress.getByText(/已识别书页与证据/).click();
    await expect(progress.getByText(/书上第12页/)).toBeVisible();
    await expect(progress.getByText(/用户补录/)).toBeVisible();
    await expect(progress.getByRole("button")).toHaveCount(0);
    await page.screenshot({ path: testInfo.outputPath(`study-${width}.png`), fullPage: true });
  });
}
