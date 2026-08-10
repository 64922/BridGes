import { expect, test } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

test("上下文说明随自然语言回答展示，不提供画像手动开关", async ({ page }) => {
  const conversationId = "conv-issue27";
  const credentials = uniqueCredentials("issue27");
  await signUp(page, credentials.username, credentials.qqEmail, "Passw0rd123!");
  await page.route("**/api/chat/conversations/" + conversationId, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        conversation_id: conversationId,
        title: "上下文说明",
        mode: "companion",
        messages: [
          {
            message_id: "u-1",
            conversation_id: conversationId,
            role: "user",
            attempt_number: 1,
            status: "done",
            content: "请用简洁方式解释熵增。",
            error_code: null,
            error_message: null,
            duration_ms: null,
            model_id: null,
            run_lock_id: null,
            created_at: "2026-08-10T00:00:00Z",
            updated_at: "2026-08-10T00:00:00Z",
          },
          {
            message_id: "a-1",
            conversation_id: conversationId,
            role: "assistant",
            attempt_number: 1,
            status: "done",
            content: "熵增表示孤立系统的熵不会自发减少。",
            context_note: {
              state: "ready",
              profile_enabled: true,
              mode: "companion",
              profile_items: [],
              material_categories: [],
              excluded_count: 0,
              note: "本轮使用了与任务相关的最小上下文。",
            },
            error_code: null,
            error_message: null,
            duration_ms: 1200,
            model_id: "mock-model",
            run_lock_id: null,
            created_at: "2026-08-10T00:00:00Z",
            updated_at: "2026-08-10T00:00:00Z",
          },
        ],
        mode_events: [],
      }),
    });
  });
  await page.goto("/chat/" + conversationId);

  await expect(page.getByTestId("context-note-card")).toContainText("本次上下文说明");
  await expect(page.getByTestId("profile-usage")).toHaveCount(0);
  await expect(page.getByTestId("composer").getByRole("button", { name: /更多功能/ })).toHaveCount(0);
});
