import { expect, test } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

async function openNewChat(page: import("@playwright/test").Page): Promise<void> {
  const credentials = uniqueCredentials("issue13");
  await signUp(page, credentials.username, credentials.qqEmail, "Passw0rd123!");
  await expect(page.getByRole("heading", { name: "有什么可以帮你的？" })).toBeVisible();
}

test.describe("Issue 13 — 新聊天输入区与自然语言入口", () => {
  test("输入区只保留消息、听写、发送/停止和必要状态", async ({ page }) => {
    await openNewChat(page);

    const composer = page.getByTestId("composer");
    await expect(composer.getByLabel("输入消息")).toBeVisible();
    await expect(composer.getByRole("button", { name: "开始听写" })).toBeVisible();
    await expect(composer.getByRole("button", { name: "发送消息" })).toBeVisible();
    await expect(composer.getByRole("button")).toHaveCount(2);
    await expect(composer.getByRole("button", { name: /更多功能/ })).toHaveCount(0);
    await expect(page.locator('input[type="file"]')).toHaveCount(0);
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(page.getByTestId("composer-source-layers")).toHaveCount(0);
  });

  test("建议卡以自然语言预填并保持输入焦点", async ({ page }) => {
    await openNewChat(page);

    const input = page.getByTestId("composer").getByLabel("输入消息");
    const cards = page.getByTestId("suggestion-cards");
    for (const [label, prefix] of [
      ["论文搜索", "论文搜索："],
      ["文章人味化", "文章人味化："],
      ["生涯规划助手", "生涯规划助手："],
    ]) {
      await cards.getByRole("button", { name: label }).click();
      await expect(input).toHaveValue(prefix);
      await expect(input).toBeFocused();
      await input.fill("");
    }
  });
});
