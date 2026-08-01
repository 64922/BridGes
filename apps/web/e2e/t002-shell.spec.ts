import { expect, test } from "@playwright/test";

import { signUp } from "./helpers/auth";
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

  test("仅通过键盘即可从公共入口进入账户主壳并触发路由公告", async ({ page }) => {
    const email = `t002-keyboard-${Date.now()}@example.com`;
    await page.goto("/register");
    await page.getByLabel("邮箱").fill(email);
    await page.getByLabel("密码", { exact: true }).fill("correct-horse-12");
    await page.getByLabel("确认密码").fill("correct-horse-12");
    await page.getByLabel("我已阅读并同意服务条款和隐私政策").check();
    await page.getByRole("button", { name: "注册" }).press("Enter");
    await page.waitForURL("/account");
    await expect(page.getByRole("heading", { name: /欢迎回来/ })).toBeVisible();
    await expect(page.getByTestId("route-announcer")).toContainText("/account");
  });

  test("账户主壳高亮当前页并在主导航中提供项目入口", async ({ page }) => {
    const email = `t002-nav-${Date.now()}@example.com`;
    await signUp(page, email, "correct-horse-12");

    const companionLink = page.getByRole("link", { name: "全局科学伙伴" });
    await expect(companionLink).toHaveAttribute("aria-current", "page");
    await page.getByRole("link", { name: "科学项目空间" }).click();
    await page.waitForURL("/account/projects");
    await expect(page.getByRole("heading", { name: "科学项目空间" })).toBeVisible();
  });

  test("项目主壳显示项目头部、任务舞台、工作台标签和上下文检查器", async ({ page }) => {
    const email = `t002-project-${Date.now()}@example.com`;
    await signUp(page, email, "correct-horse-12");

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
    const email = `t002-tabs-${Date.now()}@example.com`;
    await signUp(page, email, "correct-horse-12");

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
    const email = `t002-motion-${Date.now()}@example.com`;
    await signUp(page, email, "correct-horse-12");

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
    const email = `t002-zoom-${Date.now()}@example.com`;
    await signUp(page, email, "correct-horse-12");

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
