import { expect, test } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

const PASSWORD = "correct-horse-12";

/**
 * Issue 10 → GQ-06 负向回归：账户级百炼密钥用户面已退出公开合同。
 *
 * 原 Issue 10 E2E（录入/显示隐藏/保存/探测/重试/替换/两步删除）随密钥
 * 设置页一起删除，不得只删不验。本文件以负向断言锁定"入口不存在"：
 * 账户菜单严格为三项、设置中心无密钥入口、旧页面与旧 API 一律 404，
 * 且整个桌面应用不再发起 /api/auth/key-settings 请求。
 */

test("账户菜单按顺序且仅包含切换账号、个人资料、退出登录", async ({ page }) => {
  const creds = uniqueCredentials("gq06-menu");
  await signUp(page, creds.username, creds.qqEmail, PASSWORD);

  const trigger = page.getByRole("button", {
    name: new RegExp(`账户菜单：${creds.username}`),
  });
  await trigger.click();
  await expect(page.getByRole("menuitem")).toHaveText([
    "切换账号",
    "个人资料",
    "退出登录",
  ]);
  // 菜单内不存在任何密钥设置入口。
  await expect(page.getByRole("menuitem", { name: /密钥/ })).toHaveCount(0);
});

test("设置中心不再出现密钥设置卡片，个人资料与数据隐私保持可用", async ({ page }) => {
  const creds = uniqueCredentials("gq06-settings");
  await signUp(page, creds.username, creds.qqEmail, PASSWORD);
  await page.goto("/account/settings");

  await expect(page.getByRole("heading", { name: "设置" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "个人资料" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "数据与隐私" })).toBeVisible();
  // 密钥设置、模型连接与能力状态等文案一律不存在。
  await expect(page.getByText(/密钥设置|模型连接|能力状态|百炼/)).toHaveCount(0);
  await expect(page.getByRole("link", { name: /密钥/ })).toHaveCount(0);

  // 个人资料入口仍然可打开。
  await page.getByRole("link", { name: "打开个人资料" }).click();
  await page.waitForURL(/\/account\/settings\/profile$/);
});

test("直接访问旧密钥设置页面返回 404", async ({ page }) => {
  const creds = uniqueCredentials("gq06-404-page");
  await signUp(page, creds.username, creds.qqEmail, PASSWORD);

  await page.goto("/account/settings/keys");
  await expect(page).toHaveURL(/\/account\/settings\/keys$/);
  await expect(page.getByRole("heading", { name: "404", level: 1 })).toBeVisible();
  await expect(page.getByText("This page could not be found.")).toBeVisible();
  // 不保留兼容页面或跳转：内容与设置中心无关。
  await expect(page.getByRole("heading", { name: /密钥设置|模型连接/ })).toHaveCount(0);
});

test("旧密钥 API 全部返回 404，页面不再请求 key-settings", async ({ page }) => {
  const creds = uniqueCredentials("gq06-404-api");
  await signUp(page, creds.username, creds.qqEmail, PASSWORD);

  // 从页面上下文直接探测旧 API：读、写、删除、全量探测与单项重试一律 404。
  const get = await page.request.get("/api/auth/key-settings");
  expect(get.status()).toBe(404);
  const put = await page.request.put("/api/auth/key-settings", {
    data: { key: "sk-gq06-e2e-does-not-exist" },
  });
  expect(put.status()).toBe(404);
  const del = await page.request.delete("/api/auth/key-settings");
  expect(del.status()).toBe(404);
  const probeAll = await page.request.post("/api/auth/key-settings/probes");
  expect(probeAll.status()).toBe(404);
  const retry = await page.request.post("/api/auth/key-settings/probes/chat/retry");
  expect(retry.status()).toBe(404);

  // 正常浏览不再发起任何 key-settings 请求。
  let keySettingsRequests = 0;
  page.on("request", (request) => {
    if (request.url().includes("/api/auth/key-settings")) keySettingsRequests += 1;
  });
  await page.goto("/");
  await page.getByRole("button", { name: /账户菜单：/ }).click();
  await page.getByRole("menuitem", { name: "个人资料" }).click();
  await page.goto("/account/settings");
  expect(keySettingsRequests).toBe(0);
});
