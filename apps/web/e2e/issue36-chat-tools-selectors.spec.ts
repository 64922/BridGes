import { expect, test, type Page } from "@playwright/test";

import { signOut, signUp, uniqueCredentials } from "./helpers/auth";

/**
 * Issue 36 — 集成聊天工具、学习项目与插件选择。
 *
 * 覆盖：两种对话模式「+」菜单统一六入口顺序（上传/论文搜索/人味化/
 * 生涯规划/选择学习项目/选择已启用插件，图片与视频为既有能力入口）；
 * 空白对话恰好三张建议卡（论文搜索/文章人味化/生涯规划）含原创图标与
 * 键盘路径；插件选择器可用集合=已安装且启用（停用后从集合移除）；选中
 * MCP 插件 chip 持续显示并可发起真实调用（真实消息流 + 真实 invoke）；
 * 敏感操作挂起 → 确认 → 结果写回消息投影；刷新后选择与结果不丢失；
 * 项目选择改变来源面板且可清除；切换账户不残留。
 *
 * 真实后端（sqlite）+ 真实 MCP 进程（echo/note，与 issue35 同一基建）；
 * GQ-06 后测试环境以确定性适配器放行消息发送，不再依赖能力探测钩子。
 */

const PASSWORD = "correct-horse-36";
const FIXTURES = "e2e/fixtures/issue35";

const SIX_ENTRIES = [
  "上传文件/图片",
  "论文搜索",
  "文章人味化",
  "生涯规划助手",
  "选择学习项目",
  "选择已启用插件",
];

async function installMcpViaApi(page: Page, yamlName: string) {
  const file = await import("node:fs").then((fs) =>
    fs.readFileSync(`${FIXTURES}/${yamlName}`, "utf-8")
  );
  const response = await page.request.post("/api/mcp/install", {
    headers: { "X-Bridges-Filename": yamlName },
    data: file,
  });
  expect(response.ok()).toBeTruthy();
}

async function createConversation(page: Page): Promise<string> {
  const response = await page.request.post("/api/chat/conversations", {
    data: {},
  });
  expect(response.ok()).toBeTruthy();
  const body = (await response.json()) as { conversation_id: string };
  return body.conversation_id;
}

async function openMenu(page: Page) {
  await page.getByTestId("composer").getByRole("button", { name: "更多功能" }).click();
  return page.getByRole("menu", { name: "更多功能" });
}

async function expectSixEntriesInOrder(menu: ReturnType<Page["getByRole"]>) {
  const labels = await menu.getByRole("menuitem").allTextContents();
  expect(labels.slice(0, 6)).toEqual(SIX_ENTRIES);
  // 清单六入口之后是既有能力入口（图片/视频生成），不是未实现占位
  expect(labels.slice(6)).toEqual(["图片生成", "视频生成"]);
}

