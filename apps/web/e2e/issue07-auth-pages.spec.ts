import { expect, test } from "@playwright/test";

import { signOut, signUp, uniqueCredentials } from "./helpers/auth";

/**
 * Issue 07 — 交付用户名与 QQ 邮箱注册登录页面。
 *
 * 覆盖：页面元素完整性、QQ 邮箱与用户名校验、双标识登录、统一不泄露错误、
 * 密码显隐、键盘路径、重定向规则、Cookie 合同与桌面视口视觉回归。
 */

test.describe("Issue 07 — 注册页", () => {
  test("注册页完整呈现 Logo、字段、密码显隐、提交与登录入口", async ({ page }) => {
    await page.goto("/register");
    await expect(page.getByRole("img", { name: /BridGes/i }).first()).toBeVisible();
    await expect(page.getByRole("heading", { name: "注册" })).toBeVisible();
    await expect(page.getByLabel("用户名")).toBeVisible();
    await expect(page.getByLabel("QQ 邮箱")).toBeVisible();
    await expect(page.getByLabel("密码")).toBeVisible();
    await expect(page.getByRole("button", { name: "显示密码" })).toBeVisible();
    await expect(page.getByRole("button", { name: "注册" })).toBeVisible();
    await expect(page.getByRole("link", { name: "直接登录" })).toBeVisible();
  });

  test("注册页说明不发送验证码且邮件提醒需 SMTP 自发自收验证", async ({ page }) => {
    await page.goto("/register");
    await expect(page.getByText(/注册时不会发送验证码/)).toBeVisible();
    await expect(page.getByText(/自发自收验证后，才能启用邮件提醒/)).toBeVisible();
  });

  test("非法 QQ 邮箱与非法用户名给出字段级错误并移动焦点", async ({ page }) => {
    await page.goto("/register");
    await page.getByLabel("用户名").fill("name@qq.com");
    await page.getByLabel("QQ 邮箱").fill("abc123@qq.com");
    await page.getByLabel("密码").fill("correct-horse-12");
    await page.getByRole("button", { name: "注册" }).click();

    await expect(page.getByText("用户名需为 1-32 个字符，不能包含空格或 @ 符号。")).toBeVisible();
    // 焦点移动到第一个错误字段。
    await expect(page.getByLabel("用户名")).toBeFocused();

    await page.getByLabel("用户名").fill("合法用户");
    await page.getByRole("button", { name: "注册" }).click();
    await expect(page.getByText("QQ 邮箱应为纯数字 QQ 号加 @qq.com。")).toBeVisible();
    await expect(page.getByLabel("QQ 邮箱")).toBeFocused();
  });

  test("短密码在客户端被拦截", async ({ page }) => {
    await page.goto("/register");
    await page.getByLabel("用户名").fill("短密码用户");
    await page.getByLabel("QQ 邮箱").fill("123456@qq.com");
    await page.getByLabel("密码").fill("short");
    await page.getByRole("button", { name: "注册" }).click();
    await expect(page.getByText("密码长度至少为 12 位。")).toBeVisible();
  });

  test("用户名大小写不敏感唯一且冲突提示不泄露信息", async ({ page }) => {
    const creds = uniqueCredentials("i7-CaseUser");
    await signUp(page, creds.username, creds.qqEmail, "correct-horse-12");
    await signOut(page);

    // 用仅大小写不同的用户名与全新 QQ 邮箱再次注册，必须被拒绝。
    const variant = creds.username.toUpperCase() === creds.username
      ? creds.username.toLowerCase()
      : creds.username.toUpperCase();
    const other = uniqueCredentials("i7-other");
    await page.goto("/register");
    await page.getByLabel("用户名").fill(variant);
    await page.getByLabel("QQ 邮箱").fill(other.qqEmail);
    await page.getByLabel("密码").fill("correct-horse-12");
    await page.getByRole("button", { name: "注册" }).click();
    await expect(page.getByTestId("error-summary")).toContainText("无法完成注册");
  });

  test("密码显隐切换可用且状态暴露给辅助技术", async ({ page }) => {
    await page.goto("/register");
    const passwordInput = page.getByLabel("密码");
    const toggle = page.getByRole("button", { name: "显示密码" });

    await expect(passwordInput).toHaveAttribute("type", "password");
    await toggle.click();
    await expect(passwordInput).toHaveAttribute("type", "text");
    await expect(page.getByRole("button", { name: "隐藏密码" })).toHaveAttribute(
      "aria-pressed",
      "true"
    );
    await page.getByRole("button", { name: "隐藏密码" }).click();
    await expect(passwordInput).toHaveAttribute("type", "password");
  });

  test("仅用键盘即可完成注册全流程", async ({ page }) => {
    const creds = uniqueCredentials("i7-keyboard");
    await page.goto("/register");

    // Tab 依次经过用户名、QQ 邮箱、密码、显隐切换、提交按钮。
    const focused: string[] = [];
    for (let i = 0; i < 14; i += 1) {
      await page.keyboard.press("Tab");
      const id = await page.evaluate(() => {
        const el = document.activeElement as HTMLElement | null;
        return el?.id || el?.textContent?.trim() || "";
      });
      focused.push(id);
    }
    const fieldOrder = ["username", "qqEmail", "password"];
    const positions = fieldOrder.map((id) => focused.indexOf(id));
    expect(positions.every((pos) => pos >= 0)).toBeTruthy();
    expect(positions[0]).toBeLessThan(positions[1]);
    expect(positions[1]).toBeLessThan(positions[2]);

    // 键盘填充并提交。
    await page.getByLabel("用户名").focus();
    await page.keyboard.type(creds.username);
    await page.keyboard.press("Tab");
    await page.keyboard.type(creds.qqEmail);
    await page.keyboard.press("Tab");
    await page.keyboard.type("correct-horse-12");
    // 下一个 Tab 到达显隐按钮，用 Enter 切换一次再切回。
    await page.keyboard.press("Tab");
    await page.keyboard.press("Enter");
    await expect(page.getByLabel("密码")).toHaveAttribute("type", "text");
    await page.keyboard.press("Enter");
    // Tab 到提交按钮并回车提交。
    await page.keyboard.press("Tab");
    await page.keyboard.press("Enter");
    await page.waitForURL("/");
    await expect(page.getByText("新聊天")).toBeVisible();
  });

  test("注册页与登录页可以互相链接", async ({ page }) => {
    await page.goto("/register");
    await page.getByRole("link", { name: "直接登录" }).click();
    await page.waitForURL("/login");
    await expect(page.getByRole("heading", { name: "登录" })).toBeVisible();
    await page.getByRole("link", { name: "注册 BridGes" }).click();
    await page.waitForURL("/register");
    await expect(page.getByRole("heading", { name: "注册" })).toBeVisible();
  });
});

