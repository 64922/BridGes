import type { Page } from "@playwright/test";

/**
 * 生成同一运行进程内唯一的用户名与纯数字 QQ 邮箱。
 * QQ 邮箱本地部分必须全为数字，因此用时间戳加随机数的数字组合；
 * 用户名上限 32 字符，数字段控制在 16 位以内。
 */
export function uniqueCredentials(prefix: string): { username: string; qqEmail: string } {
  const digits = `${Date.now()}${Math.floor(Math.random() * 1_000_000)}`.slice(-16);
  return {
    username: `${prefix}-${digits}`,
    qqEmail: `${digits}@qq.com`,
  };
}

// 注意：必填字段的 label 内含 aria-hidden 的必填星号，getByLabel 精确匹配会
// 失败，这里统一使用包含匹配（各页面内标签文本互不冲突）。
export async function signUp(
  page: Page,
  username: string,
  qqEmail: string,
  password: string
): Promise<void> {
  await page.goto("/register");
  await page.getByLabel("用户名").fill(username);
  await page.getByLabel("QQ 邮箱").fill(qqEmail);
  await page.getByLabel("密码").fill(password);
  await page.getByRole("button", { name: "注册" }).click();
  await page.waitForURL("/");
}

export async function signIn(page: Page, identifier: string, password: string): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("用户名或 QQ 邮箱").fill(identifier);
  await page.getByLabel("密码").fill(password);
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForURL("/");
}

export async function signOut(page: Page): Promise<void> {
  await page.getByRole("button", { name: /账户菜单：/ }).click();
  await page.getByRole("menuitem", { name: "退出登录" }).click();
  await page.waitForURL(/\/login/);
}
