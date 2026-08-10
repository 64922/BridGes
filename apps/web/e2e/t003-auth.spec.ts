import { expect, test } from "@playwright/test";

import { signIn, signOut, signUp, uniqueCredentials } from "./helpers/auth";

test.describe("T003 — 完成账户注册、登录、退出与会话恢复", () => {
  test("未登录用户无法进入认证首页", async ({ page }) => {
    await page.goto("/account");
    await page.waitForURL(/\/login/);
    await expect(page.getByRole("heading", { name: "登录" })).toBeVisible();
  });

  test("用户可以注册、登录并进入新聊天", async ({ page }) => {
    const creds = uniqueCredentials("t003-register");
    await signUp(page, creds.username, creds.qqEmail, "correct-horse-12");
    // 注册成功落在新聊天首页。
    await expect(page.getByTestId("new-chat-home")).toBeVisible();
  });

  test("登录失败显示安全且不泄露信息的错误", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel("用户名或 QQ 邮箱").fill("unknown-user");
    await page.getByLabel("密码").fill("correct-horse-12");
    await page.getByRole("button", { name: "登录" }).click();
    await expect(page.getByTestId("error-summary")).toContainText("用户名或密码不正确");
  });

  test("退出后原会话立即失效", async ({ page }) => {
    const creds = uniqueCredentials("t003-logout");
    await signUp(page, creds.username, creds.qqEmail, "correct-horse-12");
    await signOut(page);

    // Attempting to access the protected route redirects back to login.
    await page.goto("/account");
    await page.waitForURL(/\/login/);
  });

  test("凭据恢复后旧会话不可继续使用", async ({ page, request }) => {
    const creds = uniqueCredentials("t003-recover");
    await signUp(page, creds.username, creds.qqEmail, "correct-horse-12");

    // Use the backend test helper to obtain a recovery token. In production this
    // would arrive through the mailbox; e2e tests cannot receive email.
    const apiBase = process.env.API_BASE_URL || "http://127.0.0.1:8000";
    const tokenResponse = await request.get(
      `${apiBase}/_test/recovery-token?qq_email=${encodeURIComponent(creds.qqEmail)}`
    );
    expect(tokenResponse.ok()).toBeTruthy();
    const { token } = await tokenResponse.json();

    await page.goto("/");
    // Simulate using the recovery token to reset password.
    const resetResponse = await request.post(`${apiBase}/auth/recover/reset`, {
      data: { token, new_password: "new-stable-password-12" },
    });
    expect(resetResponse.ok()).toBeTruthy();

    // The existing browser session must be rejected by the API.
    const sessionResponse = await page.request.get("/api/auth/session");
    expect(sessionResponse.status()).toBe(401);
  });

  test("认证用户访问公共登录页被重定向到新聊天", async ({ page }) => {
    const creds = uniqueCredentials("t003-redirect");
    await signUp(page, creds.username, creds.qqEmail, "correct-horse-12");
    await page.goto("/login");
    await page.waitForURL("/");
    await expect(page.getByTestId("new-chat-home")).toBeVisible();
  });

  test("同一账户可以用用户名或 QQ 邮箱分别登录", async ({ page }) => {
    const creds = uniqueCredentials("t003-dual");
    await signUp(page, creds.username, creds.qqEmail, "correct-horse-12");
    await signOut(page);

    // 用 QQ 邮箱登录。
    await signIn(page, creds.qqEmail, "correct-horse-12");
    await expect(page.getByTestId("new-chat-home")).toBeVisible();
    await signOut(page);

    // 用用户名登录。
    await signIn(page, creds.username, "correct-horse-12");
    await expect(page.getByTestId("new-chat-home")).toBeVisible();
  });
});