test.describe("Issue 07 — 登录页", () => {
  test("登录页完整呈现 Logo、单一标识字段、密码、显隐、提交与注册入口", async ({ page }) => {
    await page.goto("/login");
    await expect(page.getByRole("img", { name: /BridGes/i }).first()).toBeVisible();
    await expect(page.getByRole("heading", { name: "登录" })).toBeVisible();
    await expect(page.getByLabel("用户名或 QQ 邮箱")).toBeVisible();
    await expect(page.getByLabel("密码")).toBeVisible();
    await expect(page.getByRole("button", { name: "显示密码" })).toBeVisible();
    await expect(page.getByRole("button", { name: "登录" })).toBeVisible();
    await expect(page.getByRole("link", { name: "注册 BridGes" })).toBeVisible();
    // 登录页只有一个标识字段：不存在独立的 QQ 邮箱字段。
    await expect(page.getByLabel(/^QQ 邮箱/)).toHaveCount(0);
  });

  test("空表单提交给出字段级错误并移动焦点", async ({ page }) => {
    await page.goto("/login");
    await page.getByRole("button", { name: "登录" }).click();
    await expect(page.getByText("请输入用户名或 QQ 邮箱。")).toBeVisible();
    await expect(page.getByLabel("用户名或 QQ 邮箱")).toBeFocused();
  });

  test("退出后回到登录页给出明确提示", async ({ page }) => {
    const creds = uniqueCredentials("i7-logout");
    await signUp(page, creds.username, creds.qqEmail, "correct-horse-12");
    await signOut(page);
    await expect(page.getByText("你已安全退出，会话已撤销。")).toBeVisible();
  });

  test("服务异常与网络失败给出中文错误且表单可恢复", async ({ page }) => {
    // 模拟服务异常 500。
    await page.route("**/api/auth/login", (route) =>
      route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: { error: "server_error", message: "服务暂时不可用，请稍后重试。" } }),
      })
    );
    await page.goto("/login");
    await page.getByLabel("用户名或 QQ 邮箱").fill("someone");
    await page.getByLabel("密码").fill("correct-horse-12");
    await page.getByRole("button", { name: "登录" }).click();
    await expect(page.getByTestId("error-summary")).toContainText("服务暂时不可用");
    // 表单恢复可提交状态。
    await expect(page.getByRole("button", { name: "登录" })).toBeEnabled();

    // 模拟网络失败（请求被中止）。
    await page.unroute("**/api/auth/login");
    await page.route("**/api/auth/login", (route) => route.abort());
    await page.getByRole("button", { name: "登录" }).click();
    await expect(page.getByTestId("error-summary")).toContainText("网络异常，请检查连接后重试。");
    await expect(page.getByRole("button", { name: "登录" })).toBeEnabled();

    // 恢复网络后同一表单可以直接重试成功。
    const creds = uniqueCredentials("i7-retry");
    await page.unroute("**/api/auth/login");
    const apiBase = process.env.API_BASE_URL || "http://127.0.0.1:8000";
    const registerResponse = await page.request.post(`${apiBase}/auth/register`, {
      data: { username: creds.username, qq_email: creds.qqEmail, password: "correct-horse-12" },
    });
    expect(registerResponse.status()).toBe(201);
    await page.getByLabel("用户名或 QQ 邮箱").fill(creds.username);
    await page.getByRole("button", { name: "登录" }).click();
    await page.waitForURL("/");
    await expect(page.getByText("新聊天")).toBeVisible();
  });

  test("提交过程中按钮防重复提交", async ({ page }) => {
    // 延迟响应以观察提交中状态。
    await page.route("**/api/auth/login", async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 1500));
      await route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ detail: { error: "invalid_credentials", message: "用户名或密码不正确。" } }),
      });
    });
    await page.goto("/login");
    await page.getByLabel("用户名或 QQ 邮箱").fill("someone");
    await page.getByLabel("密码").fill("correct-horse-12");
    const submit = page.getByRole("button", { name: "登录" });
    await submit.click();
    // 提交中按钮禁用并暴露忙碌状态，防止重复提交。
    await expect(submit).toBeDisabled();
    await expect(page.getByTestId("loading-status")).toBeVisible();
    await expect(page.getByTestId("error-summary")).toContainText("用户名或密码不正确");
    await expect(submit).toBeEnabled();
  });
});

