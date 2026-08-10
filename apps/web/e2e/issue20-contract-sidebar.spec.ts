import { expect, test } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

const PASSWORD = "correct-horse-12";

test.describe("Issue 20 — 收敛侧栏与退役独立页面", () => {
  test("登录后只展示当前产品入口，最近对话不暴露项目归属", async ({ page }) => {
    const credentials = uniqueCredentials("i20-sidebar");
    await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);

    const sidebar = page.getByTestId("app-sidebar");
    await expect(sidebar).toBeVisible();
    const text = await sidebar.innerText();
    const labels = ["搜索", "收起侧边栏", "新聊天", "知识库", "用户画像", "最近对话"];
    let previous = -1;
    for (const label of labels) {
      const index = text.indexOf(label);
      expect(index, `侧栏应按固定顺序包含「${label}」`).toBeGreaterThan(previous);
      previous = index;
    }
    for (const retired of ["学习项目", "任务安排", "插件", "MCP"]) {
      await expect(sidebar).not.toContainText(retired);
    }
    await expect(sidebar.getByRole("link", { name: "知识库" })).toHaveAttribute(
      "href",
      "/knowledge-base"
    );
    await expect(sidebar.getByRole("link", { name: "用户画像" })).toHaveAttribute(
      "href",
      "/account/profile"
    );

    const newChat = sidebar.getByRole("link", { name: "新聊天", exact: true });
    await expect(newChat).toBeVisible();
    await expect
      .poll(() => newChat.evaluate((element) => getComputedStyle(element).backgroundColor))
      .not.toBe("rgb(255, 255, 255)");
  });

  test("首页、聊天、搜索、知识库和画像页不请求退役模块接口", async ({ page }) => {
    const credentials = uniqueCredentials("i20-zero-requests");
    await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);
    const retiredRequests: string[] = [];
    page.on("request", (request) => {
      if (/\/api\/(learning-projects|plugins|mcp|reminders)(?:\/|$)/.test(request.url())) {
        retiredRequests.push(request.url());
      }
    });

    for (const path of ["/", "/search", "/knowledge-base", "/account/profile"]) {
      await page.goto(path);
      await expect(page.getByTestId("app-sidebar")).toBeVisible();
    }
    expect(retiredRequests).toEqual([]);
  });

  test("旧独立页面只发生一次兼容重定向并到达新聊天", async ({ page }) => {
    const credentials = uniqueCredentials("i20-redirect");
    await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);

    for (const path of [
      "/account/projects",
      "/account/projects/project-1",
      "/tasks",
      "/plugins",
      "/mcp",
    ]) {
      await page.goto(path);
      await expect(page).toHaveURL(/\/$/);
      await expect(page.getByRole("heading", { name: "有什么可以帮你的？" })).toBeVisible();
    }
  });
});
