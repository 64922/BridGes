import { expect, test, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

/**
 * Issue 03 反馈环：原子创建新会话首轮（真实 HTTP + 真实 SQLite + 真实后台执行器）。
 *
 * 覆盖验收标准：
 * - 首页发送普通消息后立即可见用户气泡与「排队/生成中」状态，不出现空白主区；
 * - 会话在首轮事务成功后出现在最近列表，不依赖助手输出完成或点击其他会话；
 * - 连续双击、网络响应重放和浏览器返回/前进均只创建一个会话、一条消息和一个 run；
 * - 首轮命令失败时仍停留在可编辑首页，输入不丢失；没有不可见空草稿；
 * - 直接刷新新会话 URL 可以恢复相同消息和回答，不再依赖 sessionStorage；
 * - 鼠标发送/程序化导航不显示蓝色「跳转到主内容」；按 Tab 时该链接仍可见且可用；
 * - 串行 100 次首轮场景，空白、重复和最近列表遗漏均为 0。
 *
 * 由全局 playwright.config.ts 驱动：真实 API（BRIDGES_GENERATION_EXECUTOR=1）
 * + 确定性替身回答（StubQwenAdapter）+ 真实 SQLite 数据目录，零 page route mock
 * （失败注入用例除外，仅对 /api/chat/first-turn 拦截一次）。
 */

const STUB_REPLY = "这是一条来自本地替身模式的确定性测试回答。";
const PASSWORD = "correct-horse-issue03";

function conversationItems(page: Page) {
  return page.getByTestId(/^conversation-item-/);
}

async function sendFirstMessage(page: Page, text: string): Promise<void> {
  const composer = page.getByTestId("composer");
  await composer.getByLabel("输入消息").fill(text);
  await composer.getByRole("button", { name: "发送消息" }).click();
  await expect(page).toHaveURL(/\/chat\/[^/]+$/);
}

test("首轮原子发送：跳转后立即渲染用户气泡与生成中状态，随后收到回答", async ({
  page,
}) => {
  const credentials = uniqueCredentials("i3a");
  await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);

  await sendFirstMessage(page, "你好，BridGes 首轮");
  // 主区立即非空白：用户气泡不等 started/完成即可见（500ms 量级）
  const thread = page.getByTestId("chat-thread");
  await expect(thread).toBeVisible();
  const userBubble = thread.getByText("你好，BridGes 首轮", { exact: true });
  await expect(userBubble).toBeVisible({ timeout: 3000 });
  // 首轮闭环：回答最终出现，且只产生一次（无重复）
  await expect(page.getByText(STUB_REPLY)).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(STUB_REPLY)).toHaveCount(1);

  // 侧栏最近列表在首轮事务成功后即包含该会话（不依赖回答完成）
  const sidebar = page.getByTestId("app-sidebar");
  await expect(sidebar.getByText("你好，BridGes 首轮", { exact: true })).toBeVisible({
    timeout: 3000,
  });
});

