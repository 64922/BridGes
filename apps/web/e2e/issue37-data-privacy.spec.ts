import { expect, test, type Page } from "@playwright/test";

import { signIn, signUp, uniqueCredentials } from "./helpers/auth";

/**
 * Issue 37：数据与隐私端到端测试（真实后端）。
 *
 * 覆盖：设置中心入口与导出范围表（确认前可见）；导出全流程（近期密码
 * 再认证 → JSON 附件下载）；备份创建（口令两次输入 + 一致性校验 →
 * .bridgesbackup 下载）；删除账户（密码 + 键入「删除」→ 会话撤销 →
 * 重新登录失败）；恢复（备份 → 修改数据 → 上传备份恢复 → 会话失效 →
 * 重新登录 → 数据回滚）；两账户隔离（删除 A 不影响 B）。
 */

// 本文件测试全部串行：恢复与删除是全局/破坏性操作，与文件内其他测试
// 并行会互相破坏数据（恢复会替换整个数据库）。全量 E2E 在 CI（workers=1）
// 天然串行；本地并行运行本文件与其他 spec 时，恢复测试可能干扰其他文件。
test.describe.configure({ mode: "serial" });

const PASSWORD = "correct-horse-37";
const BACKUP_PASSPHRASE = "e2e-backup-passphrase-37";
const EXPORT_ROUTE = "**/api/data/export";
const BACKUPS_ROUTE = "**/api/data/backups";

async function gotoDataPrivacy(page: Page): Promise<void> {
  await page.goto("/account/settings/data");
  await expect(page.getByRole("heading", { name: "数据与隐私" })).toBeVisible();
}

async function createConversation(
  page: Page,
  title: string
): Promise<string> {
  // 用浏览器内 fetch（携带 HttpOnly 会话 cookie），page.request 不共享 cookie。
  const conversationId = await page.evaluate(async (t: string) => {
    const res = await fetch("/api/chat/conversations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: t, mode: "companion" }),
    });
    if (res.status !== 201) throw new Error(`conversation create ${res.status}`);
    return (await res.json()).conversation_id as string;
  }, title);
  return conversationId;
}

/** 浏览器内 GET 返回当前账户对话标题列表（带会话 cookie）。 */
async function listConversationTitles(page: Page): Promise<string[]> {
  return page.evaluate(async () => {
    const res = await fetch("/api/chat/conversations", { cache: "no-store" });
    if (res.status !== 200) throw new Error(`conversation list ${res.status}`);
    const body = await res.json();
    return (body.conversations ?? []).map(
      (item: { title: string }) => item.title
    );
  });
}

/** 捕获指定 API 的响应状态（blob 下载无法用 download 事件断言）。 */
function captureStatus(
  page: Page,
  routePattern: string,
  status: { value: number }
): void {
  void page.route(routePattern, async (route) => {
    const response = await route.fetch();
    status.value = response.status();
    await route.fulfill({ response });
  });
}

test("设置中心入口与导出范围表（确认前可见）", async ({ page }) => {
  const creds = uniqueCredentials("i37");
  await signUp(page, creds.username, creds.qqEmail, PASSWORD);
  await createConversation(page, "范围表对话");

  // 设置中心入口：数据与隐私卡片 → 链接
  await page.goto("/account/settings");
  await expect(page.getByRole("heading", { name: "数据与隐私" })).toBeVisible();
  await page.getByRole("link", { name: "打开数据与隐私" }).click();
  await page.waitForURL("/account/settings/data");

  // 导出范围表：14 个类别行 + 表头 + 总计行，确认前可见
  await expect(page.getByRole("heading", { name: "导出数据" })).toBeVisible();
  const table = page.getByRole("table");
  await expect(table).toBeVisible();
  await expect(table.getByRole("row")).toHaveCount(16);
  await expect(table).toContainText("对话");
  await expect(table).toContainText("消息");
  await expect(table).toContainText("画像与版本");
  await expect(table).toContainText("学习项目");
  await expect(table).toContainText("提醒与投递记录");
  await expect(table).toContainText("插件清单");
  await expect(table).toContainText("授权记录");
  await expect(table).toContainText("资产清单");
  await expect(table).toContainText("总计");
  await expect(page.getByText(/不包含百炼 Key/)).toBeVisible();
});

