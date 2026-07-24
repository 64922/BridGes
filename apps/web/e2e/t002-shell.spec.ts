import { expect, test } from "@playwright/test";

test.describe("T002 — 项目主壳、设计系统、响应式与无障碍基线", () => {
  test("公共入口显示健康状态、跳转链接和地标", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    await expect(page.getByRole("heading", { name: "Science Companion" }).first()).toBeVisible();
    await expect(page.getByRole("link", { name: "登录" })).toBeVisible();
    await expect(page.getByRole("link", { name: "注册" })).toBeVisible();
    await expect(page.getByRole("link", { name: "进入账户主壳（演示）" })).toBeVisible();
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
    await page.goto("/");
    // Tab past skip link to reach the entry links.
    await page.keyboard.press("Tab");
    await page.keyboard.press("Tab");
    await page.keyboard.press("Tab");
    await page.keyboard.press("Tab");
    await page.getByRole("link", { name: "进入账户主壳（演示）" }).press("Enter");
    await page.waitForURL("/account");
    await expect(page.getByRole("heading", { name: "欢迎回来，演示用户" })).toBeVisible();
    await expect(page.getByTestId("route-announcer")).toContainText("/account");
  });

  test("账户主壳高亮当前页并在主导航中提供项目入口", async ({ page, isMobile }) => {
    test.skip(isMobile, "Mobile navigation is inside a drawer; covered by dedicated mobile tests.");
    await page.goto("/account");
    const companionLink = page.getByRole("link", { name: "全局科学伙伴" });
    await expect(companionLink).toHaveAttribute("aria-current", "page");
    await page.getByRole("link", { name: "科学项目空间" }).click();
    await page.waitForURL("/account/projects");
    await expect(page.getByRole("heading", { name: "科学项目空间" })).toBeVisible();
  });

  test("项目主壳显示项目头部、任务舞台、工作台标签和上下文检查器", async ({ page, isMobile }) => {
    test.skip(isMobile, "Mobile inspector is a drawer; covered by dedicated mobile tests.");
    await page.goto("/projects/demo-id");
    await page.waitForLoadState("networkidle");
    await expect(page.getByRole("heading", { name: /示例项目/ })).toBeVisible();
    await expect(page.getByText("任务舞台")).toBeVisible();
    await expect(page.getByRole("navigation", { name: "项目工作台" })).toBeVisible();
    await expect(page.getByTestId("main-content").getByRole("link", { name: "学习实验室" })).toBeVisible();
    await expect(page.getByRole("complementary", { name: "上下文检查器" })).toBeVisible();
    // Status badges must include text (not rely on color alone).
    await expect(page.getByTestId("status-badge").filter({ hasText: "运行中" })).toBeVisible();
    await expect(page.getByTestId("status-badge").filter({ hasText: "已绑定证据" })).toBeVisible();
  });

  test("工作台标签切换路由并刷新当前页高亮", async ({ page }) => {
    await page.goto("/projects/demo-id");
    const learningTab = page.getByTestId("main-content").getByRole("link", { name: "学习实验室" });
    await learningTab.click();
    await page.waitForURL("/projects/demo-id/learning");
    await expect(page.getByRole("heading", { name: "学习实验室" })).toBeVisible();
    await expect(learningTab).toHaveAttribute("aria-current", "page");
  });

  test("移动端视口下单栏布局无横向溢出，且可打开主导航抽屉", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto("/projects/demo-id");
    await page.waitForLoadState("networkidle");

    const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    const viewportWidth = await page.evaluate(() => window.innerWidth);
    expect(scrollWidth).toBeLessThanOrEqual(viewportWidth);

    const menuButton = page.getByRole("button", { name: "打开主导航" });
    await expect(menuButton).toBeVisible();
    await menuButton.click();
    const navigation = page.getByRole("navigation", { name: "主导航" });
    await expect(navigation).toBeVisible();
    await page.getByRole("button", { name: "关闭导航" }).click();
    await expect(navigation).not.toBeVisible();
  });

  test("减少动画模式下 CSS 过渡时长归零", async ({ page, browserName }) => {
    test.skip(browserName !== "chromium", "Reduced-motion emulation is verified on Chromium.");
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/projects/demo-id");
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
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.goto("/projects/demo-id");
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

  test("移动端 200% 文本缩放仍可完成打开上下文检查器操作", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto("/projects/demo-id");
    await page.waitForLoadState("networkidle");
    await page.evaluate(() => {
      document.documentElement.style.fontSize = "32px";
    });
    const toggle = page.getByTestId("inspector-toggle");
    await expect(toggle).toBeVisible();
    await toggle.click();
    const inspector = page.getByRole("complementary", { name: "上下文检查器" });
    await expect(inspector).toBeVisible();
    await expect(inspector.getByText("画像与记忆切片")).toBeVisible();
    await toggle.click();
    await expect(inspector).not.toBeVisible();
  });
});
