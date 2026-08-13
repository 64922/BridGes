import { expect, test } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

test("上下文说明明确披露已授权用户背景，不提供画像手动开关", async ({ page }) => {
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
              used_at: "2026-08-10T00:00:00Z",
              profile_item_count: 2,
              material_categories: ["知识库材料"],
              note: "本轮回答参考了 2 条你已授权的用户背景信息（兴趣偏好、知识状态），仅用于当前任务；只保留与当前任务相关的少量内容。",
            },
            web_search: {
              status: "error",
              trigger_reason: "你明确要求联网核实",
              query_summary: "熵增",
              results: [],
              searched_at: "2026-08-10T00:00:00Z",
              error_code: "web_search_timeout",
              error_message: "本轮未联网核实。",
              can_retry: false,
              can_cancel: false,
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
  await expect(page.getByTestId("context-note-card")).toContainText("你已授权的用户背景信息");
  await expect(page.getByTestId("context-note-card")).toHaveAttribute(
    "aria-label",
    "本次上下文说明（已授权用户背景使用情况）",
  );
  await page.getByTestId("context-note-card").locator('[role="button"]').click();
  await expect(page.getByTestId("context-note-card")).toContainText("仅用于当前任务");
  await expect(page.getByTestId("context-note-card")).toContainText("知识库材料");
  await expect(page.getByTestId("web-search-card-error")).toContainText("本轮未联网核实");
  await expect(page.getByTestId("profile-usage")).toHaveCount(0);
  await expect(page.getByTestId("composer").getByRole("button", { name: /更多功能/ })).toHaveCount(0);
});
