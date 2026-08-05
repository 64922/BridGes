import { expect, test, type Page } from "@playwright/test";

import { signOut, signUp, uniqueCredentials } from "./helpers/auth";

const PASSWORD = "correct-horse-35";

const FIXTURES = "e2e/fixtures/issue35";

async function gotoPlugins(page: Page) {
  await page.getByRole("link", { name: "插件" }).click();
  await expect(page.getByRole("heading", { name: "插件中心" })).toBeVisible();
}

async function installMcp(page: Page, yamlName: string) {
  await page.getByTestId("mcp-install-open").click();
  await page.getByRole("button", { name: "选择文件" }).click();
  await page.getByTestId("mcp-descriptor-input").setInputFiles(`${FIXTURES}/${yamlName}`);
  await expect(page.getByTestId("mcp-install-preview")).toBeVisible();
  await page.getByTestId("mcp-confirm-install").click();
  await expect(page.getByTestId("mcp-install-done")).toBeVisible();
  await page.getByRole("button", { name: "完成" }).click();
  await expect(page.getByTestId("mcp-notice")).toBeVisible();
}

test("上传 MCP 描述：权限清单逐项预览 → 确认安装 → 真实调用 → 调用统计", async ({ page }) => {
  await signUp(page, uniqueCredentials("m35a").username, uniqueCredentials("m35a").qqEmail, PASSWORD);
  await gotoPlugins(page);

  // 空态
  await expect(page.getByText("当前账户还没有 MCP 服务器")).toBeVisible();

  // 安装回显 MCP：权限预览表格逐项展示
  await page.getByTestId("mcp-install-open").click();
  await page.getByRole("button", { name: "选择文件" }).click();
  await page.getByTestId("mcp-descriptor-input").setInputFiles(`${FIXTURES}/valid-echo.yaml`);
  const preview = page.getByTestId("mcp-install-preview");
  await expect(preview).toBeVisible();
  await expect(preview.getByText("回显演示", { exact: true })).toBeVisible();
  await expect(preview.getByText("1.0.0", { exact: true })).toBeVisible();
  // 权限逐项：网络域名/可读目录/可写目录/外部命令/数据类别/敏感操作
  for (const label of ["网络域名", "可读目录", "可写目录", "外部命令", "数据类别", "敏感操作"]) {
    await expect(preview.locator("th", { hasText: label })).toBeVisible();
  }
  await expect(preview.getByText("无（默认拒绝）").first()).toBeVisible();
  await expect(preview.getByText("当前消息文本", { exact: true })).toBeVisible();
  await page.getByTestId("mcp-confirm-install").click();
  await expect(page.getByTestId("mcp-install-done")).toBeVisible();
  await page.getByRole("button", { name: "完成" }).click();

  // 卡片：状态、版本、完整性、来源
  const card = page.getByTestId("mcp-bridges-echo");
  await expect(card).toBeVisible();
  await expect(card.getByText("运行中", { exact: true })).toBeVisible();
  await expect(card.getByText("1.0.0", { exact: true })).toBeVisible();
  await expect(card.getByText("完整性已锁定", { exact: true })).toBeVisible();
  await expect(card.getByText("来源：local", { exact: true })).toBeVisible();

  // 权限展开
  await card.getByRole("button", { name: /权限清单/ }).click();
  const perms = page.getByTestId("mcp-permissions-bridges-echo");
  await expect(perms.getByText("当前消息文本", { exact: true })).toBeVisible();
  await expect(perms.getByText("无（默认拒绝）").first()).toBeVisible();

  // 真实调用
  await card.getByTestId("mcp-invoke-bridges-echo").click();
  const invokeDialog = page.getByTestId("dialog");
  await invokeDialog.getByTestId("mcp-tool-input").fill("echo");
  await invokeDialog.getByTestId("mcp-slice-input").fill("E2E 授权的数据切片");
  await invokeDialog.getByRole("button", { name: "调用", exact: true }).click();
  const invokeResult = invokeDialog.getByTestId("mcp-invoke-result");
  await expect(invokeResult).toBeVisible();
  await expect(invokeResult.getByText("调用成功", { exact: true })).toBeVisible();
  await expect(invokeResult.getByText("E2E 授权的数据切片")).toBeVisible();
  await invokeDialog.getByRole("button", { name: "关闭" }).click();

  // 调用统计更新
  await expect(card.getByText("1", { exact: true })).toBeVisible();
  await expect(card.getByText("成功", { exact: true })).toBeVisible();

  // 停用 / 启用
  await card.getByRole("button", { name: "停用", exact: true }).click();
  await expect(card.getByText("已停用", { exact: true })).toBeVisible();
  await card.getByRole("button", { name: "启用", exact: true }).click();
  await expect(card.getByText("运行中", { exact: true })).toBeVisible();
});

