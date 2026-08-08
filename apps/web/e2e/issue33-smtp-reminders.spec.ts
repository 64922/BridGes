import { expect, test } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

/**
 * Issue 33：QQ SMTP 任务提醒端到端测试。
 *
 * 真实后端 + 本地假 SMTP/IMAP 服务器（scripts/e2e_mail_server.py，
 * 授权码固定 e2etestauthcode33）：配置授权码 → 自发自收验证（真实
 * 网关经假服务器收发）→ 自然语言解析预览（时区/首次执行/重复规则/
 * 主题/正文）→ 确认创建 → 手动补发（真实投递进假服务器邮箱）→
 * 查看投递记录 → 编辑 → 暂停/恢复 → 取消；未验证账户禁止创建；
 * 切换账户不残留前一账户数据。
 */

const AUTH_CODE = "e2etestauthcode33";
const PASSWORD = "correct-horse-33";

async function gotoTasks(page: import("@playwright/test").Page): Promise<void> {
  await page.getByRole("link", { name: "任务安排" }).click();
  await expect(page.getByRole("heading", { name: "任务安排" })).toBeVisible();
}

async function saveAndVerifySmtp(page: import("@playwright/test").Page): Promise<void> {
  await page.getByLabel("QQ 邮箱授权码").fill(AUTH_CODE);
  await page.getByRole("button", { name: "保存并验证" }).click();
  // 后台自发自收验证（真实网关 → 假 SMTP/IMAP）收敛为「已验证」
  await expect(page.getByText("已验证", { exact: true })).toBeVisible({
    timeout: 15_000,
  });
}

