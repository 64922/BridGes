import { expect, test } from "@playwright/test";

// 仅验证桌面交互；模型来源核验与事务恢复由聊天 API 集成测试覆盖。
const NOW = "2026-09-26T00:00:00Z";
for (const [width, height] of [[1280, 720], [1440, 900], [1920, 1080]]) {
  test(`辅导提问、来源与待确认追加页 ${width}×${height}`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height });
    await page.context().addCookies([{ name: "bridges_session", value: "tutor-test", url: testInfo.project.use.baseURL! }]);
    const account = { id: "study-owner", username: "学习测试", qq_email: "12345@qq.com",
      avatar_choice: "initials", created_at: NOW, updated_at: NOW };
    const pageEvidence = { object_id: "photo-1", ordinal: 1, page_number: 12, same_section: true,
      content_hash: "hash-1", model_id: "test", fragments: [{ fragment_id: "f1", kind: "formula",
        position: "中部公式", text: "y=ax+b", confidence: 1, source: "photo" }], unclear: [] };
    const conversation = {
      conversation_id: "tutor-test", title: "线性函数", mode: "study", mode_locked: true,
      pinned: false, created_at: NOW, updated_at: NOW, mode_events: [],
      messages: [{ message_id: "a1", conversation_id: "tutor-test", role: "assistant",
        attempt_number: 1, status: "done", created_at: NOW, updated_at: NOW,
        content: "本节书页：a 是斜率。\n\n依据：上传第1页（书上第12页） · 中部公式：y=ax+b\n\n模型知识补充：可以把斜率理解为变化快慢。" }],
      study: { subsection_id: "tutor-test", stage: "tutoring", pages: [pageEvidence],
        page_update: { wait_reason: "unclear_page", pages: [pageEvidence, {
          ...pageEvidence, object_id: "photo-2", ordinal: 2, page_number: 13, same_section: false,
          unclear: [{ position: "整页", reason: "疑似不同小节，请确认或在新对话上传" }],
        }] } },
    };
    let sent: Record<string, unknown> | undefined;
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      let body: unknown = {};
      if (path === "/api/auth/session") body = { account, session: {}, subject: {} };
      else if (path === "/api/auth/device/accounts") body = { accounts: [], current_account: account };
      else if (path === "/api/chat/conversations") body = { conversations: [conversation] };
      else if (path === "/api/chat/conversations/tutor-test") body = conversation;
      else if (path === "/api/chat/attachment-drafts") body = [];
      else if (path.endsWith("/messages") && route.request().method() === "POST") {
        sent = route.request().postDataJSON();
        await route.fulfill({ status: 503, json: { detail: { message: "发送暂时失败，请重试。" } } });
        return;
      }
      await route.fulfill({ json: body });
    });
    await page.goto("/chat/tutor-test");
    const progress = page.getByRole("region", { name: "学习阶段" });
    await expect(progress.getByText("辅导", { exact: true })).toHaveAttribute("aria-current", "step");
    await expect(progress.getByText(/尚未更新本节范围/)).toBeVisible();
    await expect(progress.getByText(/同节归属待确认/)).toBeVisible();
    await expect(page.getByText(/a 是斜率/)).toBeVisible();
    const input = page.getByRole("textbox", { name: "输入消息" });
    await input.fill("确认第2页属于本节");
    await page.getByRole("button", { name: "发送消息" }).click();
    await expect.poll(() => sent?.content).toBe("确认第2页属于本节");
    await expect(input).toHaveValue("确认第2页属于本节");
    await page.reload();
    await expect(page.getByText(/模型知识补充：可以/)).toBeVisible();
    await expect(progress.getByText(/尚未更新本节范围/)).toBeVisible();
    await expect(progress.getByRole("button")).toHaveCount(0);
    await page.screenshot({ path: testInfo.outputPath(`tutoring-${width}.png`), fullPage: true });
  });
}
