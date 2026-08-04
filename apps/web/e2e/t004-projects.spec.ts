import { expect, test } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";
import { createProject } from "./helpers/projects";

/**
 * T004 — 创建带对象归属的科学项目空间（legacy 工作台 /projects/{id}）。
 *
 * Issue 19 后 /account/projects 改为「学习项目」文件夹列表，旧的页面内
 * 创建表单退场；科学项目空间仍由 POST /api/projects 提供，因此用例统一
 * 经 API 创建（见 helpers/projects.ts）再断言工作台投影。
 */
test.describe("T004 — 创建带对象归属的科学项目空间", () => {
  test("认证用户创建项目并进入项目工作台", async ({ page }) => {
    const creds = uniqueCredentials("t004-create");
    await signUp(page, creds.username, creds.qqEmail, "correct-horse-12");

    const projectName = `测试项目 ${Date.now()}`;
    const projectId = await createProject(page, projectName);

    await expect(page).toHaveURL(`/projects/${projectId}`);
    await expect(page.getByRole("heading", { name: projectName })).toBeVisible();
    await expect(page.getByText("对象域：个人保险库")).toBeVisible();
    await expect(page.getByText("角色：所有者")).toBeVisible();
  });

  test("深链刷新后恢复同一项目、对象域与角色投影", async ({ page }) => {
    const creds = uniqueCredentials("t004-deep");
    await signUp(page, creds.username, creds.qqEmail, "correct-horse-12");

    const projectName = `深链项目 ${Date.now()}`;
    await createProject(page, projectName);
    const deepUrl = page.url();

    await page.reload();
    await page.waitForLoadState("networkidle");
    await expect(page.getByRole("heading", { name: projectName })).toBeVisible();
    await expect(page.getByText("对象域：个人保险库")).toBeVisible();
    await expect(page.getByText("角色：所有者")).toBeVisible();
    expect(page.url()).toBe(deepUrl);
  });

  test("浏览器前进后退保持合法项目状态", async ({ page }) => {
    const creds = uniqueCredentials("t004-history");
    await signUp(page, creds.username, creds.qqEmail, "correct-horse-12");

    const projectName = `历史项目 ${Date.now()}`;
    const projectId = await createProject(page, projectName);

    const learningTab = page.getByTestId("main-content").getByRole("link", { name: "学习实验室" });
    await learningTab.click();
    await page.waitForURL(`/projects/${projectId}/learning`);

    await page.goBack();
    await page.waitForURL(`/projects/${projectId}`);
    await expect(page.getByRole("heading", { name: projectName })).toBeVisible();

    await page.goForward();
    await page.waitForURL(`/projects/${projectId}/learning`);
  });

  test("账户首页显示最近创建的项目", async ({ page }) => {
    const creds = uniqueCredentials("t004-dashboard");
    await signUp(page, creds.username, creds.qqEmail, "correct-horse-12");

    const projectName = `仪表项目 ${Date.now()}`;
    await createProject(page, projectName);

    await page.goto("/account");
    await expect(page.getByRole("heading", { name: "学习项目" })).toBeVisible();
    await expect(page.getByRole("link", { name: `打开项目 ${projectName}` })).toBeVisible();
  });

  test("其他账户无法通过深链访问项目", async ({ page, browser }) => {
    const alice = uniqueCredentials("t004-alice");
    const bob = uniqueCredentials("t004-bob");

    await signUp(page, alice.username, alice.qqEmail, "correct-horse-12");
    const projectName = `Alice 私有项目 ${Date.now()}`;
    await createProject(page, projectName);
    const deepUrl = page.url();

    const bobContext = await browser.newContext();
    const bobPage = await bobContext.newPage();
    await bobPage.goto("/register");
    await bobPage.getByLabel("用户名").fill(bob.username);
    await bobPage.getByLabel("QQ 邮箱").fill(bob.qqEmail);
    await bobPage.getByLabel("密码").fill("correct-horse-12");
    await bobPage.getByRole("button", { name: "注册" }).click();
    await bobPage.waitForURL("/");

    await bobPage.goto(deepUrl);
    await bobPage.waitForLoadState("networkidle");
    // Bob must not see Alice's project header; he should land on an authorized state.
    await expect(bobPage.getByRole("heading", { name: projectName })).not.toBeVisible();

    await bobContext.close();
  });

  test("未选择项目时项目工作台不可访问", async ({ page }) => {
    const creds = uniqueCredentials("t004-no-project");
    await signUp(page, creds.username, creds.qqEmail, "correct-horse-12");

    // Direct access to a non-existent project deep link should fail gracefully.
    await page.goto("/projects/not-a-real-project");
    await page.waitForLoadState("networkidle");
    await expect(page.getByRole("heading", { name: "无法加载项目" })).toBeVisible();
  });
});
