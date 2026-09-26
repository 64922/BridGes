import { expect, test } from "@playwright/test";

// 界面使用受控 API 替身；判定、持久化与恢复另由聊天 API 集成测试验证。
const NOW = "2026-09-26T00:00:00Z";
for (const [width, height] of [[1280, 720], [1440, 900], [1920, 1080]]) {
  test(`逐题复盘与暂停恢复操作 ${width}×${height}`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height });
    await page.context().addCookies([{ name: "bridges_session", value: "review-test", url: testInfo.project.use.baseURL! }]);
    const account = { id: "study-owner", username: "学习测试", qq_email: "12345@qq.com",
      avatar_choice: "initials", created_at: NOW, updated_at: NOW };
    const conversation = {
      conversation_id: "review-test", title: "线性函数", mode: "study", mode_locked: true,
      pinned: false, created_at: NOW, updated_at: NOW, mode_events: [],
      messages: [{ message_id: "a1", conversation_id: "review-test", role: "assistant",
        attempt_number: 1, status: "done", created_at: NOW, updated_at: NOW,
        content: "预习时带着问题阅读，暂不需要作答。" }],
      study: { subsection_id: "review-test", stage: "tutoring", pages: [],
        review: null as null | { complete: boolean } },
    };
    let sent: Record<string, unknown> | undefined;
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      let body: unknown = {};
      if (path === "/api/auth/session") body = { account, session: {}, subject: {} };
      else if (path === "/api/auth/device/accounts") body = { accounts: [], current_account: account };
      else if (path === "/api/chat/conversations") body = { conversations: [conversation] };
      else if (path === "/api/chat/conversations/review-test") body = conversation;
      else if (path === "/api/chat/attachment-drafts") body = [];
      else if (path.endsWith("/messages") && route.request().method() === "POST") {
        sent = route.request().postDataJSON();
        await route.fulfill({ status: 503, json: { detail: { message: "发送暂时失败，请重试。" } } });
        return;
      }
      await route.fulfill({ json: body });
    });
    await page.goto("/chat/review-test");
    const progress = page.getByRole("region", { name: "学习阶段" });
    await progress.getByRole("button", { name: "开始复盘" }).click();
    await expect.poll(() => sent?.content).toBe("开始复盘");
    await expect(progress.getByRole("button", { name: "开始复盘" })).toBeEnabled();
    conversation.study.stage = "review";
    conversation.study.review = { complete: false };
    conversation.messages[0].content = "回答不完整。\n\n正确答案：a 是斜率，b 是纵截距。\n\nx 每增加 1，y 增加 a。\n\n复盘第2题：b 如何影响图像？";
    await page.reload();
    await expect(progress.getByText("复盘", { exact: true })).toHaveAttribute("aria-current", "step");
    await expect(page.getByText(/正确答案：a 是斜率/)).toBeVisible();
    await expect(page.getByText(/复盘第2题：/)).toHaveCount(1);
    await expect(page.getByText(/复盘第3题：/)).toHaveCount(0);
    await page.getByRole("textbox", { name: "输入消息" }).fill("不知道");
    await page.getByRole("button", { name: "发送消息" }).click();
    await expect.poll(() => sent?.content).toBe("不知道");
    await expect(page.getByRole("textbox", { name: "输入消息" })).toHaveValue("不知道");
    await progress.getByRole("button", { name: "暂停复盘回辅导" }).click();
    await expect.poll(() => sent?.content).toBe("暂停复盘回辅导");
    await page.screenshot({ path: testInfo.outputPath(`review-${width}.png`), fullPage: true });
    conversation.study.stage = "tutoring";
    await page.reload();
    await progress.getByRole("button", { name: "继续复盘" }).click();
    await expect.poll(() => sent?.content).toBe("继续复盘");
    conversation.study.review.complete = true;
    await page.reload();
    await expect(progress.getByText(/本节复盘已结束/)).toBeVisible();
    await expect(progress.getByRole("button")).toHaveCount(0);
  });
}