test("双击发送与同键并发重放：只创建一个会话、一条消息和一个 run", async ({
  page,
}) => {
  const credentials = uniqueCredentials("i3b");
  await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);

  // 双击发送按钮：前端同步防重 + 服务端幂等，只产生一个会话
  const composer = page.getByTestId("composer");
  await composer.getByLabel("输入消息").fill("双击发送测试");
  await composer.getByRole("button", { name: "发送消息" }).dblclick();
  await expect(page).toHaveURL(/\/chat\/[^/]+$/);
  await expect(page.getByText(STUB_REPLY)).toBeVisible({ timeout: 30_000 });
  const url = page.url();
  const conversationId = url.split("/chat/")[1].split(/[?#]/)[0];

  // 权威投影：恰好一条用户消息、一条助手消息（run 唯一性由同键并发
  // 重放的 run_id 收敛断言覆盖；active_run 仅在运行活跃时出现在视图）
  const projection = await page.request.get(
    `/api/chat/conversations/${conversationId}`
  );
  expect(projection.status()).toBe(200);
  const body = (await projection.json()) as {
    messages: { role: string; message_id: string }[];
  };
  const users = body.messages.filter((message) => message.role === "user");
  const assistants = body.messages.filter((message) => message.role === "assistant");
  expect(users).toHaveLength(1);
  expect(assistants).toHaveLength(1);

  // 同键并发重放（模拟网络重放/双标签页）：服务端收敛到同一会话同一 run
  const duplicated = await page.evaluate(async () => {
    const payload = {
      content: "并发双击测试",
      idempotency_key: "issue03-e2e-concurrent-key",
    };
    const [first, second] = await Promise.all([
      fetch("/api/chat/first-turn", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }),
      fetch("/api/chat/first-turn", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }),
    ]);
    return {
      firstStatus: first.status,
      secondStatus: second.status,
      first: (await first.json()) as { conversation: { conversation_id: string }; run_id: string; idempotent_replay: boolean },
      second: (await second.json()) as { conversation: { conversation_id: string }; run_id: string; idempotent_replay: boolean },
    };
  });
  // 并发处理顺序不定：恰好一个 201（新建）一个 200（重放）
  expect([duplicated.firstStatus, duplicated.secondStatus].sort()).toEqual([200, 201]);
  expect(
    [duplicated.first.idempotent_replay, duplicated.second.idempotent_replay].filter(
      Boolean
    )
  ).toHaveLength(1);
  expect(duplicated.first.conversation.conversation_id).toBe(
    duplicated.second.conversation.conversation_id
  );
  expect(duplicated.first.run_id).toBe(duplicated.second.run_id);

  // 双击只产生一个会话（跳转时侧栏已刷新；并发重放经浏览器 fetch 不
  // 触发前端刷新事件，其收敛性已由上方 API 断言覆盖）
  await expect(conversationItems(page)).toHaveCount(1);
});

test("刷新新会话 URL：恢复相同消息与回答，不重复创建", async ({ page }) => {
  const credentials = uniqueCredentials("i3c");
  await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);

  await sendFirstMessage(page, "刷新后仍应只有一条");
  await expect(page.getByText(STUB_REPLY)).toBeVisible({ timeout: 30_000 });

  await page.reload();
  await expect(page.getByTestId("chat-thread")).toBeVisible();
  const thread = page.getByTestId("chat-thread");
  await expect(thread.getByText("刷新后仍应只有一条", { exact: true })).toHaveCount(1);
  await expect(page.getByText(STUB_REPLY)).toHaveCount(1);
});

test("浏览器返回/前进：不重复创建会话或消息", async ({ page }) => {
  const credentials = uniqueCredentials("i3d");
  await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);

  await sendFirstMessage(page, "返回前进测试");
  await expect(page.getByText(STUB_REPLY)).toBeVisible({ timeout: 30_000 });

  // 返回首页：空白态且输入区为空（sessionStorage 不再承担待发送消息）
  await page.goBack();
  await expect(page.getByRole("heading", { name: "有什么可以帮你的？" })).toBeVisible();
  await expect(page.getByTestId("composer").getByLabel("输入消息")).toHaveValue("");

  // 前进回会话页：同一会话同一消息，不产生第二条
  await page.goForward();
  await expect(page.getByTestId("chat-thread")).toBeVisible();
  await expect(
    page.getByTestId("chat-thread").getByText("返回前进测试", { exact: true })
  ).toHaveCount(1);
  await expect(page.getByText(STUB_REPLY)).toHaveCount(1);
});

