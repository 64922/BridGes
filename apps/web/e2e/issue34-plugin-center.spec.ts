import { expect, test, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

const PASSWORD = "correct-horse-34";

const FIXTURES = "e2e/fixtures/issue34";

async function gotoPlugins(page: Page) {
  await page.getByRole("link", { name: "插件" }).click();
  await expect(page.getByRole("heading", { name: "插件中心" })).toBeVisible();
}

async function installPlugin(page: Page, zipName: string) {
  await page.getByRole("button", { name: "安装插件" }).first().click();
  await page.getByRole("button", { name: "选择 zip 包" }).click();
  await page.getByTestId("plugin-file-input").setInputFiles(`${FIXTURES}/${zipName}`);
  await expect(page.getByTestId("plugin-check-preview")).toBeVisible();
  await page.getByTestId("plugin-confirm-install").click();
  await expect(page.getByTestId("plugin-install-done")).toBeVisible();
  await page.getByRole("button", { name: "完成" }).click();
}

test("内置插件展示固定版本、能力、来源、授权并可启停", async ({ page }) => {
  await signUp(page, uniqueCredentials("p34a").username, uniqueCredentials("p34a").qqEmail, PASSWORD);
  await gotoPlugins(page);

  for (const skillId of ["bridges-pdf", "bridges-documents", "bridges-humanizer"]) {
    const card = page.getByTestId(`builtin-${skillId}`);
    await expect(card).toBeVisible();
    await expect(card.getByText("内置", { exact: true })).toBeVisible();
    await expect(card.getByText(/^v\d+\.\d+\.\d+$/)).toBeVisible();
    await expect(card.getByText("已启用", { exact: true })).toBeVisible();
    await expect(card.getByText("来源", { exact: true })).toBeVisible();
    await expect(card.getByText("授权", { exact: true })).toBeVisible();
    await expect(card.getByText("将接收的数据", { exact: true })).toBeVisible();
  }

  // 停用 PDF → 状态更新
  const pdf = page.getByTestId("builtin-bridges-pdf");
  await pdf.getByRole("button", { name: "停用 PDF 文档解析" }).click();
  await expect(pdf.getByText("已停用", { exact: true })).toBeVisible();
  await pdf.getByRole("button", { name: "启用 PDF 文档解析" }).click();
  await expect(pdf.getByText("已启用", { exact: true })).toBeVisible();
});

test("上传合法包：预览内容清单与数据类别 → 确认安装 → 启停 → 卸载", async ({ page }) => {
  await signUp(page, uniqueCredentials("p34b").username, uniqueCredentials("p34b").qqEmail, PASSWORD);
  await gotoPlugins(page);

  // 上传 → 检查 → 预览
  await page.getByRole("button", { name: "安装插件" }).first().click();
  await page.getByRole("button", { name: "选择 zip 包" }).click();
  await page.getByTestId("plugin-file-input").setInputFiles(`${FIXTURES}/valid-plugin.zip`);
  const preview = page.getByTestId("plugin-check-preview");
  await expect(preview).toBeVisible();
  await expect(preview.getByText("待办整理助手", { exact: true })).toBeVisible();
  await expect(preview.getByText("1.2.3", { exact: true })).toBeVisible();
  await expect(preview.getByText("声明能力", { exact: true })).toBeVisible();
  await expect(preview.getByText("用户粘贴的待办文本", { exact: true })).toBeVisible();
  const fileList = page.getByTestId("plugin-file-list");
  await expect(fileList.getByText("docs/usage.md", { exact: true })).toBeVisible();
  await expect(fileList.getByText("templates/checklist.html", { exact: true })).toBeVisible();
  await expect(fileList.getByText("resources/icon.svg", { exact: true })).toBeVisible();

  // 确认安装
  await page.getByTestId("plugin-confirm-install").click();
  await expect(page.getByTestId("plugin-install-done")).toBeVisible();
  await page.getByRole("button", { name: "完成" }).click();

  // 已安装卡片（含版本与状态）
  const card = page.getByTestId("user-e2e-todo");
  await expect(card).toBeVisible();
  await expect(card.getByText("待办整理助手", { exact: true })).toBeVisible();
  await expect(card.getByText("已启用", { exact: true })).toBeVisible();

  // 停用 → 启用
  await card.getByRole("button", { name: "停用 待办整理助手" }).click();
  await expect(card.getByText("已停用", { exact: true })).toBeVisible();
  await card.getByRole("button", { name: "启用 待办整理助手" }).click();
  await expect(card.getByText("已启用", { exact: true })).toBeVisible();

  // 卸载（确认对话框）
  await card.getByRole("button", { name: "卸载 待办整理助手" }).click();
  await expect(page.getByRole("dialog", { name: "卸载插件" })).toBeVisible();
  await page.getByRole("button", { name: "确认卸载" }).click();
  await expect(card).toBeHidden();
  await expect(page.getByText("当前账户还没有用户插件", { exact: true })).toBeVisible();
});

test("坏包拒绝并给出具体原因，修正后可重新安装", async ({ page }) => {
  await signUp(page, uniqueCredentials("p34c").username, uniqueCredentials("p34c").qqEmail, PASSWORD);
  await gotoPlugins(page);

  // 含脚本的包 → 拒绝 + 原因
  await page.getByRole("button", { name: "安装插件" }).first().click();
  await page.getByRole("button", { name: "选择 zip 包" }).click();
  await page.getByTestId("plugin-file-input").setInputFiles(`${FIXTURES}/script-plugin.zip`);
  const rejected = page.getByTestId("plugin-check-rejected");
  await expect(rejected).toBeVisible();
  await expect(rejected.getByText(/evil\.py.*脚本/)).toBeVisible();

  // 关闭对话框：不留半安装状态
  await page.getByRole("button", { name: "取消" }).click();
  await expect(page.getByTestId("plugin-check-rejected")).toBeHidden();

  // 修正包（同标识）→ 重新安装成功，失败记录被覆盖
  await installPlugin(page, "valid-plugin.zip");
  const card = page.getByTestId("user-e2e-todo");
  await expect(card.getByText("已启用", { exact: true })).toBeVisible();
  await expect(page.getByTestId("user-e2e-todo")).toHaveCount(1);
});

test("缺版本声明的包被拒绝并说明原因", async ({ page }) => {
  await signUp(page, uniqueCredentials("p34d").username, uniqueCredentials("p34d").qqEmail, PASSWORD);
  await gotoPlugins(page);

  await page.getByRole("button", { name: "安装插件" }).first().click();
  await page.getByRole("button", { name: "选择 zip 包" }).click();
  await page.getByTestId("plugin-file-input").setInputFiles(`${FIXTURES}/missing-version.zip`);
  const rejected = page.getByTestId("plugin-check-rejected");
  await expect(rejected).toBeVisible();
  await expect(rejected.getByText(/固定版本（version）/)).toBeVisible();
});

test("两账户完全隔离：B 看不到、不能操作 A 安装的插件", async ({ page }) => {
  const accountA = uniqueCredentials("p34e");
  await signUp(page, accountA.username, accountA.qqEmail, PASSWORD);
  await gotoPlugins(page);
  await installPlugin(page, "valid-plugin.zip");
  await expect(page.getByTestId("user-e2e-todo")).toBeVisible();

  // 第二个账户真实注册（API 直连），再从切换器添加并登录
  const accountB = uniqueCredentials("p34f");
  const registered = await page.request.post("/api/auth/register", {
    data: {
      username: accountB.username,
      qq_email: accountB.qqEmail,
      password: PASSWORD,
    },
  });
  expect(registered.status()).toBe(201);
  await page.getByRole("button", { name: /账户菜单/ }).click();
  await page.getByRole("menuitem", { name: "切换账号" }).click();
  await page.getByRole("button", { name: "添加账户" }).click();
  await page.getByLabel("用户名或 QQ 邮箱").fill(accountB.username);
  await page.getByLabel("密码").fill(PASSWORD);
  await page.getByRole("button", { name: "登录并添加" }).click();
  await expect(page.getByRole("button", { name: new RegExp(`账户菜单：${accountB.username}`) })).toBeVisible();

  // 账户 B：看不到 A 的插件，也无法操作
  await gotoPlugins(page);
  await expect(page.getByText("当前账户还没有用户插件", { exact: true })).toBeVisible();
  await expect(page.getByTestId("user-e2e-todo")).toHaveCount(0);

  // 直接 API 调用同样被拒绝（服务端账户作用域）
  const disabled = await page.request.post("/api/plugins/e2e-todo/disable");
  expect(disabled.status()).toBe(404);
});

test("内置演示：Documents 对上传附件执行真实解析", async ({ page }) => {
  await signUp(page, uniqueCredentials("p34g").username, uniqueCredentials("p34g").qqEmail, PASSWORD);
  await gotoPlugins(page);

  const documents = page.getByTestId("builtin-bridges-documents");
  await documents.getByRole("button", { name: "演示" }).click();
  await expect(page.getByRole("dialog", { name: "演示：Documents 文档解析" })).toBeVisible();
  await page.getByRole("button", { name: "选择附件" }).click();
  await page.getByTestId("plugin-demo-file-input").setInputFiles({
    name: "demo.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("# 演示章节\n\n这是真实解析的文本。\n"),
  });
  const result = page.getByTestId("plugin-demo-result");
  await expect(result).toBeVisible();
  await expect(result.getByText("markdown-v1", { exact: true })).toBeVisible();
  await expect(result).toContainText("这是真实解析的文本");
});

test("humanizer「在聊天中使用」跳转首页并自动打开对话框", async ({ page }) => {
  await signUp(page, uniqueCredentials("p34h").username, uniqueCredentials("p34h").qqEmail, PASSWORD);
  await gotoPlugins(page);

  const humanizer = page.getByTestId("builtin-bridges-humanizer");
  await humanizer.getByRole("button", { name: "在聊天中使用" }).click();
  await expect(page.getByRole("dialog", { name: /文章人味化/ })).toBeVisible();
});

test("纯键盘完成安装、调用与卸载", async ({ page }) => {
  await signUp(page, uniqueCredentials("p34i").username, uniqueCredentials("p34i").qqEmail, PASSWORD);
  await gotoPlugins(page);

  // 键盘导航到「安装插件」按钮并回车打开对话框（focus 为 Tab 序列起点）。
  await page.getByRole("button", { name: "安装插件" }).first().focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog", { name: "安装插件" })).toBeVisible();

  // 对话框打开后焦点自动落在「选择 zip 包」→ 回车打开文件选择器。
  await page.keyboard.press("Enter");
  await page.getByTestId("plugin-file-input").setInputFiles(`${FIXTURES}/valid-plugin.zip`);
  await expect(page.getByTestId("plugin-check-preview")).toBeVisible();

  // 预览态：Tab 一次到「确认安装」→ 回车 → 完成态焦点在「完成」→ 回车。
  await page.keyboard.press("Tab");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("plugin-install-done")).toBeVisible();
  // 完成态内容切换后焦点回到页面背景，Tab 收回「完成」→ 回车。
  await page.keyboard.press("Tab");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("user-e2e-todo")).toBeVisible();

  // 键盘：调用内置能力演示（Documents 真实解析）。
  const documents = page.getByTestId("builtin-bridges-documents");
  await documents.getByRole("button", { name: "演示" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog", { name: "演示：Documents 文档解析" })).toBeVisible();
  await page.keyboard.press("Enter"); // 焦点在「选择附件」
  await page.getByTestId("plugin-demo-file-input").setInputFiles({
    name: "demo.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("# 键盘演示\n"),
  });
  await expect(page.getByTestId("plugin-demo-result")).toBeVisible();
  await page.keyboard.press("Escape"); // 关闭演示对话框（焦点归还）

  // 键盘：聚焦卡片内「停用」→ 回车。
  await page.getByTestId("user-e2e-todo").getByRole("button", { name: "停用 待办整理助手" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("user-e2e-todo").getByText("已停用", { exact: true })).toBeVisible();

  // 键盘：聚焦「卸载」→ 回车 → 确认对话框（焦点在「确认卸载」）→ 回车。
  await page.getByTestId("user-e2e-todo").getByRole("button", { name: "卸载 待办整理助手" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog", { name: "卸载插件" })).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("user-e2e-todo")).toHaveCount(0);
});
