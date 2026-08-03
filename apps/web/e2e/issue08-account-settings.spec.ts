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
    await expect(items).toHaveText(["切换账号", "密钥设置", "个人资料", "退出登录"]);
    await expect(items.nth(0)).toBeFocused();

    await page.keyboard.press("End");
    await expect(items.nth(3)).toBeFocused();
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

test.describe("Issue 08 — 密钥保护与会话闭环", () => {
  test("密钥页覆盖重新认证、成功与真实尚未配置状态", async ({ page }) => {
    const creds = uniqueCredentials("i8-reauth");
    await signUp(page, creds.username, creds.qqEmail, PASSWORD);

    let keySettingsRequests = 0;
    await page.route("**/api/auth/key-settings", async (route) => {
      keySettingsRequests += 1;
      if (keySettingsRequests === 1) {
        await route.fulfill({
          status: 403,
          contentType: "application/json",
          body: JSON.stringify({
            detail: {
              error: "reauth_required",
              message: "此页面包含敏感设置，请重新输入当前账户密码。",
              details: {},
            },
          }),
        });
      } else {
        await route.continue();
      }
    });

    await page.goto("/account/settings/keys");
    await expect(page.getByText("需要重新确认身份")).toBeVisible();
    await page.getByLabel("当前账户密码").fill(PASSWORD);
    await page.getByRole("button", { name: "确认并继续" }).press("Enter");

    await expect(page.getByText("身份确认成功，已安全读取当前账户的配置状态。")).toBeVisible();
    await expect(page.getByTestId("key-status")).toHaveText(/尚未配置/);
    // Issue 10：空态提供真实录入入口与固定能力矩阵（全部未探测，无 Stub 成功）。
    await expect(page.getByLabel("百炼 API Key")).toBeVisible();
    await expect(page.getByRole("button", { name: "保存并逐项探测" })).toBeVisible();
    await expect(page.getByText("固定能力矩阵")).toBeVisible();
    await expect(page.getByTestId("capability-chat")).toHaveText(/未探测/);
    await expect(page.getByTestId("capability-video")).toHaveText(/未探测/);
  });

  test("密钥状态读取失败可重试恢复", async ({ page }) => {
    const creds = uniqueCredentials("i8-key-error");
    await signUp(page, creds.username, creds.qqEmail, PASSWORD);

    let failed = false;
    await page.route("**/api/auth/key-settings", async (route) => {
      if (!failed) {
        failed = true;
        await route.fulfill({
          status: 500,
          contentType: "application/json",
          body: JSON.stringify({
            detail: { error: "server_error", message: "服务暂时不可用，请稍后重试。" },
          }),
        });
      } else {
        await route.continue();
      }
    });

    await page.goto("/account/settings/keys");
    await expect(page.getByText("密钥状态读取失败")).toBeVisible();
    await page.getByRole("button", { name: "重新读取" }).click();
    await expect(page.getByTestId("key-status")).toHaveText(/尚未配置/);
  });

  test("重新认证期间会话失效时立即隐藏受保护页面", async ({ page }) => {
    const creds = uniqueCredentials("i8-expired");
    await signUp(page, creds.username, creds.qqEmail, PASSWORD);
    let sessionExpired = false;

    await page.route("**/api/auth/session", async (route) => {
      if (!sessionExpired) return route.continue();
      await route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({
          detail: { error: "unauthenticated", message: "会话已失效。", details: {} },
        }),
      });
    });
    await page.route("**/api/auth/key-settings", (route) =>
      route.fulfill({
        status: 403,
        contentType: "application/json",
        body: JSON.stringify({
          detail: { error: "reauth_required", message: "请重新认证。", details: {} },
        }),
      })
    );
    await page.route("**/api/auth/reauthenticate", async (route) => {
      sessionExpired = true;
      await route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({
          detail: { error: "unauthenticated", message: "会话已失效。", details: {} },
        }),
      });
    });

    await page.goto("/account/settings/keys");
    await page.getByLabel("当前账户密码").fill(PASSWORD);
    await page.getByRole("button", { name: "确认并继续" }).press("Enter");

    await expect(page.getByText("需要重新登录").first()).toBeVisible();
    await expect(page.getByText("需要重新确认身份")).toHaveCount(0);
    await expect(page.getByLabel("当前账户密码")).toHaveCount(0);
  });

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
  test("账户菜单、个人资料与密钥页在约定视口保持稳定", async ({ page, context }) => {
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
    await page.route("**/api/auth/key-settings", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          status: "unconfigured",
          configured: false,
          key_tail: null,
          updated_at: null,
          capabilities: [
            { capability_id: "chat", display_name: "核心对话", model_id: "qwen3.7-plus-2026-05-26", status: "not_probed", message: "尚未探测。", can_retry: false, probed_at: null },
            { capability_id: "embedding", display_name: "知识库向量化", model_id: "text-embedding-v4", status: "not_probed", message: "尚未探测。", can_retry: false, probed_at: null },
            { capability_id: "asr", display_name: "语音转写", model_id: "qwen3-asr-flash-2025-09-08", status: "not_probed", message: "尚未探测。", can_retry: false, probed_at: null },
            { capability_id: "tts", display_name: "语音朗读", model_id: "qwen3-tts-flash-2025-11-27", status: "not_probed", message: "尚未探测。", can_retry: false, probed_at: null },
            { capability_id: "image", display_name: "图片生成与编辑", model_id: "qwen-image-2.0-pro-2026-06-22", status: "not_probed", message: "尚未探测。", can_retry: false, probed_at: null },
            { capability_id: "video", display_name: "视频生成", model_id: "wan2.7-t2v-2026-06-12", status: "not_probed", message: "尚未探测。", can_retry: false, probed_at: null },
          ],
          message: "尚未配置百炼密钥。",
          next_step: "录入百炼 Key 后，系统将用非用户数据逐项真实探测固定能力。",
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

      await page.goto("/");
      await page.getByRole("button", { name: /账户菜单：桥见知行/ }).click();
      await expect(page).toHaveScreenshot(`issue08-account-menu-${viewport.name}.png`, {
        animations: "disabled",
      });

      await page.goto("/account/settings/profile");
      await expect(page.getByRole("heading", { name: "让每次相遇都认得是你" })).toBeVisible();
      await expect(page).toHaveScreenshot(`issue08-profile-${viewport.name}.png`, {
        animations: "disabled",
      });

      await page.goto("/account/settings/keys");
      await expect(page.getByTestId("key-status")).toBeVisible();
      await expect(page).toHaveScreenshot(`issue08-keys-${viewport.name}.png`, {
        animations: "disabled",
      });
    }
  });
});
