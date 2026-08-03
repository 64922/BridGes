import { expect, test } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";
import { createProject } from "./helpers/projects";

test.describe("T002 — 项目主壳、电脑端布局与无障碍基线", () => {
  test("公共入口显示健康状态、跳转链接和地标", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    await expect(page.getByRole("heading", { name: "BridGes" }).first()).toBeVisible();
    await expect(page.getByRole("link", { name: "登录" })).toBeVisible();
    await expect(page.getByRole("link", { name: "注册" })).toBeVisible();
    await expect(page.getByTestId("main-content")).toBeVisible();
    await expect(page.getByRole("region", { name: /系统健康状态/ })).toBeVisible();
  });

  test("跳转主内容链接在 Tab 后可见并可将焦点移到 main", async ({ page }) => {
    await page.goto("/");
    await page.keyboard.press("Tab");
    const skipLink = page.getByTestId("skip-link");
    await expect(skipLink).toBeVisible();
    await skipLink.press("Enter");
    await expect(page.getByTestId("main-content")).toBeFocused();
  });

  test("仅通过键盘即可注册并进入账户主壳并触发路由公告", async ({ page }) => {
    const id = Date.now();
    await page.goto("/register");
    await page.getByLabel("用户名").fill(`t002-keyboard-${id}`);
    await page.getByLabel("QQ 邮箱").fill(`2${id}@qq.com`);
    await page.getByLabel("密码").fill("correct-horse-12");
    await page.getByRole("button", { name: "注册" }).press("Enter");
    await page.waitForURL("/");
    // 注册成功落在新聊天首页；经全局侧栏进入学习项目（客户端导航触发
    // 路由公告），账户主壳页面保持直链可达。
    const sidebar = page.getByTestId("app-sidebar");
    await sidebar.getByRole("link", { name: "学习项目" }).click();
    await page.waitForURL("/account/projects");
    await expect(page.getByTestId("route-announcer")).toContainText("/account/projects");
    await page.goto("/account");
    await expect(page.getByRole("heading", { name: /欢迎回来/ })).toBeVisible();
  });

  test("全局侧栏高亮当前页并提供学习项目入口", async ({ page }) => {
    const creds = uniqueCredentials("t002-nav");
    await signUp(page, creds.username, creds.qqEmail, "correct-horse-12");

    // Issue 12：普通用户导航为固定顺序的全局侧栏。
    const sidebar = page.getByTestId("app-sidebar");
    await expect(sidebar).toBeVisible();
    await sidebar.getByRole("link", { name: "学习项目" }).click();
    await page.waitForURL("/account/projects");
    await expect(page.getByRole("heading", { name: "学习项目" })).toBeVisible();
    await expect(sidebar.getByRole("link", { name: "学习项目" })).toHaveAttribute(
      "aria-current",
      "page"
    );
  });

  test("项目主壳显示项目头部、任务舞台、工作台标签和上下文检查器", async ({ page }) => {
    const creds = uniqueCredentials("t002-project");
    await signUp(page, creds.username, creds.qqEmail, "correct-horse-12");

    const projectName = `T002 示例项目 ${Date.now()}`;
    await createProject(page, projectName);
    await page.waitForLoadState("networkidle");
    await expect(page.getByRole("heading", { name: projectName })).toBeVisible();
    await expect(page.getByText("任务舞台")).toBeVisible();
    await expect(page.getByRole("navigation", { name: "项目工作台" })).toBeVisible();
    await expect(page.getByTestId("main-content").getByRole("link", { name: "学习实验室" })).toBeVisible();
    await expect(page.getByRole("complementary", { name: "上下文检查器" })).toBeVisible();
    // Status badges must include text (not rely on color alone).
    await expect(page.getByTestId("status-badge").filter({ hasText: "运行中" })).toBeVisible();
    await expect(page.getByTestId("status-badge").filter({ hasText: "已绑定证据" })).toBeVisible();
  });

  test("工作台标签切换路由并刷新当前页高亮", async ({ page }) => {
    const creds = uniqueCredentials("t002-tabs");
    await signUp(page, creds.username, creds.qqEmail, "correct-horse-12");

    const projectName = `T002 标签项目 ${Date.now()}`;
    const projectId = await createProject(page, projectName);
    const learningTab = page.getByTestId("main-content").getByRole("link", { name: "学习实验室" });
    await learningTab.click();
    await page.waitForURL(`/projects/${projectId}/learning`);
    await expect(page.getByRole("heading", { name: "学习实验室" })).toBeVisible();
    await expect(learningTab).toHaveAttribute("aria-current", "page");
  });

  test("减少动画模式下 CSS 过渡时长归零", async ({ page, browserName }) => {
    test.skip(browserName !== "chromium", "Reduced-motion emulation is verified on Chromium.");
    const creds = uniqueCredentials("t002-motion");
    await signUp(page, creds.username, creds.qqEmail, "correct-horse-12");

    const projectName = `T002 动画项目 ${Date.now()}`;
    await createProject(page, projectName);
    await page.emulateMedia({ reducedMotion: "reduce" });
    const transition = await page.evaluate(() => {
      const button = document.querySelector('[data-testid="sc-button"]') as HTMLElement | null;
      if (!button) return null;
      return window.getComputedStyle(button).transitionDuration;
    });
    expect(transition).toBeTruthy();
    // prefers-reduced-motion sets durations to 0.001ms to preserve transitions
    // while effectively disabling motion.
    expect(Number.parseFloat(transition!)).toBeLessThanOrEqual(0.001);
  });

  test("200% 文本缩放下关键操作仍然可见且无横向溢出", async ({ page }) => {
    const creds = uniqueCredentials("t002-zoom");
    await signUp(page, creds.username, creds.qqEmail, "correct-horse-12");

    const projectName = `T002 缩放项目 ${Date.now()}`;
    await createProject(page, projectName);
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.waitForLoadState("networkidle");
    await page.evaluate(() => {
      document.documentElement.style.fontSize = "32px";
    });
    const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    const viewportWidth = await page.evaluate(() => window.innerWidth);
    expect(scrollWidth).toBeLessThanOrEqual(viewportWidth);
    await expect(page.getByRole("button", { name: "取消" })).toBeVisible();
    await expect(page.getByRole("button", { name: "重试" })).toBeVisible();
    await expect(page.getByTestId("main-content").getByRole("link", { name: "学习实验室" })).toBeVisible();
  });

});