test("两种对话模式统一六入口顺序；建议卡恰好三张且键盘可触发", async ({ page }) => {
  await signUp(page, uniqueCredentials("m36a").username, uniqueCredentials("m36a").qqEmail, PASSWORD);

  // 日常陪伴模式：六入口统一顺序
  const menu = await openMenu(page);
  await expect(menu).toBeVisible();
  await expectSixEntriesInOrder(menu);
  await page.keyboard.press("Escape");

  // 学习模式：同样顺序（菜单不随模式变化）
  await page.getByTestId("mode-toggle").getByRole("button", { name: "学习模式" }).click();
  const studyMenu = await openMenu(page);
  await expectSixEntriesInOrder(studyMenu);
  await page.keyboard.press("Escape");

  // 建议卡：恰好三张，名称与原创图标齐全
  const cards = page.getByTestId("suggestion-cards");
  await expect(cards.getByRole("button")).toHaveCount(3);
  for (const label of ["论文搜索", "文章人味化", "生涯规划助手"]) {
    const card = cards.getByRole("button", { name: label });
    await expect(card).toBeVisible();
    await expect(card.locator("svg").first()).toBeVisible();
  }
  // 键盘路径：聚焦论文搜索卡 → Enter → 输入区预填结构化意图（真实消息前缀）
  const input = page.getByTestId("composer").getByLabel("输入消息");
  await cards.getByRole("button", { name: "论文搜索" }).focus();
  await page.keyboard.press("Enter");
  await expect(input).toHaveValue(/^论文搜索：/);
  // 键盘路径：人味化卡 → Enter → 真实任务对话框（不伪造结果）
  await cards.getByRole("button", { name: "文章人味化" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog", { name: /文章人味化/ })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  // 键盘路径：生涯规划卡 → Enter → 真实任务对话框
  await cards.getByRole("button", { name: "生涯规划助手" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog", { name: /生涯规划助手/ })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("插件选择器真实选择 → chip 持续显示 → MCP 真实调用成功 → 刷新恢复", async ({ page }) => {
  const credentials = uniqueCredentials("m36b");
  await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);
  await installMcpViaApi(page, "valid-echo.yaml");
  const conversationId = await createConversation(page);
  await page.goto(`/chat/${conversationId}`);
  await expect(page.getByTestId("chat-thread")).toBeVisible();

  // 打开选择器：可用集合 = 已安装且启用（回显演示）
  const menu = await openMenu(page);
  await menu.getByRole("menuitem", { name: "选择已启用插件" }).click();
  const picker = page.getByRole("dialog", { name: "选择已启用插件" });
  await expect(picker).toBeVisible();
  await expect(page.getByTestId("plugin-picker-item-bridges-echo")).toBeVisible();
  // 数据类别披露（发送前可查看插件数据披露并取消不需要的授权）
  await expect(page.getByTestId("plugin-picker-categories-bridges-echo")).toContainText(
    "当前消息文本"
  );
  // 勾选并确认 → 选择随对话持久化
  await page.getByTestId("plugin-picker-item-bridges-echo").click();
  await page.getByTestId("plugin-picker-confirm").click();
  await expect(page.getByTestId("composer-selected-plugin-bridges-echo")).toBeVisible();
  await expect(page.getByTestId("composer-selected-plugin-bridges-echo")).toContainText("回显演示");

  // chip「调用」按钮 → 真实调用对话框（工具名 + 参数 + 数据切片披露）
  await page.getByTestId("composer-invoke-plugin-bridges-echo").click();
  const invokeDialog = page.getByRole("dialog", { name: /调用 MCP 插件/ });
  await expect(invokeDialog).toBeVisible();
  await invokeDialog.getByTestId("mcp-invoke-tool").fill("echo");
  await invokeDialog.getByTestId("mcp-invoke-input").fill('{"问题": "你好"}');
  await invokeDialog.getByTestId("mcp-invoke-slice").fill("E2E 授权的切片内容");
  await invokeDialog.getByTestId("mcp-invoke-submit").click();

  // 真实 invoke：消息流出现成功结果卡（结果摘要含授权切片）
  const callCard = page.getByTestId("mcp-call-card");
  await expect(callCard).toBeVisible({ timeout: 15_000 });
  await expect(callCard.getByTestId("mcp-call-status")).toHaveText("调用成功");
  await expect(callCard.getByTestId("mcp-call-result")).toContainText("E2E 授权的切片内容");

  // 刷新：选择与运行结果都不丢失（随对话持久化）
  await page.reload();
  await expect(page.getByTestId("chat-thread")).toBeVisible();
  await expect(page.getByTestId("composer-selected-plugin-bridges-echo")).toBeVisible();
  await expect(page.getByTestId("mcp-call-card")).toBeVisible();
  await expect(page.getByTestId("mcp-call-status")).toHaveText("调用成功");
});

test("敏感操作挂起 → 确认执行；停用后立即从可用集合移除", async ({ page }) => {
  const credentials = uniqueCredentials("m36c");
  await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);
  await installMcpViaApi(page, "valid-note.yaml");
  const conversationId = await createConversation(page);
  await page.goto(`/chat/${conversationId}`);
  await expect(page.getByTestId("chat-thread")).toBeVisible();

  // 选择 note 插件
  const menu = await openMenu(page);
  await menu.getByRole("menuitem", { name: "选择已启用插件" }).click();
  await page.getByTestId("plugin-picker-item-bridges-note").click();
  await page.getByTestId("plugin-picker-confirm").click();
  await expect(page.getByTestId("composer-selected-plugin-bridges-note")).toBeVisible();

  // 调用 note 工具（写文件敏感操作）→ 消息卡挂起确认
  await page.getByTestId("composer-invoke-plugin-bridges-note").click();
  const invokeDialog = page.getByRole("dialog", { name: /调用 MCP 插件/ });
  await invokeDialog.getByTestId("mcp-invoke-tool").fill("note");
  await invokeDialog.getByTestId("mcp-invoke-input").fill('{"path": "C:\\\\e2e-mcp-notes\\\\issue36.txt"}');
  await invokeDialog.getByTestId("mcp-invoke-submit").click();

  const callCard = page.getByTestId("mcp-call-card");
  await expect(callCard.getByTestId("mcp-call-status")).toHaveText("等待敏感操作确认", {
    timeout: 15_000,
  });
  await expect(callCard.getByTestId("mcp-call-confirmation")).toContainText("写入文件");
  // 确认执行（仅本次）
  await callCard.getByTestId("mcp-call-approve").click();
  await expect(callCard.getByTestId("mcp-call-status")).toHaveText("调用成功");

  // 停用 → 可用集合立即移除 → 刷新后 chip 消失
  const disable = await page.request.post("/api/mcp/bridges-note/disable");
  expect(disable.ok()).toBeTruthy();
  await page.reload();
  await expect(page.getByTestId("chat-thread")).toBeVisible();
  await expect(page.getByTestId("composer-selected-plugin-bridges-note")).toHaveCount(0);
  // 选择器里也不再列出已停用服务器
  const menu2 = await openMenu(page);
  await menu2.getByRole("menuitem", { name: "选择已启用插件" }).click();
  await expect(page.getByTestId("plugin-picker-item-bridges-note")).toHaveCount(0);
  await page.keyboard.press("Escape");

  // 卸载（另一条 AC7 路径）：卸载后同样立即从可用集合与选择中移除
  const uninstall = await page.request.delete("/api/mcp/bridges-note");
  expect(uninstall.ok()).toBeTruthy();
  await page.reload();
  await expect(page.getByTestId("chat-thread")).toBeVisible();
  await expect(page.getByTestId("composer-selected-plugin-bridges-note")).toHaveCount(0);
});

test("项目选择改变来源面板且可清除；切换账户不残留", async ({ page }) => {
  const credentials = uniqueCredentials("m36d");
  await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);
  // 创建学习项目与归属对话
  const projectResponse = await page.request.post("/api/learning-projects", {
    data: { name: "量子计算入门" },
  });
  expect(projectResponse.ok()).toBeTruthy();
  const project = (await projectResponse.json()) as { project_id: string };
  const conversationResponse = await page.request.post("/api/chat/conversations", {
    data: { project_id: project.project_id },
  });
  expect(conversationResponse.ok()).toBeTruthy();
  const conversation = (await conversationResponse.json()) as { conversation_id: string };
  await page.goto(`/chat/${conversation.conversation_id}`);
  await expect(page.getByTestId("chat-thread")).toBeVisible();

  // 来源面板显示当前项目归属
  await expect(page.getByTestId("source-layer-project")).toContainText("项目：量子计算入门");
  // 选择器可清除项目（chip 关闭按钮）
  await page.getByTestId("composer-learning-project-chip").getByRole("button", { name: /清除/ }).click();
  await expect(page.getByTestId("source-layer-project")).toContainText("当前项目 未归属");

  // 切换账户：B 看不到 A 的会话（404），B 首页无任何残留选择
  await page.goto("/");
  await signOut(page);
  await signUp(page, uniqueCredentials("m36e").username, uniqueCredentials("m36e").qqEmail, PASSWORD);
  await page.goto(`/chat/${conversation.conversation_id}`);
  await expect(page.getByText("对话不存在或没有访问权限")).toBeVisible();
  await page.goto("/");
  await expect(page.getByTestId("composer-plugin-chips")).toHaveCount(0);
});
