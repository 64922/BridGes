/**
 * Issue 15：整体概览与开放主题浏览的正式桌面验收。
 *
 * 连真实 API（零 mock）：注册账户后用显式「记住」写入一条真实来源的长期
 * 信息，在桌面浏览器里核验概览按主题原样汇总该句；点击概览陈述定位到
 * 对应条目（高亮、展开依据）；行内修改保存后旧概览立即失效并重建出新
 * 内容；删除后概览如实回到空态。
 */

import { expect, test, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

const ORIGINAL = "我每晚只能抽出半小时";
const UPDATED = "我每晚只能抽出四十分钟";

async function remember(page: Page, content: string): Promise<void> {
  const response = await page.request.post("/api/chat/first-turn", {
    data: {
      content,
      idempotency_key: `e2e-issue15-${Date.now()}-${Math.random()
        .toString(36)
        .slice(2)}`,
    },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
}

test("概览汇总真实条目、点击定位并随编辑与删除立即更新", async ({ page }) => {
  const credentials = uniqueCredentials("issue15");
  await signUp(
    page,
    credentials.username,
    credentials.qqEmail,
    "correct-horse-issue15"
  );
  await remember(page, `记住${ORIGINAL}`);

  // 等长期信息真实落库后再打开页面，避免与后台异步提取竞争。
  await expect
    .poll(async () => {
      const response = await page.request.get("/api/profiles/items");
      const items = (await response.json()) as Array<{ text: string }>;
      return items.some((entry) => entry.text === ORIGINAL);
    })
    .toBe(true);

  await page.goto("/account/profile");
  const overview = page.getByTestId("overview-card");
  await expect(overview).toBeVisible();
  await expect(
    page.getByTestId("atomic-item").filter({ hasText: ORIGINAL })
  ).toBeVisible();

  // 概览陈述原样包含该句；点击段落定位到对应条目：高亮并展开依据。
  const statement = overview
    .getByTestId("overview-statement")
    .filter({ hasText: ORIGINAL });
  await expect(statement).toBeVisible({ timeout: 15_000 });
  await statement.getByTestId("overview-segment").click();

  const row = page.getByTestId("atomic-item").filter({ hasText: ORIGINAL });
  await expect(row).toHaveAttribute("data-located", "true");
  await expect(row.getByTestId("atomic-evidence")).toBeVisible();

  // 行内修改保存：旧概览立即失效，重建后显示新内容。
  await row.getByRole("button", { name: "修改" }).click();
  await page.getByLabel("长期信息内容").fill(UPDATED);
  await page.getByRole("button", { name: "保存" }).click();
  await expect(overview).toContainText(UPDATED, { timeout: 15_000 });
  await expect(overview).not.toContainText(ORIGINAL);
  await expect(
    page.getByTestId("atomic-item").filter({ hasText: UPDATED })
  ).toBeVisible();

  // 删除唯一一条信息：概览如实回到空态，不显示旧内容。
  page.on("dialog", (dialog) => void dialog.accept());
  await page
    .getByTestId("atomic-item")
    .filter({ hasText: UPDATED })
    .getByRole("button", { name: "删除" })
    .click();
  await expect(overview.getByTestId("overview-empty")).toBeVisible({
    timeout: 15_000,
  });
  await expect(overview).not.toContainText(UPDATED);
});
