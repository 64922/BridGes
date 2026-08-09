import { expect, test, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

/**
 * Issue 12 — ChatGPT 电脑端模板的全局聊天外壳与固定顺序侧栏。
 *
 * 覆盖：固定顺序渲染、Logo 进入新聊天且不建空会话、收起/恢复与持久化、
 * 底部账户菜单、旧工作台入口退场、模块路由与真实空状态、搜索状态闭环、
 * 最近对话加载/错误/空/正常状态、键盘遍历与焦点可见、单一 aria-current、
 * 长用户名布局。
 */

const PASSWORD = "correct-horse-12";

async function freshAccount(page: Page, prefix: string) {
  const creds = uniqueCredentials(prefix);
  await signUp(page, creds.username, creds.qqEmail, PASSWORD);
  return creds;
}

/** 聊天存储未启用的 API 实例（reuseExistingServer 场景）跳过依赖对话的用例。 */
async function skipIfChatStorageDisabled(page: Page) {
  const chatReady = await page.request.get("/api/chat/conversations");
  if (chatReady.status() === 503) {
    test.skip(true, "当前 API 实例未启用对话存储（BRIDGES_DATABASE_URL），跳过该用例。");
  }
}

async function createConversation(page: Page, title: string | null): Promise<string> {
  const created = await page.request.post("/api/chat/conversations", { data: { title } });
  expect(created.ok()).toBeTruthy();
  const body = await created.json();
  return body.conversation_id as string;
}

test.describe("Issue 12 — 固定顺序侧栏", () => {
  test("侧栏按固定顺序渲染：Logo、搜索、收起、新聊天、五个模块、最近对话、底部账户菜单", async ({
    page,
  }) => {
    const creds = await freshAccount(page, "i12-order");
    const sidebar = page.getByTestId("app-sidebar");
    await expect(sidebar).toBeVisible();

    // Logo 是侧栏第一个链接，带品牌可访问名
    const logo = sidebar.getByRole("link", { name: "BridGes — 新聊天" });
    await expect(logo).toBeVisible();
    await expect(sidebar.locator("a").first()).toHaveAccessibleName("BridGes — 新聊天");

    // 文本入口按固定顺序出现
    const text = await sidebar.innerText();
    const orderedLabels = [
      "搜索",
      "收起侧边栏",
      "新聊天",
      "本地知识库",
      "学习项目",
      "插件",
      "用户画像",
      "最近对话",
    ];
    let cursor = -1;
    for (const label of orderedLabels) {
      const index = text.indexOf(label);
      expect(index, `侧栏应包含「${label}」且顺序正确`).toBeGreaterThan(cursor);
      cursor = index;
    }

    // 账户菜单固定在侧栏底部区域内
    const trigger = sidebar.getByRole("button", {
      name: new RegExp(`账户菜单：${creds.username}`),
    });
    await expect(trigger).toBeVisible();
    const sidebarBox = (await sidebar.boundingBox())!;
    const triggerBox = (await trigger.boundingBox())!;
    expect(triggerBox.y).toBeGreaterThan(sidebarBox.height * 0.6);
    expect(triggerBox.y + triggerBox.height).toBeLessThanOrEqual(
      sidebarBox.y + sidebarBox.height + 1
    );
  });

  test("Logo 进入新聊天，不创建重复空会话", async ({ page }) => {
    await freshAccount(page, "i12-logo");
    await skipIfChatStorageDisabled(page);
    const sidebar = page.getByTestId("app-sidebar");

    await sidebar.getByRole("link", { name: "搜索" }).click();
    await page.waitForURL("/search");

    await page.getByTestId("app-sidebar").getByRole("link", { name: "BridGes — 新聊天" }).click();
    await page.waitForURL("/");
    await expect(page.getByRole("heading", { name: "有什么可以帮你的？" })).toBeVisible();

    // 最近对话仍为空：不出现任何会话条目或重复空会话
    const homeSidebar = page.getByTestId("app-sidebar");
    await expect(homeSidebar.getByText("还没有对话")).toBeVisible();
    await expect(homeSidebar.locator('a[href^="/chat/"]')).toHaveCount(0);
  });

  test("普通用户侧栏不包含「更多」与旧工作台入口", async ({ page }) => {
    await freshAccount(page, "i12-clean");
    const sidebar = page.getByTestId("app-sidebar");
    for (const label of [
      "更多",
      "学习实验室",
      "科学表达工坊",
      "证据与校验台",
      "多模态科学实验室",
      "领域包",
      "全局科学伙伴",
      "评测与运行中心",
    ]) {
      await expect(sidebar).not.toContainText(label);
    }
  });
});

test.describe("Issue 12 — 收起与恢复", () => {
  test("收起后侧栏隐藏、内容区变宽，刷新与路由切换保持，可键盘恢复", async ({ page }) => {
    await freshAccount(page, "i12-collapse");
    const sidebar = page.getByTestId("app-sidebar");
    const main = page.getByTestId("main-content");
    await expect(sidebar).toBeVisible();
    const widthBefore = (await main.boundingBox())!.width;

    await page.getByTestId("sidebar-collapse").click();
    await expect(page.getByTestId("app-sidebar")).toHaveCount(0);
    await expect(page.getByTestId("sidebar-expand")).toBeVisible();
    expect((await main.boundingBox())!.width).toBeGreaterThan(widthBefore);
    expect(await page.evaluate(() => localStorage.getItem("bridges-sidebar-collapsed"))).toBe("1");

    // 刷新后保持收起
    await page.reload();
    await expect(page.getByTestId("app-sidebar")).toHaveCount(0);
    await expect(page.getByTestId("sidebar-expand")).toBeVisible();

    // 路由切换后保持收起
    await page.goto("/search");
    await expect(page.getByTestId("app-sidebar")).toHaveCount(0);
    await expect(page.getByTestId("sidebar-expand")).toBeVisible();

    // 恢复入口包含新聊天链接
    await expect(page.getByRole("link", { name: "新聊天", exact: true })).toBeVisible();

    await page.getByTestId("sidebar-expand").click();
    await expect(page.getByTestId("app-sidebar")).toBeVisible();
    expect(await page.evaluate(() => localStorage.getItem("bridges-sidebar-collapsed"))).toBe("0");
  });
});

test.describe("Issue 12 — 账户菜单", () => {
  test("底部账户菜单向上弹出，三项顺序固定（GQ-06 无密钥入口）", async ({ page }) => {
    const creds = await freshAccount(page, "i12-menu");
    const trigger = page.getByRole("button", {
      name: new RegExp(`账户菜单：${creds.username}`),
    });
    await trigger.click();
    await expect(page.getByRole("menuitem")).toHaveText([
      "切换账号",
      "个人资料",
      "退出登录",
    ]);

    // 向上弹出：菜单整体位于触发按钮上方
    const menuBox = (await page.getByRole("menu").boundingBox())!;
    const triggerBox = (await trigger.boundingBox())!;
    expect(menuBox.y + menuBox.height).toBeLessThanOrEqual(triggerBox.y + 1);
  });
});

test.describe("Issue 12 — 模块入口与真实空状态", () => {
  test("各模块入口到达稳定路由，未完成能力呈现真实空状态", async ({ page }) => {
    await freshAccount(page, "i12-modules");
    const sidebar = page.getByTestId("app-sidebar");

    const cases: { name: string; url: string; heading: string; emptyText?: string; emptyAction?: string }[] = [
      {
        name: "本地知识库",
        url: "/knowledge-base",
        heading: "本地知识库",
        emptyText: "当前账户还没有知识库材料",
      },
      { name: "学习项目", url: "/account/projects", heading: "学习项目" },
      {
        name: "插件",
        url: "/plugins",
        heading: "插件",
        // Issue 34：插件中心已交付真实页面，空态为真实说明（用户插件空 + 安装入口）
        emptyText: "当前账户还没有用户插件",
        emptyAction: "安装插件",
      },
    ];

    for (const item of cases) {
      await page.getByTestId("app-sidebar").getByRole("link", { name: item.name }).click();
      await page.waitForURL(item.url);
      await expect(page.getByRole("heading", { name: item.heading })).toBeVisible();
      await expect(page.locator("body")).not.toContainText("将在这里呈现");
      if (item.emptyText) {
        // 真实空状态：解释原因 + 可操作下一步（返回新聊天或真实能力入口）。
        // 插件页含 SKILL 与 MCP 两个分区（Issue 35），多空态共存合法，
        // 按文案精确过滤。
        const emptyState = page.getByTestId("state-empty").filter({ hasText: item.emptyText });
        await expect(emptyState).toBeVisible();
        await expect(page.getByRole("button", { name: item.emptyAction ?? "返回新聊天" }).first()).toBeVisible();
      }
    }

    // Issue 25：用户画像已是完整治理页面（非空占位），九类分区面板内的空态
    // 提供「该类别还没有记录」与真实导航的返回新聊天链接。
    await page.getByTestId("app-sidebar").getByRole("link", { name: "用户画像" }).click();
    await page.waitForURL("/account/profile");
    await expect(page.getByRole("heading", { name: "数字分身画像" })).toBeVisible();
    await expect(page.locator("body")).not.toContainText("将在这里呈现");
    // 画像九类分区中未填写的类别各有一个真实空态（多空态共存合法）。
    await expect(
      page.getByTestId("state-empty").filter({ hasText: "该类别还没有记录" }).first()
    ).toBeVisible();
    await expect(page.getByRole("link", { name: "返回新聊天" })).toBeVisible();

    // 返回新聊天是真实导航（画像中心顶部为链接形式）
    await page.getByRole("link", { name: "返回新聊天" }).click();
    await page.waitForURL("/");
    await expect(page.getByRole("heading", { name: "有什么可以帮你的？" })).toBeVisible();
    await expect(sidebar).toBeVisible();
  });

  test("搜索页覆盖空查询引导、无结果与结果跳转", async ({ page }) => {
    await freshAccount(page, "i12-search");
    await skipIfChatStorageDisabled(page);

    await page.goto("/search");
    await expect(page.getByRole("heading", { name: "搜索", exact: true })).toBeVisible();
    // 空查询引导（Issue 24 统一搜索页）
    await expect(page.getByTestId("state-empty")).toContainText(
      "输入关键词，搜索聊天、图片、文档和学习项目"
    );

    // 无结果态
    await page.getByTestId("search-input").fill("绝不存在的关键词xyz");
    await expect(page.getByTestId("state-empty")).toContainText("没有找到");

    // 通过真实 API 建立对话后可检索并跳转（会话标题命中）
    const conversationId = await createConversation(page, "光合作用笔记");
    await page.reload();
    await page.getByTestId("search-input").fill("光合作用");
    // 侧栏最近对话同步出现该会话
    await expect(
      page.getByTestId("app-sidebar").getByRole("link", { name: "光合作用笔记" })
    ).toBeVisible();
    const result = page
      .getByTestId("main-content")
      .locator('[data-result-type="chat"]')
      .first();
    await expect(result).toBeVisible();
    await result.click();
    await page.waitForURL(`/chat/${conversationId}`);
  });

  test("对话列表加载失败显示可重试错误，不伪装成空列表", async ({ page }) => {
    await freshAccount(page, "i12-error");
    let failing = true;
    await page.route("**/api/chat/conversations", async (route) => {
      if (route.request().method() === "GET" && failing) {
        await route.fulfill({
          status: 500,
          contentType: "application/json",
          body: JSON.stringify({
            detail: { error: "server_error", message: "服务暂时不可用，请稍后重试。" },
          }),
        });
        return;
      }
      await route.continue();
    });

    await page.goto("/");
    // 侧栏最近对话：错误 + 重试，不显示空列表文案
    const sidebar = page.getByTestId("app-sidebar");
    await expect(sidebar.getByRole("alert")).toBeVisible();
    await expect(sidebar.getByText("还没有对话")).toHaveCount(0);

    failing = false;
    await sidebar.getByRole("button", { name: "重试" }).click();
    await expect(sidebar.getByText("还没有对话")).toBeVisible();
  });
});

test.describe("Issue 12 — 激活语义与键盘可达", () => {
  test("任一时刻只有一个 aria-current，对话页只标记会话条目", async ({ page }) => {
    await freshAccount(page, "i12-current");
    await skipIfChatStorageDisabled(page);
    const sidebar = page.getByTestId("app-sidebar");

    // 新聊天首页：仅「新聊天」为当前页
    await expect(sidebar.locator('[aria-current="page"]')).toHaveCount(1);
    await expect(
      sidebar.getByRole("link", { name: "新聊天", exact: true })
    ).toHaveAttribute("aria-current", "page");

    // 模块页：仅该模块为当前页（前缀匹配覆盖嵌套路由）
    await sidebar.getByRole("link", { name: "学习项目" }).click();
    await page.waitForURL("/account/projects");
    const projectsSidebar = page.getByTestId("app-sidebar");
    await expect(projectsSidebar.locator('[aria-current="page"]')).toHaveCount(1);
    await expect(
      projectsSidebar.getByRole("link", { name: "学习项目" })
    ).toHaveAttribute("aria-current", "page");

    // 对话页：仅该会话条目为当前页，模块均不标记。
    // 注：Issue 15 起最近对话有意过滤「无标题且无消息」的空草稿会话，
    // 因此这里创建带标题会话，保证它出现在侧边栏以断言激活语义。
    const conversationId = await createConversation(page, "aria 测试会话");
    await page.goto(`/chat/${conversationId}`);
    const chatSidebar = page.getByTestId("app-sidebar");
    await expect(chatSidebar.locator('[aria-current="page"]')).toHaveCount(1);
    await expect(
      chatSidebar.locator(`a[href="/chat/${conversationId}"]`)
    ).toHaveAttribute("aria-current", "page");
    // Logo 从不标记当前页
    await expect(
      chatSidebar.getByRole("link", { name: "BridGes — 新聊天" })
    ).not.toHaveAttribute("aria-current", "page");
  });

  test("键盘按视觉顺序遍历侧栏，焦点可见，账户菜单与收起均可键盘操作", async ({ page }) => {
    const creds = await freshAccount(page, "i12-keyboard");
    // 整页加载后从文档起点开始 Tab（SPA 跳转后浏览器顺序焦点起点不在文档开头）。
    await page.reload();
    const sidebar = page.getByTestId("app-sidebar");
    // 等待会话解析完成、账户菜单渲染，避免 Tab 经过时页脚仍是加载态。
    await expect(sidebar.getByRole("button", { name: /账户菜单：/ })).toBeVisible();

    // 第一个 Tab：跳转主内容链接；随后按视觉顺序经过侧栏控件
    await page.keyboard.press("Tab");
    await expect(page.getByTestId("skip-link")).toBeFocused();

    const focusOrder: { role: "link" | "button"; name: string | RegExp; exact?: boolean }[] = [
      { role: "link", name: "BridGes — 新聊天" },
      { role: "link", name: "搜索" },
      { role: "button", name: "收起侧边栏" },
      { role: "link", name: "新聊天", exact: true },
      { role: "link", name: "本地知识库" },
      { role: "link", name: "学习项目" },
      { role: "link", name: "插件" },
      { role: "link", name: "用户画像" },
      { role: "button", name: new RegExp(`账户菜单：${creds.username}`) },
    ];
    for (const item of focusOrder) {
      await page.keyboard.press("Tab");
      const locator = page.getByRole(item.role, {
        name: item.name,
        exact: item.exact ?? false,
      });
      await expect(locator).toBeFocused();
      // 焦点可见：全局 focus-visible 描边生效
      const outline = await page.evaluate(() => {
        const el = document.activeElement as HTMLElement;
        const style = window.getComputedStyle(el);
        return `${style.outlineStyle} ${style.outlineWidth}`;
      });
      expect(outline).toContain("solid");
    }

    // Enter 打开账户菜单并聚焦首项，Esc 关闭并归还焦点
    await page.keyboard.press("Enter");
    await expect(page.getByRole("menuitem").first()).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("menu")).toHaveCount(0);
    await expect(
      sidebar.getByRole("button", { name: new RegExp(`账户菜单：${creds.username}`) })
    ).toBeFocused();

    // 键盘收起：焦点回到页面后，恢复入口键盘可达
    await page.reload();
    await page.keyboard.press("Tab"); // skip link
    await page.keyboard.press("Tab"); // Logo
    await page.keyboard.press("Tab"); // 搜索
    await page.keyboard.press("Tab"); // 收起侧边栏
    await expect(page.getByTestId("sidebar-collapse")).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("app-sidebar")).toHaveCount(0);

    // 收起按钮卸载后焦点直接落在恢复按钮上；展开后焦点回到收起按钮
    await expect(page.getByTestId("sidebar-expand")).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("app-sidebar")).toBeVisible();
    await expect(page.getByTestId("sidebar-collapse")).toBeFocused();
  });

  test("长用户名不遮挡收起按钮或溢出侧栏", async ({ page }) => {
    const digits = `${Date.now()}`.slice(-8);
    const username = `超长用户名甲乙丙丁戊己庚辛${digits}`;
    await signUp(page, username, `${digits}9@qq.com`, PASSWORD);

    const sidebar = page.getByTestId("app-sidebar");
    await expect(sidebar).toBeVisible();
    const sidebarBox = (await sidebar.boundingBox())!;
    const trigger = sidebar.getByRole("button", { name: /账户菜单：/ });
    await expect(trigger).toBeVisible();
    const triggerBox = (await trigger.boundingBox())!;
    const collapseBox = (await page.getByTestId("sidebar-collapse").boundingBox())!;

    // 账户菜单完全在侧栏宽度内（长用户名被截断，不横向溢出）
    expect(triggerBox.x).toBeGreaterThanOrEqual(sidebarBox.x);
    expect(triggerBox.x + triggerBox.width).toBeLessThanOrEqual(
      sidebarBox.x + sidebarBox.width + 1
    );
    // 收起按钮始终位于账户菜单上方且可见，不被推出视口
    await expect(page.getByTestId("sidebar-collapse")).toBeVisible();
    expect(collapseBox.y + collapseBox.height).toBeLessThanOrEqual(triggerBox.y);
  });
});
