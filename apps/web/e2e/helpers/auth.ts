import type { Page } from "@playwright/test";

export async function signUp(page: Page, email: string, password: string): Promise<void> {
  await page.goto("/register");
  await page.getByLabel("邮箱").fill(email);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByLabel("确认密码").fill(password);
  await page.getByLabel("我已阅读并同意服务条款和隐私政策").check();
  await page.getByRole("button", { name: "注册" }).click();
  await page.waitForURL("/account");
}

export async function signIn(page: Page, email: string, password: string): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("邮箱").fill(email);
  await page.getByLabel("密码").fill(password);
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForURL("/account");
}

export async function signOut(page: Page): Promise<void> {
  await page.getByRole("button", { name: "退出" }).click();
  await page.waitForURL("/login");
}