test("敏感操作确认：展示目标与影响 → 确认后仅本次执行；拒绝后安全终止", async ({ page }) => {
  await signUp(page, uniqueCredentials("m35b").username, uniqueCredentials("m35b").qqEmail, PASSWORD);
  await gotoPlugins(page);
  await installMcp(page, "valid-note.yaml");

  const card = page.getByTestId("mcp-bridges-note");
  await expect(card.getByText("运行中", { exact: true })).toBeVisible();
  await expect(card.getByText(/写 1/)).toBeVisible();

  // 拒绝路径：敏感确认对话框展示目标与影响
  await card.getByTestId("mcp-invoke-bridges-note").click();
  const invokeDialog = page.getByTestId("dialog");
  await invokeDialog.getByTestId("mcp-tool-input").fill("note");
  await invokeDialog.getByTestId("mcp-input-field").fill(JSON.stringify({ path: "C:\\e2e-mcp-notes\\denied.txt" }));
  await invokeDialog.getByTestId("mcp-slice-input").fill("应被拒绝的笔记");
  await invokeDialog.getByRole("button", { name: "调用", exact: true }).click();
  const confirmDialog = page.getByTestId("mcp-sensitive-target");
  await expect(confirmDialog).toBeVisible();
  await expect(confirmDialog.getByText("C:\\e2e-mcp-notes\\denied.txt")).toBeVisible();
  await expect(page.getByText(/确认仅对本次调用有效/)).toBeVisible();
  await page.getByTestId("mcp-sensitive-deny").click();
  await expect(page.getByTestId("mcp-notice")).toContainText("拒绝");
  await expect(card.getByText("已拒绝", { exact: true })).toBeVisible();
  // 关闭调用对话框（遮罩会挡住卡片操作）
  await invokeDialog.getByRole("button", { name: "关闭" }).click();

  // 确认路径：写入成功
  await card.getByTestId("mcp-invoke-bridges-note").click();
  await expect(page.getByTestId("dialog").getByTestId("mcp-tool-input")).toBeVisible();
  await invokeDialog.getByTestId("mcp-tool-input").fill("note");
  await expect(invokeDialog.getByTestId("mcp-tool-input")).toHaveValue("note");
  await invokeDialog.getByTestId("mcp-input-field").fill(JSON.stringify({ path: "C:\\e2e-mcp-notes\\approved.txt" }));
  await invokeDialog.getByTestId("mcp-slice-input").fill("经确认的笔记内容");
  await invokeDialog.getByRole("button", { name: "调用", exact: true }).click();
  await expect(page.getByTestId("mcp-sensitive-target")).toBeVisible();
  await page.getByTestId("mcp-sensitive-approve").click();
  await expect(page.getByTestId("mcp-notice")).toContainText("已确认执行");
  await expect(card.getByText("成功", { exact: true })).toBeVisible();
});

