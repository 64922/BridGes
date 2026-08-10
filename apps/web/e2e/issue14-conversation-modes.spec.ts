import { expect, test, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

const CONVERSATION_ID = "conv-issue14";

async function openConversation(page: Page, mode: "companion" | "study"): Promise<void> {
  const credentials = uniqueCredentials("issue14");
  await signUp(page, credentials.username, credentials.qqEmail, "Passw0rd123!");
  await page.route("**/api/chat/conversations/" + CONVERSATION_ID, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        conversation_id: CONVERSATION_ID,
        title: "固定模式会话",
        mode,
        messages: [],
        mode_events: [],
      }),
    });
  });
  await page.goto("/chat/" + CONVERSATION_ID);
  await expect(page.getByTestId("conversation-mode")).toBeVisible();
}

test.describe("Issue 14 — 已有会话模式只读", () => {
  for (const mode of ["companion", "study"] as const) {
    test(mode + " 会话显示只读模式标签，不提供切换动作", async ({ page }) => {
      await openConversation(page, mode);
      const label = page.getByTestId("conversation-mode");
      await expect(label).toContainText(mode === "study" ? "学习模式" : "日常陪伴");
      await expect(label).not.toHaveAttribute("role", "button");
      await expect(page.getByTestId("mode-toggle")).toHaveCount(0);
      await expect(page.getByTestId("mode-event")).toHaveCount(0);
      await expect(page.getByTestId("composer").getByRole("button", { name: /更多功能/ })).toHaveCount(0);
    });
  }
});