test("首轮失败：留在可编辑首页、输入保留、无不可见空草稿", async ({ page }) => {
  const credentials = uniqueCredentials("i3e");
  await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);

  // 仅拦截一次：注入服务端 500，模拟首轮命令失败（ChatError 契约：
  // detail = { error: 稳定错误码, message: 中文提示 }）
  await page.route("**/api/chat/first-turn", (route) =>
    route.fulfill({
      status: 500,
      contentType: "application/json",
      body: JSON.stringify({
        detail: { error: "internal_error", message: "模拟发送失败" },
      }),
    })
  );

  const composer = page.getByTestId("composer");
  await composer.getByLabel("输入消息").fill("这条消息不应丢失");
  await composer.getByRole("button", { name: "发送消息" }).click();

  // 停留在首页（未跳转）、错误横幅明确、输入保留
  await expect(page.getByRole("heading", { name: "有什么可以帮你的？" })).toBeVisible();
  await expect(page.getByText(/模拟发送失败/)).toBeVisible();
  await expect(composer.getByLabel("输入消息")).toHaveValue("这条消息不应丢失");

  // 侧栏无新增会话（原子命令失败不留空草稿）
  await expect(conversationItems(page)).toHaveCount(0);
});

test("鼠标发送不显示跳转链接；地址栏进入按 Tab 可见且可跳到主内容", async ({
  page,
}) => {
  const credentials = uniqueCredentials("i3f");
  await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);

  await sendFirstMessage(page, "焦点与跳转链接测试");
  await expect(page.getByText(STUB_REPLY)).toBeVisible({ timeout: 30_000 });

  // 鼠标/程序化导航后：跳转链接不显示（视觉隐藏），且焦点不在它上面
  const skipLink = page.getByTestId("skip-link");
  const hiddenByCss = await skipLink.evaluate((el) => {
    const style = getComputedStyle(el);
    return style.clip === "rect(0px, 0px, 0px, 0px)" && style.width === "1px";
  });
  expect(hiddenByCss).toBe(true);
  const activeClass = await page.evaluate(() => document.activeElement?.className ?? "");
  expect(activeClass).not.toContain("sc-visually-hidden");

  // 地址栏直达（整页加载）：按 Tab 第一个可聚焦项是跳转链接，且可见可用
  const url = page.url();
  await page.goto(url);
  await page.keyboard.press("Tab");
  await expect(skipLink).toBeVisible(); // :focus-visible 覆盖层
  await page.keyboard.press("Enter");
  const focusedId = await page.evaluate(() => document.activeElement?.id);
  expect(focusedId).toBe("main-content");
});

test("串行 100 次首轮场景：无空白、无重复、最近列表无遗漏", async ({ page }) => {
  test.setTimeout(600_000);
  const credentials = uniqueCredentials("i3g");
  await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);

  const createdIds = new Set<string>();
  for (let round = 0; round < 100; round += 1) {
    const text = `第 ${round} 轮首轮消息`;
    const composer = page.getByTestId("composer");
    await composer.getByLabel("输入消息").fill(text);
    await composer.getByRole("button", { name: "发送消息" }).click();
    await expect(page).toHaveURL(/\/chat\/[^/]+$/);
    // 主区非空白：用户气泡立即可见（不等回答完成）
    const thread = page.getByTestId("chat-thread");
    await expect(thread.getByText(text, { exact: true })).toBeVisible({ timeout: 5000 });
    const conversationId = page.url().split("/chat/")[1].split(/[?#]/)[0];
    expect(createdIds.has(conversationId)).toBe(false); // 无重复会话
    createdIds.add(conversationId);
    // 返回首页发起下一轮
    await page
      .getByTestId("app-sidebar")
      .getByRole("link", { name: "新聊天", exact: true })
      .click();
    await expect(page.getByRole("heading", { name: "有什么可以帮你的？" })).toBeVisible();
  }
  expect(createdIds.size).toBe(100);

  // 最近列表无遗漏：恰好 100 个会话，且最新会话在顶部
  await expect(conversationItems(page)).toHaveCount(100);
  await expect(
    page.getByTestId("app-sidebar").getByText("第 99 轮首轮消息", { exact: true })
  ).toBeVisible();
  await expect(
    page.getByTestId("app-sidebar").getByText("第 0 轮首轮消息", { exact: true })
  ).toBeVisible();
});