test("坏描述拒绝并展示具体原因；修正后可重装", async ({ page }) => {
  await signUp(page, uniqueCredentials("m35c").username, uniqueCredentials("m35c").qqEmail, PASSWORD);
  await gotoPlugins(page);

  // 缺版本描述 → 拒绝原因（直接上传坏描述）
  await page.getByTestId("mcp-install-open").click();
  await page.getByRole("button", { name: "选择文件" }).click();
  await page.getByTestId("mcp-descriptor-input").setInputFiles({
    name: "bad-version.yaml",
    mimeType: "application/yaml",
    buffer: Buffer.from(
      "---\nmcp_id: bridges-bad\nname: 坏描述\nversion: latest\nsource: local\ncommand:\n  - python\n  - -m\n  - bridges.mcp.servers.echo\ndata_categories:\n  - current_message_text\n---\n",
      "utf-8"
    ),
  });
  const rejected = page.getByTestId("mcp-install-rejected");
  await expect(rejected).toBeVisible();
  await expect(rejected.getByText(/未锁定/)).toBeVisible();
  await page.getByRole("button", { name: "取消" }).click();

  // 取消后无残留
  await expect(page.getByText("当前账户还没有 MCP 服务器")).toBeVisible();

  // 修正后安装成功
  await page.getByTestId("mcp-install-open").click();
  await page.getByRole("button", { name: "选择文件" }).click();
  await page.getByTestId("mcp-descriptor-input").setInputFiles(`${FIXTURES}/valid-echo.yaml`);
  await expect(page.getByTestId("mcp-install-preview")).toBeVisible();
  await page.getByTestId("mcp-confirm-install").click();
  await expect(page.getByTestId("mcp-install-done")).toBeVisible();
  await page.getByRole("button", { name: "完成" }).click();
  await expect(page.getByTestId("mcp-bridges-echo")).toBeVisible();
});

test("两账户完全隔离：B 看不到 A 安装的 MCP", async ({ page }) => {
  const credentialsA = uniqueCredentials("m35d");
  await signUp(page, credentialsA.username, credentialsA.qqEmail, PASSWORD);
  await gotoPlugins(page);
  await installMcp(page, "valid-echo.yaml");

  // 登出并注册账户 B
  await signOut(page);
  const credentialsB = uniqueCredentials("m35e");
  await signUp(page, credentialsB.username, credentialsB.qqEmail, PASSWORD);
  await gotoPlugins(page);
  await expect(page.getByText("当前账户还没有 MCP 服务器")).toBeVisible();
  await expect(page.getByTestId("mcp-bridges-echo")).toHaveCount(0);

  // B 直接调 API 也 404
  const response = await page.request.post("/api/mcp/bridges-echo/invoke", {
    data: { tool: "echo", input: {}, data_slice: { text: "x" } },
  });
  expect(response.status()).toBe(404);
});

test("撤权：移除权限后新调用使用新清单，敏感权限移除终止运行", async ({ page }) => {
  await signUp(page, uniqueCredentials("m35f").username, uniqueCredentials("m35f").qqEmail, PASSWORD);
  await gotoPlugins(page);
  await installMcp(page, "valid-note.yaml");

  const card = page.getByTestId("mcp-bridges-note");
  // 撤掉写目录与敏感操作
  await card.getByRole("button", { name: "权限", exact: true }).click();
  const revokeGroups = page.getByTestId("mcp-revoke-groups");
  await expect(revokeGroups).toBeVisible();
  // 关闭"可写目录"与"敏感操作"两个开关
  const writeRow = revokeGroups.locator("label", { hasText: "可写目录" });
  await writeRow.locator('input[type="checkbox"]').uncheck();
  const sensitiveRow = revokeGroups.locator("label", { hasText: "敏感操作" });
  await sensitiveRow.locator('input[type="checkbox"]').uncheck();
  await page.getByRole("button", { name: "保存权限" }).click();
  await expect(page.getByTestId("mcp-notice")).toContainText("权限已更新");

  // 新调用：write_file 被拒（不在允许清单）
  await card.getByTestId("mcp-invoke-bridges-note").click();
  const invokeDialog = page.getByTestId("dialog");
  await invokeDialog.getByTestId("mcp-tool-input").fill("note");
  await invokeDialog.getByTestId("mcp-input-field").fill(JSON.stringify({ path: "C:\\e2e-mcp-notes\\revoked.txt" }));
  await invokeDialog.getByTestId("mcp-slice-input").fill("撤权后的写入");
  await invokeDialog.getByRole("button", { name: "调用", exact: true }).click();
  const invokeResult = page.getByTestId("mcp-invoke-result");
  await expect(invokeResult).toBeVisible();
  await expect(invokeResult.getByText("不在允许清单内")).toBeVisible();
  await page.getByRole("button", { name: "关闭" }).click();
});