test.describe("Issue 07 — 重定向与 Cookie 合同", () => {
  test("未登录访问受保护路由只跳转一次到登录页且不循环", async ({ page }) => {
    await page.goto("/account");
    await page.waitForURL(/\/login\?return_to=/);
    expect(page.url()).toContain(`return_to=${encodeURIComponent("/account")}`);
    // 登录页是公共路径：渲染表单，不再发生新的重定向。
    await expect(page.getByRole("heading", { name: "登录" })).toBeVisible();
    await page.waitForTimeout(500);
    expect(page.url()).toContain("/login");
  });

  test("已登录访问登录与注册页进入新聊天", async ({ page }) => {
    const creds = uniqueCredentials("i7-redirect");
    await signUp(page, creds.username, creds.qqEmail, "correct-horse-12");

    await page.goto("/login");
    await page.waitForURL("/");
    await expect(page.getByText("新聊天")).toBeVisible();

    await page.goto("/register");
    await page.waitForURL("/");
    await expect(page.getByText("新聊天")).toBeVisible();
  });

  test("会话 Cookie 为 HttpOnly + SameSite=Lax 且令牌不进入本地存储", async ({
    page,
    context,
  }) => {
    const creds = uniqueCredentials("i7-cookie");
    await signUp(page, creds.username, creds.qqEmail, "correct-horse-12");

    const cookies = await context.cookies();
    const sessionCookie = cookies.find((cookie) => cookie.name === "bridges_session");
    expect(sessionCookie).toBeTruthy();
    expect(sessionCookie!.httpOnly).toBeTruthy();
    expect(sessionCookie!.sameSite).toBe("Lax");
    expect(sessionCookie!.path).toBe("/");
    // 过期时间在约 8 小时后（允许 5 分钟误差）。
    const eightHours = 8 * 60 * 60;
    const remaining = sessionCookie!.expires - Date.now() / 1000;
    expect(remaining).toBeGreaterThan(eightHours - 300);
    expect(remaining).toBeLessThanOrEqual(eightHours);

    // 令牌不出现在 localStorage / sessionStorage。
    const stored = await page.evaluate(() => ({
      local: Object.entries(window.localStorage),
      session: Object.entries(window.sessionStorage),
    }));
    const serialized = JSON.stringify(stored);
    expect(serialized).not.toContain(sessionCookie!.value);
    expect(serialized).not.toContain("bridges_session");
  });
});

test.describe("Issue 07 — 桌面视口视觉回归", () => {
  for (const viewport of [
    { width: 1280, height: 720, name: "1280x720" },
    { width: 1440, height: 900, name: "1440x900" },
    { width: 1920, height: 1080, name: "1920x1080" },
  ]) {
    test(`登录与注册页在 ${viewport.name} 视口无回归`, async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      for (const target of [
        { path: "/login", name: "issue07-login" },
        { path: "/register", name: "issue07-register" },
      ]) {
        await page.goto(target.path);
        await page.waitForLoadState("networkidle");
        await expect(page).toHaveScreenshot(`${target.name}-${viewport.name}.png`, {
          animations: "disabled",
        });
      }
    });
  }
});
