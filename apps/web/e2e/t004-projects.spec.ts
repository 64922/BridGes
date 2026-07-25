import { expect, test } from "@playwright/test";

import { signUp } from "./helpers/auth";

test.describe("T004 — 创建带对象归属的科学项目空间", () => {
  test("认证用户创建项目并在列表中选择项目", async ({ page }) => {
    const email = `t004-create-${Date.now()}@example.com`;
    await signUp(page, email, "correct-horse-12");

    await page.goto("/account/projects");
    await expect(page.getByRole("heading", { name: "科学项目空间" })).toBeVisible();

    const projectName = `测试项目 ${Date.now()}`;
    await page.getByLabel("项目名称").fill(projectName);
    await page.getByRole("button", { name: "创建项目" }).click();

    await page.waitForURL(/\/projects\/[A-Za-z0-9_-]+/);
    await expect(page.getByRole("heading", { name: projectName })).toBeVisible();
    await expect(page.getByText("对象域：个人保险库")).toBeVisible();
    await expect(page.getByText("角色：所有者")).toBeVisible();
  });

  test("深链刷新后恢复同一项目、对象域与角色投影", async ({ page }) => {
    const email = `t004-deep-${Date.now()}@example.com`;
    await signUp(page, email, "correct-horse-12");

    await page.goto("/account/projects");
    const projectName = `深链项目 ${Date.now()}`;
    await page.getByLabel("项目名称").fill(projectName);
    await page.getByRole("button", { name: "创建项目" }).click();
    await page.waitForURL(/\/projects\/[A-Za-z0-9_-]+/);

    const deepUrl = page.url();

    await page.reload();
    await page.waitForLoadState("networkidle");
    await expect(page.getByRole("heading", { name: projectName })).toBeVisible();
    await expect(page.getByText("对象域：个人保险库")).toBeVisible();
    await expect(page.getByText("角色：所有者")).toBeVisible();
    expect(page.url()).toBe(deepUrl);
  });

  test("浏览器前进后退保持合法项目状态", async ({ page }) => {
    const email = `t004-history-${Date.now()}@example.com`;
    await signUp(page, email, "correct-horse-12");

    await page.goto("/account/projects");
    const projectName = `历史项目 ${Date.now()}`;
    await page.getByLabel("项目名称").fill(projectName);
    await page.getByRole("button", { name: "创建项目" }).click();
    await page.waitForURL(/\/projects\/[A-Za-z0-9_-]+/);

    const learningTab = page.getByTestId("main-content").getByRole("link", { name: "学习实验室" });
    await learningTab.click();
    await page.waitForURL(/\/projects\/[A-Za-z0-9_-]+\/learning/);

    await page.goBack();
    await page.waitForURL(/\/projects\/[A-Za-z0-9_-]+$/);
    await expect(page.getByRole("heading", { name: projectName })).toBeVisible();

    await page.goForward();
    await page.waitForURL(/\/projects\/[A-Za-z0-9_-]+\/learning/);
  });

  test("账户首页显示最近创建的项目", async ({ page }) => {
    const email = `t004-dashboard-${Date.now()}@example.com`;
    await signUp(page, email, "correct-horse-12");

    await page.goto("/account/projects");
    const projectName = `仪表项目 ${Date.now()}`;
    await page.getByLabel("项目名称").fill(projectName);
    await page.getByRole("button", { name: "创建项目" }).click();
    await page.waitForURL(/\/projects\/[A-Za-z0-9_-]+/);

    await page.goto("/account");
    await expect(page.getByRole("heading", { name: "科学项目空间" })).toBeVisible();
    await expect(page.getByRole("link", { name: `打开项目 ${projectName}` })).toBeVisible();
  });

  test("其他账户无法通过深链访问项目", async ({ page, browser }) => {
    const aliceEmail = `t004-alice-${Date.now()}@example.com`;
    const bobEmail = `t004-bob-${Date.now()}@example.com`;

    await signUp(page, aliceEmail, "correct-horse-12");
    await page.goto("/account/projects");
    const projectName = `Alice 私有项目 ${Date.now()}`;
    await page.getByLabel("项目名称").fill(projectName);
    await page.getByRole("button", { name: "创建项目" }).click();
    await page.waitForURL(/\/projects\/[A-Za-z0-9_-]+/);
    const deepUrl = page.url();

    const bobContext = await browser.newContext();
    const bobPage = await bobContext.newPage();
    await bobPage.goto("/register");
    await bobPage.getByLabel("邮箱").fill(bobEmail);
    await bobPage.getByLabel("密码", { exact: true }).fill("correct-horse-12");
    await bobPage.getByLabel("确认密码").fill("correct-horse-12");
    await bobPage.getByLabel("我已阅读并同意服务条款和隐私政策").check();
    await bobPage.getByRole("button", { name: "注册" }).click();
    await bobPage.waitForURL("/account");

    await bobPage.goto(deepUrl);
    await bobPage.waitForLoadState("networkidle");
    // Bob must not see Alice's project header; he should land on an authorized state.
    await expect(bobPage.getByRole("heading", { name: projectName })).not.toBeVisible();

    await bobContext.close();
  });

  test("未选择项目时项目工作台不可访问", async ({ page }) => {
    const email = `t004-no-project-${Date.now()}@example.com`;
    await signUp(page, email, "correct-horse-12");

    // Direct access to a non-existent project deep link should fail gracefully.
    await page.goto("/projects/not-a-real-project");
    await page.waitForLoadState("networkidle");
    await expect(page.getByRole("heading", { name: "无法加载项目" })).toBeVisible();
  });
});
