import { expect, test } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

test("旧任务深链展示退役说明并返回学习聊天", async ({ page }) => {
  const credentials = uniqueCredentials("retired-reminders");
  await signUp(page, credentials.username, credentials.qqEmail, "correct-horse-33");

  await page.goto("/tasks");
  await expect(page.getByRole("heading", { name: "任务安排已退役" })).toBeVisible();
  await expect(page.getByText("学习任务、复习计划和邮件提醒已停止使用。")).toBeVisible();
  await expect(
    page.getByText("请在学习模式聊天中继续，学习进度会保留在连续教学回合里。"),
  ).toBeVisible();
  await page.getByRole("link", { name: "返回聊天学习" }).click();
  await expect(page).toHaveURL(/\/$/);
});

test("旧模板任务深链同样展示退役说明", async ({ page }) => {
  await page.goto("/templates/list?section=tasks");
  await expect(page.getByRole("heading", { name: "任务安排已退役" })).toBeVisible();
  await expect(page.getByRole("link", { name: "返回聊天学习" })).toBeVisible();
});
