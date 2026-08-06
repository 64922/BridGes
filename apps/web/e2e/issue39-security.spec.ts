import { expect, test } from "@playwright/test";

import { signOut, signUp, uniqueCredentials } from "./helpers/auth";

/**
 * Issue 39 — 加固安全、隐私与账户隔离（E2E 层）。
 *
 * 后端拒绝路径（CSRF 来源校验 403、对象标识 404、路径闭锁）由 pytest
 * 回归测试覆盖（浏览器无法伪造托管来源头，E2E 不重复验证拒绝路径）；
 * 这里验证浏览器可观察的安全契约：
 * 1. 会话 Cookie HttpOnly：JavaScript 无法读取会话令牌；
 * 2. 无效/伪造会话 Cookie 不形成登录重定向循环（清 Cookie → 可恢复登录）；
 * 3. 登出/切换账户时清空账户作用域的本地临时数据（bridges: sessionStorage 键）；
 * 4. 正常登录流程不被 CSRF 防护误伤。
 */

test.describe("Issue 39 — 安全加固 E2E", () => {
  test("会话 Cookie 为 HttpOnly：页面脚本无法读取令牌", async ({ page }) => {
    const credentials = uniqueCredentials("i39-http");
    await signUp(page, credentials.username, credentials.qqEmail, "correct-horse-12");

    const exposedCookies = await page.evaluate(() => document.cookie);
    expect(exposedCookies).not.toContain("bridges_session=");

    // 浏览器确实持有会话 Cookie（HttpOnly 仍然随请求发送）
    const cookies = await page.context().cookies();
    const sessionCookie = cookies.find((cookie) => cookie.name === "bridges_session");
    expect(sessionCookie).toBeDefined();
    expect(sessionCookie?.httpOnly).toBe(true);
    expect(sessionCookie?.sameSite).toBe("Lax");
    expect(sessionCookie?.path).toBe("/");
  });

  test("无效会话 Cookie 不形成登录重定向循环", async ({ page }) => {
    // 伪造一个无效会话 Cookie 后访问登录页：中间件会跳过登录页，
    // 但应用会话校验会清除无效 Cookie 并给出可恢复的登录入口，
    // 全程不出现无限重定向。
    await page.context().addCookies([
      {
        name: "bridges_session",
        value: "forged-invalid-token-value",
        url: "http://127.0.0.1:3000",
      },
    ]);
    await page.goto("/login");
    // 中间件看到「有 Cookie」→ 跳回首页；首页会话校验 401 并清除 Cookie
    await page.waitForURL("/");
    await expect(page.getByText("需要重新登录", { exact: false }).first()).toBeVisible();
    await page.getByRole("button", { name: "前往登录" }).click();
    // Cookie 已清除：登录页正常显示（无重定向循环）
    await page.waitForURL(/\/login/);
    await expect(page.getByRole("heading", { name: "登录" })).toBeVisible();
  });

  test("登出账户时清空账户作用域的本地临时数据", async ({ page }) => {
    const credentials = uniqueCredentials("i39-sw");
    await signUp(page, credentials.username, credentials.qqEmail, "correct-horse-12");

    // 模拟页面遗留的账户作用域暂存数据（待发送消息/插件意图桥等 bridges: 键）。
    await page.evaluate(() => {
      sessionStorage.setItem("bridges:chat:prompt:conv-1", "上一账户的草稿");
      sessionStorage.setItem("bridges:plugin:open-humanizer", "1");
      sessionStorage.setItem("bridges:chat:attachments:conv-1", '["obj-1"]');
    });

    // 登出（切换/登出账户共用同一清理路径）。
    await signOut(page);

    const remaining = await page.evaluate(() => {
      const keys: string[] = [];
      for (let index = 0; index < sessionStorage.length; index += 1) {
        const key = sessionStorage.key(index);
        if (key) keys.push(key);
      }
      return keys.filter((key) => key.startsWith("bridges:"));
    });
    expect(remaining).toEqual([]);
  });

  test("登录页正常流程不被 CSRF 防护误伤", async ({ page }) => {
    const credentials = uniqueCredentials("i39-nm");
    await signUp(page, credentials.username, credentials.qqEmail, "correct-horse-12");
    await signOut(page);

    // 常规登录（同源浏览器请求）必须成功。
    await page.goto("/login");
    await page.getByLabel("用户名或 QQ 邮箱").fill(credentials.username);
    await page.getByLabel("密码").fill("correct-horse-12");
    await page.getByRole("button", { name: "登录" }).click();
    await page.waitForURL("/");
    await expect(page.getByRole("button", { name: /账户菜单：/ })).toBeVisible();
  });
});