async function createReminder(
  page: import("@playwright/test").Page,
  text: string
): Promise<void> {
  await page.getByRole("button", { name: "新建提醒" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("提醒内容").fill(text);
  await dialog.getByRole("button", { name: "解析并预览" }).click();
  await expect(page.getByTestId("reminder-preview")).toBeVisible({ timeout: 10_000 });
  await dialog.getByRole("button", { name: "确认创建" }).click();
  await expect(page.getByRole("dialog")).toBeHidden();
}

test("配置-验证-解析-确认-投递-记录-编辑/取消全流程", async ({ page }) => {
  const credentials = uniqueCredentials("r33");
  await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);
  await gotoTasks(page);

  // 未配置：状态芯片 + 新建按钮禁用（未验证不能启用）
  await expect(page.getByText("未配置", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "新建提醒" })).toBeDisabled();

  // 配置 → 自发自收验证 → 已验证
  await saveAndVerifySmtp(page);
  await expect(page.getByRole("button", { name: "新建提醒" })).toBeEnabled();

  // 自然语言 → 预览（时区/首次执行/重复规则/主题/正文）→ 确认创建
  await page.getByRole("button", { name: "新建提醒" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("提醒内容").fill("明天早上八点提醒我复习 transformer");
  await dialog.getByRole("button", { name: "解析并预览" }).click();
  const preview = page.getByTestId("reminder-preview");
  await expect(preview).toBeVisible({ timeout: 10_000 });
  await expect(preview).toContainText("Asia/Shanghai");
  await expect(preview).toContainText("一次");
  await expect(preview).toContainText("复习 transformer");
  await expect(preview).toContainText("来自 BridGes 提醒");
  await dialog.getByRole("button", { name: "确认创建" }).click();
  await expect(page.getByRole("dialog")).toBeHidden();

  // 提醒卡片出现（主题 + 状态芯片 + 下次执行）
  await expect(page.getByRole("heading", { name: "复习 transformer" })).toBeVisible();
  await expect(page.getByText("启用中", { exact: true })).toBeVisible();
  await expect(page.getByText(/下次执行/)).toBeVisible();

  // 手动补发（真实投递到假邮件服务器）→ 成功后自动展开投递记录。
  // 不重复点击「查看投递记录」：sendNow 完成时已 setExpanded(true)，
  // 与点击 toggle 交错会落在「收起投递记录」上反而折叠（竞态）。
  await page.getByRole("button", { name: "手动补发" }).click();
  await expect(page.getByRole("button", { name: "收起投递记录" })).toBeVisible({
    timeout: 10_000,
  });
  await expect(page.getByText("手动重试·成功")).toBeVisible({ timeout: 10_000 });

  // 编辑：改为每周一和周三下午 3 点半
  await page.getByRole("button", { name: "编辑" }).click();
  const editDialog = page.getByRole("dialog");
  await editDialog.getByLabel("提醒内容").fill("每周一和周三下午3点半提醒我开会");
  await editDialog.getByRole("button", { name: "解析并预览" }).click();
  await expect(page.getByTestId("reminder-preview")).toBeVisible({ timeout: 10_000 });
  await editDialog.getByRole("button", { name: "保存修改" }).click();
  await expect(page.getByRole("heading", { name: "开会" })).toBeVisible();
  await expect(page.getByText("每周一、三")).toBeVisible();

  // 暂停 → 恢复 → 取消
  await page.getByRole("button", { name: "暂停" }).click();
  await expect(page.getByText("已暂停", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "恢复" }).click();
  await expect(page.getByText("启用中", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "取消" }).click();
  await page.getByRole("button", { name: "确认取消" }).click();
  await expect(page.getByText("已取消", { exact: true })).toBeVisible();
  // 投递记录保留（列表仍展开）
  await expect(page.getByText("手动重试·成功")).toBeVisible();
});

test("验证失败给出明确原因与重新验证路径", async ({ page }) => {
  const credentials = uniqueCredentials("r33");
  await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);
  await gotoTasks(page);

  // 错误授权码：假服务器拒绝 → 状态 failed + 中文原因 + 重新验证按钮
  await page.getByLabel("QQ 邮箱授权码").fill("WRONGCODE123456");
  await page.getByRole("button", { name: "保存并验证" }).click();
  await expect(page.getByText("验证失败", { exact: true })).toBeVisible({
    timeout: 15_000,
  });
  // 失败原因框内给出中文原因（授权码无效）与重新验证入口
  await expect(page.getByText(/授权码无效|已失效/)).toBeVisible();
  await expect(page.getByRole("button", { name: "重新验证" })).toBeVisible();
  await expect(page.getByRole("button", { name: "新建提醒" })).toBeDisabled();
});

test("切换账户不残留前一账户数据", async ({ page }) => {
  const first = uniqueCredentials("r33a");
  const second = uniqueCredentials("r33b");
  await signUp(page, first.username, first.qqEmail, PASSWORD);
  await gotoTasks(page);
  await saveAndVerifySmtp(page);
  await createReminder(page, "明天早上八点提醒我复习 transformer");
  await expect(page.getByRole("heading", { name: "复习 transformer" })).toBeVisible();

  // 第二个账户真实注册（API 直连），再从切换器添加并登录
  const registered = await page.request.post("/api/auth/register", {
    data: {
      username: second.username,
      qq_email: second.qqEmail,
      password: PASSWORD,
    },
  });
  expect(registered.status()).toBe(201);
  await page.getByRole("button", { name: /账户菜单/ }).click();
  await page.getByRole("menuitem", { name: "切换账号" }).click();
  await page.getByRole("button", { name: "添加账户" }).click();
  await page.getByLabel("用户名或 QQ 邮箱").fill(second.username);
  await page.getByLabel("密码").fill(PASSWORD);
  await page.getByRole("button", { name: "登录并添加" }).click();
  await expect(page.getByRole("button", { name: /账户菜单：r33b/ })).toBeVisible();

  // 切换后的账户：提醒列表为空、SMTP 未配置、新建禁用
  await gotoTasks(page);
  await expect(page.getByText("还没有提醒")).toBeVisible();
  await expect(page.getByText("未配置", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "复习 transformer" })).toBeHidden();
  await expect(page.getByRole("button", { name: "新建提醒" })).toBeDisabled();
});
