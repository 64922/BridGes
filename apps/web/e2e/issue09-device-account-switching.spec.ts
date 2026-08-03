import { expect, test, type Page } from "@playwright/test";

const alice = {
  id: "account-alice",
  username: "Alice",
  qq_email: "111111@qq.com",
  avatar_choice: "initials",
  has_uploaded_avatar: false,
  avatar_updated_at: null,
  created_at: "2026-08-03T00:00:00Z",
  updated_at: "2026-08-03T00:00:00Z",
};

const bob = {
  id: "account-bob",
  username: "Bob",
  qq_email: "222222@qq.com",
  avatar_choice: "knowledge",
  has_uploaded_avatar: false,
  avatar_updated_at: null,
  created_at: "2026-08-03T00:00:00Z",
  updated_at: "2026-08-03T00:00:00Z",
};

function session(account: typeof alice, id: string) {
  return {
    account,
    session: {
      id,
      account_id: account.id,
      created_at: "2026-08-03T00:00:00Z",
      expires_at: "2026-08-03T08:00:00Z",
      revoked_at: null,
    },
    subject: {
      account_id: account.id,
      session_id: id,
      auth_method: "password",
      device_id: "device-1",
      memberships: [],
    },
  };
}

function deviceAccount(account: typeof alice, sessionId: string, isCurrent: boolean) {
  return {
    session_id: sessionId,
    username: account.username,
    masked_qq_email: `${account.qq_email.slice(0, 2)}***${account.qq_email.slice(-7)}`,
    avatar_choice: account.avatar_choice,
    has_uploaded_avatar: false,
    status: "active",
    is_current: isCurrent,
  };
}

async function setUpAuthenticatedPage(page: Page) {
  await page.context().addCookies([
    { name: "bridges_session", value: "mock-session", url: "http://127.0.0.1:3000" },
  ]);
  await page.route("**/api/auth/session", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(session(alice, "s-alice")) })
  );
  await page.route("**/api/projects", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ active: [], archived: [] }) })
  );
  await page.goto("/account/settings");
  await expect(page.getByRole("button", { name: /账户菜单：Alice/ })).toBeVisible();
}