test("卸载：确认后删除记录并清空列表", async ({ page }) => {
  await signUp(page, uniqueCredentials("m35g").username, uniqueCredentials("m35g").qqEmail, PASSWORD);
  await gotoPlugins(page);
  await installMcp(page, "valid-echo.yaml");

  const card = page.getByTestId("mcp-bridges-echo");
  await card.getByRole("button", { name: "卸载", exact: true }).click();
  await page.getByRole("button", { name: "确认卸载" }).click();
  await expect(page.getByTestId("mcp-notice")).toContainText("已卸载");
  await expect(page.getByText("当前账户还没有 MCP 服务器")).toBeVisible();
});

test("纯键盘完成：安装 → 调用 → 敏感确认 → 撤权 → 卸载", async ({ page }) => {
  await signUp(page, uniqueCredentials("m35h").username, uniqueCredentials("m35h").qqEmail, PASSWORD);
  await gotoPlugins(page);

  // 键盘安装 note（含敏感操作确认路径）
  await page.getByTestId("mcp-install-open").focus();
  await page.keyboard.press("Enter");
  await page.getByRole("button", { name: "选择文件" }).focus();
  await page.keyboard.press("Enter");
  await page.getByTestId("mcp-descriptor-input").setInputFiles(`${FIXTURES}/valid-note.yaml`);
  await expect(page.getByTestId("mcp-install-preview")).toBeVisible();
  await page.getByTestId("mcp-confirm-install").focus();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("mcp-install-done")).toBeVisible();
  await page.getByRole("button", { name: "完成" }).focus();
  await page.keyboard.press("Enter");

  // 键盘调用 → 敏感确认对话框 → 确认执行（仅本次）
  const card = page.getByTestId("mcp-bridges-note");
  await card.getByTestId("mcp-invoke-bridges-note").focus();
  await page.keyboard.press("Enter");
  const invokeDialog = page.getByTestId("dialog");
  await invokeDialog.getByTestId("mcp-tool-input").fill("note");
  await invokeDialog.getByTestId("mcp-input-field").fill(JSON.stringify({ path: "C:\\e2e-mcp-notes\\keyboard.txt" }));
  await invokeDialog.getByTestId("mcp-slice-input").focus();
  await page.keyboard.type("键盘敏感写入");
  await invokeDialog.getByRole("button", { name: "调用", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("mcp-sensitive-target")).toBeVisible();
  await page.getByTestId("mcp-sensitive-approve").focus();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("mcp-notice")).toContainText("已确认执行");
  await invokeDialog.getByRole("button", { name: "关闭" }).focus();
  await page.keyboard.press("Enter");

  // 键盘撤权：移除可写目录与敏感操作
  await card.getByRole("button", { name: "权限", exact: true }).focus();
  await page.keyboard.press("Enter");
  const revokeGroups = page.getByTestId("mcp-revoke-groups");
  await expect(revokeGroups).toBeVisible();
  await revokeGroups.locator("label", { hasText: "可写目录" }).locator('input[type="checkbox"]').focus();
  await page.keyboard.press("Space");
  await revokeGroups.locator("label", { hasText: "敏感操作" }).locator('input[type="checkbox"]').focus();
  await page.keyboard.press("Space");
  await page.getByRole("button", { name: "保存权限" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("mcp-notice")).toContainText("权限已更新");

  // 键盘卸载
  await card.getByRole("button", { name: "卸载", exact: true }).focus();
  await page.keyboard.press("Enter");
  await page.getByRole("button", { name: "确认卸载" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByText("当前账户还没有 MCP 服务器")).toBeVisible();
});