test("导出数据：近期密码确认后下载 JSON 附件", async ({ page }) => {
  const creds = uniqueCredentials("i37");
  await signUp(page, creds.username, creds.qqEmail, PASSWORD);
  await createConversation(page, "导出对话");

  await gotoDataPrivacy(page);
  await expect(page.getByRole("table")).toBeVisible();

  // 键盘路径：聚焦「导出数据」按钮 → Enter 打开对话框
  const exportButton = page.getByRole("button", { name: /打开导出确认对话框/ });
  await exportButton.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText("导出");

  const status = { value: 0 };
  captureStatus(page, EXPORT_ROUTE, status);

  await dialog.getByLabel(/当前账户密码/).fill(PASSWORD);
  await dialog.getByRole("button", { name: "导出" }).click();
  await expect(dialog).toBeHidden({ timeout: 15_000 });
  expect(status.value).toBe(200);
});

test("创建备份：口令一致性校验与加密备份下载", async ({ page }) => {
  const creds = uniqueCredentials("i37");
  await signUp(page, creds.username, creds.qqEmail, PASSWORD);
  await gotoDataPrivacy(page);

  await page.getByRole("button", { name: /打开创建备份对话框/ }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();

  // 口令太短 → 内联错误（role=alert；必填字段先填好避免原生校验拦截）
  await dialog.getByLabel(/备份口令/).first().fill("short");
  await dialog.getByLabel(/再次输入备份口令/).fill(BACKUP_PASSPHRASE);
  await dialog.getByLabel(/当前账户密码/).fill(PASSWORD);
  await dialog.getByRole("button", { name: "创建备份" }).click();
  await expect(dialog.getByText(/至少需要 8 个字符/).first()).toBeVisible();

  await dialog.getByLabel(/备份口令/).first().fill(BACKUP_PASSPHRASE);
  await dialog.getByLabel(/再次输入备份口令/).fill(BACKUP_PASSPHRASE + "-x");
  await dialog.getByRole("button", { name: "创建备份" }).click();
  await expect(dialog.getByText(/两次输入的口令不一致/).first()).toBeVisible();

  const status = { value: 0 };
  captureStatus(page, BACKUPS_ROUTE, status);

  await dialog.getByLabel(/再次输入备份口令/).fill(BACKUP_PASSPHRASE);
  await dialog.getByLabel(/当前账户密码/).fill(PASSWORD);
  await dialog.getByRole("button", { name: "创建备份" }).click();
  await expect(dialog).toBeHidden({ timeout: 15_000 });
  expect(status.value).toBe(200);
});

test("删除账户：强确认后会话撤销且无法重新登录", async ({ page }) => {
  const creds = uniqueCredentials("i37");
  await signUp(page, creds.username, creds.qqEmail, PASSWORD);
  await createConversation(page, "删除前的对话");

  await gotoDataPrivacy(page);
  await page.getByRole("button", { name: /打开删除账户确认对话框/ }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText("不可撤销");

  // 错误密码 → 内联错误
  await dialog.getByLabel(/当前账户密码/).fill("wrong-password");
  await dialog.getByTestId("delete-confirmation").fill("删除");
  await dialog.getByRole("button", { name: "删除账户" }).click();
  await expect(dialog.getByLabel(/当前账户密码/)).toHaveAttribute(
    "aria-invalid",
    "true"
  );

  // 正确密码 + 键入「删除」→ 删除成功 → 跳登录
  await dialog.getByLabel(/当前账户密码/).fill(PASSWORD);
  await dialog.getByRole("button", { name: "删除账户" }).click();
  await page.waitForURL(/\/login/, { timeout: 15_000 });

  // 重新登录失败（账户已删除，统一不泄露错误）
  await page.getByLabel("用户名或 QQ 邮箱").fill(creds.username);
  await page.getByLabel("密码").fill(PASSWORD);
  await page.getByRole("button", { name: "登录" }).click();
  await expect(page.getByTestId("error-summary")).toBeVisible();
  await expect(page.getByTestId("error-summary")).not.toContainText("已删除");
});

test("恢复备份：替换数据、会话失效、重新登录后数据回滚", async ({ page }) => {
  const creds = uniqueCredentials("i37");
  await signUp(page, creds.username, creds.qqEmail, PASSWORD);
  await createConversation(page, "备份前的对话");

  // 创建备份（API 层：注册后会话处于近期认证窗口）
  const backupResponse = await page.request.post("/api/data/backups", {
    multipart: { passphrase: BACKUP_PASSPHRASE },
  });
  expect(backupResponse.status()).toBe(200);
  const backupBytes = await backupResponse.body();

  // 修改数据：新增一个对话
  await createConversation(page, "备份后的对话");
  expect(await listConversationTitles(page)).toContain("备份后的对话");

  // 恢复：上传备份 + 口令 + 账户密码 + 键入「恢复」
  await gotoDataPrivacy(page);
  await page.getByRole("button", { name: /打开恢复备份对话框/ }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await dialog.getByTestId("restore-file-input").setInputFiles({
    name: "backup.bridgesbackup",
    mimeType: "application/octet-stream",
    buffer: backupBytes,
  });
  await dialog.getByLabel(/备份口令/).fill(BACKUP_PASSPHRASE);
  await dialog.getByLabel(/当前账户密码/).fill(PASSWORD);
  await dialog.getByTestId("restore-confirmation").fill("恢复");
  await dialog.getByRole("button", { name: "恢复" }).click();

  // 恢复成功后会话失效 → 跳登录
  await page.waitForURL(/\/login/, { timeout: 20_000 });

  // 重新登录 → 数据回滚（只有备份前的对话）
  await signIn(page, creds.username, PASSWORD);
  await expect(page).toHaveURL("/");
  const restoredTitles = await listConversationTitles(page);
  expect(restoredTitles).toContain("备份前的对话");
  expect(restoredTitles).not.toContain("备份后的对话");
});

test("删除一个账户不影响另一账户", async ({ page }) => {
  const credsA = uniqueCredentials("i37a");
  const credsB = uniqueCredentials("i37b");
  await signUp(page, credsA.username, credsA.qqEmail, PASSWORD);
  await createConversation(page, "A 的对话");
  // B 账户先真实注册（API 直连），再从切换器添加并登录
  const registered = await page.request.post("/api/auth/register", {
    data: {
      username: credsB.username,
      qq_email: credsB.qqEmail,
      password: PASSWORD,
    },
  });
  expect(registered.status()).toBe(201);
  await page.getByRole("button", { name: /账户菜单：/ }).click();
  await page.getByRole("menuitem", { name: "切换账号" }).click();
  // 等待设备账户列表加载完成（列表渲染会重挂「添加账户」按钮，提前点击
  // 会因元素 detached 而失败）。
  const accountsLoaded = page.waitForResponse(
    (res) =>
      res.url().includes("/api/auth/device/accounts") && res.status() === 200
  );
  await accountsLoaded;
  await page.getByRole("button", { name: "添加账户" }).click();
  await page.getByLabel("用户名或 QQ 邮箱").fill(credsB.username);
  await page.getByLabel("密码").fill(PASSWORD);
  await page.getByRole("button", { name: "登录并添加" }).click();
  await expect(
    page.getByRole("button", { name: new RegExp(`账户菜单：${credsB.username}`) })
  ).toBeVisible();
  await createConversation(page, "B 的对话");

  // 删除 B
  await gotoDataPrivacy(page);
  await page.getByRole("button", { name: /打开删除账户确认对话框/ }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel(/当前账户密码/).fill(PASSWORD);
  await dialog.getByTestId("delete-confirmation").fill("删除");
  await dialog.getByRole("button", { name: "删除账户" }).click();
  await page.waitForURL(/\/login/, { timeout: 15_000 });

  // A 登录后数据完好
  await signIn(page, credsA.username, PASSWORD);
  const titles = await listConversationTitles(page);
  expect(titles).toContain("A 的对话");
  expect(titles).not.toContain("B 的对话");
});