test.describe("Issue 09 — 同设备账户切换与再认证", () => {
  test("显示脱敏账户列表，有效会话可切换且 Esc 归还焦点", async ({ page }) => {
    await setUpAuthenticatedPage(page);
    await page.route("**/api/auth/device/accounts", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          accounts: [deviceAccount(alice, "s-alice", true), deviceAccount(bob, "s-bob", false)],
          current_account: alice,
          current_session_id: "s-alice",
        }),
      })
    );
    await page.route("**/api/auth/device/switch", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          accounts: [deviceAccount(bob, "s-bob", true), deviceAccount(alice, "s-alice", false)],
          current_account: bob,
          current_session_id: "s-bob",
        }),
      })
    );

    const trigger = page.getByRole("button", { name: /账户菜单：Alice/ });
    await trigger.focus();
    await page.keyboard.press("Enter");
    await page.getByRole("menuitem", { name: "切换账号" }).press("Enter");
    await expect(page.getByRole("dialog", { name: "切换账号" })).toBeVisible();
    await expect(page.getByText("11***@qq.com")).toBeVisible();
    await expect(page.getByText("22***@qq.com")).toBeVisible();

    await page.getByRole("button", { name: /Bob，22\*\*\*@qq.com/ }).click();
    await expect(page.getByRole("button", { name: /账户菜单：Bob/ })).toBeVisible();
    await expect(page.getByRole("dialog")).toHaveCount(0);

    const switchedTrigger = page.getByRole("button", { name: /账户菜单：Bob/ });
    await switchedTrigger.focus();
    await page.keyboard.press("Enter");
    await page.getByRole("menuitem", { name: "切换账号" }).press("Enter");
    await expect(page.getByRole("dialog")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(switchedTrigger).toBeFocused();
  });

  test("失效账户切换进入密码再认证，错误不泄露会话状态", async ({ page }) => {
    await setUpAuthenticatedPage(page);
    await page.route("**/api/auth/device/accounts", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          accounts: [
            deviceAccount(alice, "s-alice", true),
            { ...deviceAccount(bob, "s-bob", false), status: "reauth_required" },
          ],
          current_account: alice,
          current_session_id: "s-alice",
        }),
      })
    );
    await page.route("**/api/auth/device/switch", (route) =>
      route.fulfill({
        status: 403,
        contentType: "application/json",
        body: JSON.stringify({
          detail: { error: "reauth_required", message: "请重新输入该账户密码。", details: {} },
        }),
      })
    );
    await page.route("**/api/auth/device/reauthenticate", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          accounts: [deviceAccount(bob, "s-bob-new", true), deviceAccount(alice, "s-alice", false)],
          current_account: bob,
          current_session_id: "s-bob-new",
        }),
      })
    );

    await page.getByRole("button", { name: /账户菜单：Alice/ }).click();
    await page.getByRole("menuitem", { name: "切换账号" }).click();
    await page.getByRole("button", { name: /Bob，22\*\*\*@qq.com/ }).click();
    await expect(page.getByRole("heading", { name: "重新认证" })).toBeVisible();
    await page.getByLabel("该账户密码").fill("correct-horse-12");
    await page.getByRole("button", { name: "确认并切换" }).click();
    await expect(page.getByRole("button", { name: /账户菜单：Bob/ })).toBeVisible();
  });

  test("慢响应在切换账户后返回不写入新账户界面（请求作用域隔离）", async ({ page }) => {
    // Alice 挂载时发起的项目请求被延迟；切换 Bob 后该响应才返回，
    // 不得把 Alice 数据渲染进 Bob 的界面（Issue 09 慢响应攻击向量）。
    await page.context().addCookies([
      { name: "bridges_session", value: "mock-session", url: "http://127.0.0.1:3000" },
    ]);
    await page.route("**/api/auth/session", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(session(alice, "s-alice")) })
    );
    let projectCalls = 0;
    await page.route("**/api/projects", async (route) => {
      projectCalls += 1;
      const isAliceRequest = projectCalls === 1;
      if (isAliceRequest) {
        // 慢响应：模拟账户 A 的请求在网络中滞留。
        await new Promise((resolve) => setTimeout(resolve, 900));
      }
      // 只有 Alice 的首次请求返回 Alice 数据；切换后的请求为空列表。
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          active: isAliceRequest
            ? [
                {
                  name: "Alice 的慢速项目",
                  status: "active",
                  ref: { object_id: "p-slow-1", version: 1, domain: "project" },
                },
              ]
            : [],
          archived: [],
        }),
      });
    });
    await page.route("**/api/auth/device/accounts", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          accounts: [deviceAccount(alice, "s-alice", true), deviceAccount(bob, "s-bob", false)],
          current_account: alice,
          current_session_id: "s-alice",
        }),
      })
    );
    await page.route("**/api/auth/device/switch", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          accounts: [deviceAccount(bob, "s-bob", true), deviceAccount(alice, "s-alice", false)],
          current_account: bob,
          current_session_id: "s-bob",
        }),
      })
    );

    await page.goto("/account");
    await expect(page.getByText("欢迎回来，Alice")).toBeVisible();

    // 在 Alice 的慢请求未返回时切换到 Bob。
    await page.getByRole("button", { name: /账户菜单：Alice/ }).click();
    await page.getByRole("menuitem", { name: "切换账号" }).click();
    await page.getByRole("button", { name: /Bob，22\*\*\*@qq.com/ }).click();
    await expect(page.getByRole("button", { name: /账户菜单：Bob/ })).toBeVisible();

    // 慢响应返回后不得把 Alice 的项目写进 Bob 的界面。
    await page.waitForTimeout(1200);
    await expect(page.getByText("Alice 的慢速项目")).toHaveCount(0);
    await expect(page.getByText("欢迎回来，Bob")).toBeVisible();
  });

  test("浏览器后退不能重新进入已退出的受保护内容", async ({ page }) => {
    await setUpAuthenticatedPage(page);
    await page.route("**/api/auth/device/accounts", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          accounts: [deviceAccount(alice, "s-alice", true)],
          current_account: alice,
          current_session_id: "s-alice",
        }),
      })
    );
    await page.route("**/api/auth/device/logout-all", (route) =>
      route.fulfill({
        status: 204,
        headers: {
          "set-cookie": "bridges_session=; Max-Age=0; Path=/",
        },
        body: "",
      })
    );

    // 建立更早的受保护历史条目：退出 replace 后回退命中受保护条目时，
    // 中间件必须因无会话 Cookie 把用户拦回登录页，而非重新渲染受保护内容。
    await page.goto("/account");

    await page.getByRole("button", { name: /账户菜单：Alice/ }).click();
    await page.getByRole("menuitem", { name: "切换账号" }).click();
    await page.getByRole("button", { name: "退出此设备上的全部账户" }).click();
    await page.getByRole("button", { name: "确认退出全部账户" }).click();
    await page.waitForURL(/\/login\?from=device-logout/);

    // 退出使用 location.replace 落地登录页，历史中不保留受保护条目；
    // 后退只能回到公共登录页，不能重新进入受保护内容。
    await page.goBack();
    await expect(page).toHaveURL(/\/login/);
    await expect(page.getByRole("heading", { name: "登录" })).toBeVisible();
  });

  test("添加账户必须登录，退出当前与退出全部账户都有安全落点", async ({ page }) => {
    await setUpAuthenticatedPage(page);
    await page.route("**/api/auth/device/accounts", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          accounts: [deviceAccount(alice, "s-alice", true)],
          current_account: alice,
          current_session_id: "s-alice",
        }),
      })
    );
    await page.route("**/api/auth/device/accounts/add", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          accounts: [deviceAccount(bob, "s-bob", true), deviceAccount(alice, "s-alice", false)],
          current_account: bob,
          current_session_id: "s-bob",
        }),
      })
    );
    await page.route("**/api/auth/device/logout-all", (route) =>
      route.fulfill({
        status: 204,
        headers: {
          "set-cookie": "bridges_session=; Max-Age=0; Path=/",
        },
        body: "",
      })
    );
    await page.route("**/api/auth/device/logout", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ current_account: alice, current_session_id: "s-alice" }),
      })
    );

    await page.getByRole("button", { name: /账户菜单：Alice/ }).click();
    await page.getByRole("menuitem", { name: "切换账号" }).click();
    await page.getByRole("button", { name: "添加账户" }).click();
    await page.getByLabel("用户名或 QQ 邮箱").fill("Bob");
    await page.getByLabel("密码").fill("correct-horse-12");
    await page.getByRole("button", { name: "登录并添加" }).click();
    await expect(page.getByRole("button", { name: /账户菜单：Bob/ })).toBeVisible();

    await page.getByRole("button", { name: /账户菜单：Bob/ }).click();
    await page.getByRole("menuitem", { name: "切换账号" }).click();
    await page.getByRole("button", { name: "退出当前账户" }).click();
    await expect(page.getByRole("heading", { name: "确认退出当前账户" })).toBeVisible();
    await page.getByRole("button", { name: "确认退出当前账户" }).click();
    await expect(page.getByRole("button", { name: /账户菜单：Alice/ })).toBeVisible();

    await page.getByRole("button", { name: /账户菜单：Alice/ }).click();
    await page.getByRole("menuitem", { name: "切换账号" }).click();
    await page.getByRole("button", { name: "退出此设备上的全部账户" }).click();
    await expect(page.getByRole("heading", { name: "确认退出全部账户" })).toBeVisible();
    await page.getByRole("button", { name: "确认退出全部账户" }).click();
    await page.waitForURL(/\/login\?from=device-logout/);
  });
});
