import { expect, test } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

const PASSWORD = "correct-horse-12";
const PNG_1X1 = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
  "base64"
);

test.describe("Issue 08 — 账户上拉菜单", () => {
  test("侧栏账户入口展示头像和用户名，菜单顺序与键盘合同完整", async ({ page }) => {
    const creds = uniqueCredentials("i8-menu");
    await signUp(page, creds.username, creds.qqEmail, PASSWORD);

    const trigger = page.getByRole("button", { name: new RegExp(`账户菜单：${creds.username}`) });
    await expect(trigger).toBeVisible();
    await expect(trigger.getByTestId("account-avatar")).toBeVisible();

    await trigger.focus();
    await page.keyboard.press("Enter");
    const items = page.getByRole("menuitem");
    // GQ-06：账户菜单严格为三项（移除密钥设置）。
    await expect(items).toHaveText(["切换账号", "个人资料", "退出登录"]);
    await expect(items.nth(0)).toBeFocused();

    await page.keyboard.press("End");
    await expect(items.nth(2)).toBeFocused();
    await page.keyboard.press("Home");
    await expect(items.nth(0)).toBeFocused();
    await page.keyboard.press("ArrowDown");
    await expect(items.nth(1)).toBeFocused();
    await page.keyboard.press("ArrowUp");
    await expect(items.nth(0)).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(items).toHaveCount(0);
    await expect(trigger).toBeFocused();

    await trigger.press("Space");
    await expect(page.getByRole("menu")).toBeVisible();
    await page.getByTestId("main-content").click({ position: { x: 40, y: 40 } });
    await expect(page.getByRole("menu")).toHaveCount(0);
    await expect(trigger).toBeFocused();
  });

  test("同设备只有当前账户时，切换账号展示安全空态", async ({ page }) => {
    const creds = uniqueCredentials("i8-switch");
    await signUp(page, creds.username, creds.qqEmail, PASSWORD);

    await page.getByRole("button", { name: /账户菜单：/ }).click();
    await page.getByRole("menuitem", { name: "切换账号" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(page.getByTestId("no-other-account")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toHaveCount(0);
  });
});

test.describe("Issue 08 — 个人资料", () => {
  test("仅用键盘选择上传头像、修改用户名并同步当前会话", async ({ page }) => {
    const creds = uniqueCredentials("i8-profile");
    await signUp(page, creds.username, creds.qqEmail, PASSWORD);
    const before = await page.request.get("/api/auth/session");
    const beforeAccount = (await before.json()).account;

    const accountTrigger = page.getByRole("button", { name: /账户菜单：/ });
    await accountTrigger.focus();
    await page.keyboard.press("Enter");
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("Enter");
    await page.waitForURL("/account/settings/profile");

    const fileInput = page.getByLabel("上传图片");
    await fileInput.focus();
    await expect(fileInput).toBeFocused();
    const [chooser] = await Promise.all([
      page.waitForEvent("filechooser"),
      fileInput.press("Enter"),
    ]);
    await chooser.setFiles({ name: "avatar.png", mimeType: "image/png", buffer: PNG_1X1 });
    await expect(page.getByRole("img", { name: "当前头像预览" })).toHaveAttribute(
      "data-avatar-choice",
      "uploaded"
    );

    const nextUsername = `新桥-${Date.now().toString().slice(-8)}`;
    await page.getByLabel("用户名").focus();
    await page.getByLabel("用户名").fill(nextUsername);
    await page.getByRole("button", { name: "保存个人资料" }).focus();
    await page.keyboard.press("Enter");

    await expect(page.getByTestId("profile-success")).toBeVisible();
    await expect(page.getByRole("button", { name: new RegExp(`账户菜单：${nextUsername}`) }))
      .toBeVisible();
    await expect(
      page.locator('[data-testid="account-menu-region"] [data-avatar-choice="uploaded"]')
    ).toBeVisible();

    const after = await page.request.get("/api/auth/session");
    const afterAccount = (await after.json()).account;
    expect(afterAccount.id).toBe(beforeAccount.id);
    expect(afterAccount.qq_email).toBe(beforeAccount.qq_email);
    expect(afterAccount.username).toBe(nextUsername);

    await page.goto("/");
    await expect(
      page.getByRole("button", { name: new RegExp(`账户菜单：${nextUsername}`) })
    ).toBeVisible();

    const updatedTrigger = page.getByRole("button", { name: new RegExp(`账户菜单：${nextUsername}`) });
    await updatedTrigger.focus();
    await page.keyboard.press("Enter");
    await page.keyboard.press("End");
    await page.keyboard.press("Enter");
    await page.waitForURL(/\/login\?from=logout/);
    await expect(page.getByText("你已安全退出，会话已撤销。")).toBeVisible();
  });

  test("上传错误给出可恢复中文反馈，静态头像仍可保存", async ({ page }) => {
    const creds = uniqueCredentials("i8-avatar-error");
    await signUp(page, creds.username, creds.qqEmail, PASSWORD);
    await page.goto("/account/settings/profile");

    await page.getByLabel("上传图片").setInputFiles({
      name: "avatar.gif",
      mimeType: "image/gif",
      buffer: Buffer.from("GIF89a", "ascii"),
    });
    await expect(page.getByText(/请选择静态 PNG 或 JPEG/)).toBeVisible();

    await page.getByRole("radio", { name: "知识手册" }).click();
    await page.getByRole("button", { name: "保存个人资料" }).click();
    await expect(page.getByTestId("profile-success")).toBeVisible();
    await expect(
      page.locator('[data-testid="account-menu-region"] [data-avatar-choice="knowledge"]')
    ).toBeVisible();
  });
});

test.describe("Issue 08 — 受保护页面与会话闭环", () => {
  test("权限拒绝不会短暂渲染受保护子页面", async ({ page, context }) => {
    await context.addCookies([
      { name: "bridges_session", value: "guessed-session", url: "http://127.0.0.1:3000" },
    ]);
    await page.goto("/account/settings/profile");

    await expect(page.getByText("需要重新登录").first()).toBeVisible();
    await expect(page.getByRole("heading", { name: "让每次相遇都认得是你" })).toHaveCount(0);
    await expect(page.getByLabel("用户名")).toHaveCount(0);
  });

  test("退出后撤销 Cookie，浏览器后退不能重新进入受保护资料页", async ({ page }) => {
    const creds = uniqueCredentials("i8-logout");
    await signUp(page, creds.username, creds.qqEmail, PASSWORD);
    await page.goto("/account/settings/profile");
    await expect(page.getByRole("heading", { name: "让每次相遇都认得是你" })).toBeVisible();
    await page.evaluate(() => localStorage.setItem("bridges:device-preference", "保留"));

    await page.getByRole("button", { name: /账户菜单：/ }).click();
    await page.getByRole("menuitem", { name: "退出登录" }).click();
    await page.waitForURL(/\/login\?from=logout/);
    await expect(page.getByText("你已安全退出，会话已撤销。")).toBeVisible();
    await expect.poll(() => page.evaluate(() => localStorage.getItem("bridges:device-preference")))
      .toBe("保留");

    await page.goBack();
    await page.waitForLoadState("domcontentloaded");
    await expect(page.getByRole("heading", { name: "让每次相遇都认得是你" })).toHaveCount(0);
    expect(page.url()).not.toContain("/account/settings/profile");
  });
});

test.describe("Issue 08 — 桌面视觉回归", () => {
  test("账户菜单与个人资料在约定视口保持稳定", async ({ page, context }) => {
    const fixedAccount = {
      id: "acct-visual-issue-08",
      username: "桥见知行",
      qq_email: "123456789@qq.com",
      avatar_choice: "bridge",
      has_uploaded_avatar: false,
      avatar_updated_at: null,
      created_at: "2026-08-03T00:00:00Z",
      updated_at: "2026-08-03T00:00:00Z",
    };
    await context.addCookies([
      { name: "bridges_session", value: "visual-session", url: "http://127.0.0.1:3000" },
    ]);
    await page.route("**/api/auth/session", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          account: fixedAccount,
          session: {
            id: "session-visual",
            account_id: fixedAccount.id,
            created_at: "2026-08-03T00:00:00Z",
            expires_at: "2026-08-03T08:00:00Z",
            revoked_at: null,
          },
          subject: {
            account_id: fixedAccount.id,
            session_id: "session-visual",
            auth_method: "password",
            device_id: null,
            memberships: [],
          },
        }),
      })
    );
    // Issue 11：新聊天首页会拉取真实对话列表；视觉回归注入空列表保证确定性。
    await page.route("**/api/chat/conversations", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ conversations: [] }),
      })
    );

    for (const viewport of [
      { width: 1280, height: 720, name: "1280x720" },
      { width: 1440, height: 900, name: "1440x900" },
      { width: 1920, height: 1080, name: "1920x1080" },
    ]) {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });

      // 本机 Playwright+Next dev 环境下，HttpOnly cookie 在页面加载后会被
      // 浏览器存储清除；每次导航前重新注入，保证服务端会话判定一致。
      await context.addCookies([
        { name: "bridges_session", value: "visual-session", url: "http://127.0.0.1:3000" },
      ]);

      await page.goto("/");
      await page.getByRole("button", { name: /账户菜单：桥见知行/ }).click();
      await expect(page).toHaveScreenshot(`issue08-account-menu-${viewport.name}.png`, {
        animations: "disabled",
      });

      await context.addCookies([
        { name: "bridges_session", value: "visual-session", url: "http://127.0.0.1:3000" },
      ]);
      await page.goto("/account/settings/profile");
      await expect(page.getByRole("heading", { name: "让每次相遇都认得是你" })).toBeVisible();
      await expect(page).toHaveScreenshot(`issue08-profile-${viewport.name}.png`, {
        animations: "disabled",
      });
    }
  });
});
