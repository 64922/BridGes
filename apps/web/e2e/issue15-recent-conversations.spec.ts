import { expect, test, type Page } from "@playwright/test";

const NOW = "2026-08-03T00:00:00Z";

const TEST_ACCOUNT = {
  id: "account-issue15",
  username: "Issue 15",
  qq_email: "151515@qq.com",
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

function history(conversation: Conversation) {
  return {
    ...conversation,
    messages: [],
    mode_events: [],
  };
}

async function installConversationApi(page: Page, initial: Conversation[]) {
  const conversations = new Map(initial.map((conversation) => [conversation.conversation_id, conversation]));

  await page.route("**/api/chat/conversations", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ conversations: Array.from(conversations.values()) }),
      });
      return;
    }
    await route.continue();
  });

  await page.route("**/api/chat/conversations/*", async (route) => {
    const url = new URL(route.request().url());
    const conversationId = url.pathname.split("/").pop() ?? "";
    const conversation = conversations.get(conversationId);
    if (!conversation) {
      await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: {} }) });
      return;
    }
    if (route.request().method() === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(history(conversation)) });
      return;
    }
    if (route.request().method() === "PATCH") {
      const body = JSON.parse(route.request().postData() ?? "{}");
      if (body.title !== undefined) conversation.title = body.title;
      if (body.pinned !== undefined) conversation.pinned = body.pinned;
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(history(conversation)) });
      return;
    }
    if (route.request().method() === "DELETE") {
      conversations.delete(conversationId);
      await route.fulfill({ status: 204, body: "" });
      return;
    }
    await route.continue();
  });
}

async function installAuthenticatedSession(page: Page) {
  await page.context().addCookies([
    { name: "bridges_session", value: "issue15-session", url: "http://127.0.0.1:3000" },
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
        current_session_id: "issue15-session",
      }),
    })
  );
  // Issue 19：侧栏新增学习项目列表请求；不 mock 时真实 API 以 401 清除
  // 伪会话 Cookie，后续整页导航会落到公开首页。
  await page.route("**/api/learning-projects", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ projects: [] }),
    })
  );
}

function conversation(id: string, title: string, mode: Conversation["mode"]): Conversation {
  return {
    conversation_id: id,
    title,
    mode,
    pinned: false,
    project_id: null,
    message_count: 2,
    created_at: NOW,
    updated_at: NOW,
  };
}

test.describe("Issue 15 — 最近对话与会话生命周期", () => {
  test("真实列表显示模式文字，并闭环置顶、改名、失败恢复、删除和键盘焦点", async ({ page }) => {
    await installAuthenticatedSession(page);
    await installConversationApi(page, [
      conversation("c-companion", "日常问题", "companion"),
      conversation("c-study", "量子学习", "study"),
    ]);
    await page.goto("/");

    const sidebar = page.getByTestId("app-sidebar");
    await expect(sidebar.getByRole("link", { name: /日常问题/ })).toBeVisible();
    await expect(sidebar.getByText("日常陪伴")).toBeVisible();
    await expect(sidebar.getByText("学习模式")).toBeVisible();

    const companionActions = sidebar.getByRole("button", { name: "会话操作：日常问题" });
    await companionActions.click();
    await page.getByRole("menuitem", { name: "置顶" }).click();
    await expect(sidebar.getByText(/置顶 · 日常问题/)).toBeVisible();

    await companionActions.click();
    await page.getByRole("menuitem", { name: "改名" }).click();
    const renameDialog = page.getByRole("dialog", { name: "修改会话名称" });
    await renameDialog.getByLabel("会话名称").fill("自然科学问题");
    await renameDialog.getByRole("button", { name: "保存名称" }).click();
    await expect(sidebar.getByRole("link", { name: /自然科学问题/ })).toBeVisible();

    // 操作失败不伪装成功，列表重新读取并给出恢复提示。
    await page.route("**/api/chat/conversations/c-study", async (route) => {
      if (route.request().method() === "PATCH") {
        await route.fulfill({
          status: 500,
          contentType: "application/json",
          body: JSON.stringify({ detail: { message: "服务暂时不可用。" } }),
        });
        return;
      }
      await route.continue();
    });
    const studyActions = sidebar.getByRole("button", { name: "会话操作：量子学习" });
    await studyActions.click();
    await page.getByRole("menuitem", { name: "置顶" }).click();
    await expect(sidebar.getByRole("alert")).toContainText("列表已恢复");
    await expect(sidebar.getByText("量子学习")).toBeVisible();

    // Esc 关闭菜单并把焦点归还触发按钮。
    await studyActions.click();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("menu")).toHaveCount(0);
    await expect(studyActions).toBeFocused();

    // 删除当前会话后回到新聊天，不留下空白详情页。
    await sidebar.getByRole("link", { name: /自然科学问题/ }).click();
    await page.waitForURL("/chat/c-companion");
    await page.getByRole("button", { name: "会话操作：自然科学问题" }).click();
    await page.getByRole("menuitem", { name: "删除" }).click();
    const deleteDialog = page.getByRole("dialog", { name: "删除会话？" });
    await expect(deleteDialog).toContainText("无法恢复");
    await deleteDialog.getByRole("button", { name: "确认删除" }).click();
    await page.waitForURL("/");
    await expect(page.getByTestId("app-sidebar").getByRole("link", { name: /自然科学问题/ })).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "有什么可以帮你的？" })).toBeVisible();
  });

  test("账户切换后立即清空旧账户列表并加载目标账户列表", async ({ page }) => {
    const alice = {
      id: "account-alice",
      username: "Alice",
      qq_email: "111111@qq.com",
      avatar_choice: "initials",
      has_uploaded_avatar: false,
      avatar_updated_at: null,
      created_at: NOW,
      updated_at: NOW,
    };
    const bob = { ...alice, id: "account-bob", username: "Bob", qq_email: "222222@qq.com" };
    let current = alice;
    await page.context().addCookies([
      { name: "bridges_session", value: "mock-session", url: "http://127.0.0.1:3000" },
    ]);
    await page.route("**/api/auth/session", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ account: current, session: {}, subject: {} }) })
    );
    await page.route("**/api/auth/device/accounts", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          accounts: [
            { session_id: "s-alice", username: "Alice", masked_qq_email: "11***@qq.com", avatar_choice: "initials", has_uploaded_avatar: false, status: "active", is_current: current.id === alice.id },
            { session_id: "s-bob", username: "Bob", masked_qq_email: "22***@qq.com", avatar_choice: "initials", has_uploaded_avatar: false, status: "active", is_current: current.id === bob.id },
          ],
          current_account: current,
          current_session_id: current.id === alice.id ? "s-alice" : "s-bob",
        }),
      })
    );
    await page.route("**/api/auth/device/switch", async (route) => {
      current = bob;
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ current_account: bob, current_session_id: "s-bob" }) });
    });
    await page.route("**/api/chat/conversations", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ conversations: [conversation(current.id === alice.id ? "alice-chat" : "bob-chat", `${current.username} 的会话`, "companion")] }),
      })
    );

    await page.goto("/");
    await expect(page.getByText("Alice 的会话")).toBeVisible();
    await page.getByRole("button", { name: /账户菜单：Alice/ }).click();
    await page.getByRole("menuitem", { name: "切换账号" }).click();
    await page.getByRole("button", { name: /Bob，22\*\*\*@qq.com/ }).click();
    await expect(page.getByRole("button", { name: /账户菜单：Bob/ })).toBeVisible();
    await expect(page.getByText("Alice 的会话")).toHaveCount(0);
    await expect(page.getByText("Bob 的会话")).toBeVisible();
  });
});
