import { expect, test, type Locator, type Page } from "@playwright/test";

const NOW = "2026-08-03T00:00:00Z";

const TEST_ACCOUNT = {
  id: "account-issue23",
  username: "Issue 23",
  qq_email: "232323@qq.com",
  avatar_choice: "initials",
  has_uploaded_avatar: false,
  avatar_updated_at: null,
  created_at: NOW,
  updated_at: NOW,
};

type Conversation = {
  conversation_id: string;
  title: string;
  mode: "companion" | "study";
  pinned: boolean;
  project_id: string | null;
  message_count: number;
  created_at: string;
  updated_at: string;
};

function conversation(id: string, title: string): Conversation {
  return {
    conversation_id: id,
    title,
    mode: "companion",
    pinned: false,
    project_id: null,
    message_count: 2,
    created_at: NOW,
    updated_at: NOW,
  };
}

async function installMocks(page: Page) {
  const conversations = Array.from({ length: 30 }, (_, index) =>
    conversation(`issue23-${index + 1}`, `Issue 23 对话 ${index + 1}`)
  );

  await page.context().addCookies([
    { name: "bridges_session", value: "issue23-session", url: "http://127.0.0.1:3000" },
  ]);
  await page.route("**/api/auth/session", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ account: TEST_ACCOUNT, session: {}, subject: {} }),
    })
  );
  await page.route("**/api/auth/device/accounts", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        accounts: [],
        current_account: TEST_ACCOUNT,
        current_session_id: "issue23-session",
      }),
    })
  );
  await page.route("**/api/chat/conversations", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ conversations }),
    })
  );
}

async function assertMenuGeometry(page: Page) {
  const menu = page.getByRole("menu");
  await expect(menu).toBeVisible();
  const geometry = await menu.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    const menuItems = Array.from(element.querySelectorAll<HTMLElement>('[role="menuitem"]'));
    const hitTargets = menuItems.map((item) => {
      const itemRect = item.getBoundingClientRect();
      return document.elementFromPoint(itemRect.left + itemRect.width / 2, itemRect.top + itemRect.height / 2);
    });
    const recent = document.querySelector('[data-testid="recent-conversations"]');
    const viewport = { width: window.innerWidth, height: window.innerHeight };
    return {
      left: rect.left,
      top: rect.top,
      right: rect.right,
      bottom: rect.bottom,
      position: getComputedStyle(element).position,
      outsideRecentScrollArea: recent ? !recent.contains(element) : false,
      allItemsHitMenu: hitTargets.every((target) => target instanceof Node && element.contains(target)),
      documentWidth: document.documentElement.scrollWidth,
      viewport,
    };
  });

  expect(geometry.position).toBe("fixed");
  expect(geometry.outsideRecentScrollArea).toBe(true);
  expect(geometry.left).toBeGreaterThanOrEqual(-1);
  expect(geometry.top).toBeGreaterThanOrEqual(-1);
  expect(geometry.right).toBeLessThanOrEqual(geometry.viewport.width + 1);
  expect(geometry.bottom).toBeLessThanOrEqual(geometry.viewport.height + 1);
  expect(geometry.allItemsHitMenu).toBe(true);
  expect(geometry.documentWidth).toBeLessThanOrEqual(geometry.viewport.width + 1);
}

async function openMenu(page: Page, conversationId: string, withKeyboard = false): Promise<Locator> {
  const trigger = page.getByRole("button", {
    name: `会话操作：Issue 23 对话 ${conversationId.split("-").pop()}`,
    exact: true,
  });
  await trigger.scrollIntoViewIfNeeded();
  if (withKeyboard) {
    await trigger.focus();
    await trigger.press("Enter");
  } else {
    await trigger.click();
  }
  await assertMenuGeometry(page);
  return trigger;
}

test.describe("Issue 23 — 最近对话菜单浮层定位", () => {
  test.beforeEach(async ({ page }) => {
    await installMocks(page);
    await page.goto("/");
    await expect(page.getByTestId("recent-conversations")).toBeVisible();
    await expect(page.getByRole("button", { name: "会话操作：Issue 23 对话 1", exact: true })).toBeVisible();
  });

  test("首条、中间和末条菜单都完整位于视口内，并跟随最近对话滚动", async ({ page }) => {
    const recent = page.getByTestId("recent-conversations");

    const firstTrigger = await openMenu(page, "issue23-1");
    const firstBeforeScroll = await firstTrigger.boundingBox();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("menu")).toHaveCount(0);

    await recent.evaluate((element) => {
      element.scrollTop = Math.floor((element.scrollHeight - element.clientHeight) / 2);
    });
    const middleTrigger = await openMenu(page, "issue23-15");
    const middleBeforeScroll = await middleTrigger.boundingBox();
    expect(middleBeforeScroll).not.toBeNull();
    await page.keyboard.press("Escape");

    const lastTrigger = await openMenu(page, "issue23-30");
    const lastBeforeScroll = await lastTrigger.boundingBox();
    expect(lastBeforeScroll).not.toBeNull();

    await recent.evaluate((element) => {
      element.scrollTop = Math.max(0, element.scrollTop - 80);
    });
    await expect.poll(async () => (await lastTrigger.boundingBox())?.y ?? 0).not.toBe(lastBeforeScroll?.y ?? 0);
    await assertMenuGeometry(page);

    expect(firstBeforeScroll).not.toBeNull();
  });

  test("菜单键盘循环、边界跳转、Escape 归还焦点，Tab 关闭且不困住焦点", async ({ page }) => {
    const trigger = page.getByRole("button", { name: "会话操作：Issue 23 对话 1", exact: true });
    await trigger.press("Enter");
    await expect(page.getByRole("menuitem", { name: "置顶" })).toBeFocused();

    await page.keyboard.press("ArrowDown");
    await expect(page.getByRole("menuitem", { name: "改名" })).toBeFocused();
    await page.keyboard.press("End");
    await expect(page.getByRole("menuitem", { name: "删除" })).toBeFocused();
    await page.keyboard.press("Home");
    await expect(page.getByRole("menuitem", { name: "置顶" })).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("menu")).toHaveCount(0);
    await expect(trigger).toBeFocused();

    await trigger.press("Space");
    await expect(page.getByRole("menu")).toBeVisible();
    await page.keyboard.press("Tab");
    await expect(page.getByRole("menu")).toHaveCount(0);
  });

  for (const viewport of [
    { label: "1280x720", width: 1280, height: 720 },
    { label: "1440x900", width: 1440, height: 900 },
    { label: "1920x1080", width: 1920, height: 1080 },
  ]) {
    for (const zoom of [1, 2]) {
      test(`桌面视口 ${viewport.label} @${zoom * 100}% 的首中末条菜单保持几何边界`, async ({ page }) => {
        await page.setViewportSize({
          width: Math.round(viewport.width / zoom),
          height: Math.round(viewport.height / zoom),
        });
        await page.waitForTimeout(120);

        for (const conversationId of ["issue23-1", "issue23-15", "issue23-30"]) {
          await openMenu(page, conversationId, zoom === 2);
          await page.keyboard.press("Escape");
          await expect(page.getByRole("menu")).toHaveCount(0);
        }
      });
    }
  }
});
