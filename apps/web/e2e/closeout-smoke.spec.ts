import { expect, test } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

/**
 * 收尾浏览器纵向切片（issue 01 AC5）：真实 HTTP 链路，零 page route mock。
 *
 * 覆盖：注册 → 建会话 → 上传附件 → 发送（真实 SSE + 确定性替身回答）→
 * 切换会话 → 权威历史轮询。test 环境的模型回答是注册到网关的确定性替身
 * 输出（StubQwenAdapter，Issue 41 门控），因此发送链路真实、回答确定。
 *
 * 本 spec 由收尾专用配置（playwright.closeout.config.ts）驱动：每次运行
 * 全新唯一数据目录与临时端口，串行执行，失败保留 trace 与截图。
 */

const STUB_REPLY = "这是一条来自本地替身模式的确定性测试回答。";
const PASSWORD = "correct-horse-closeout";

test("真实链路：注册-建会话-上传-发送-切换会话-轮询状态", async ({ page }) => {
  // 1) 注册（真实 POST /auth/register + 会话 Cookie）
  const credentials = uniqueCredentials("co1");
  await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);

  // 2) 首页 composer 发送 → 真实创建会话并跳转（POST /chat/conversations +
  //    POST /chat/conversations/:id/messages，SSE 流式回答）
  const composer = page.getByTestId("composer");
  await composer.getByLabel("输入消息").fill("你好，BridGes");
  await composer.getByRole("button", { name: "发送消息" }).click();
  await expect(page).toHaveURL(/\/chat\//);
  await expect(page.getByTestId("chat-thread")).toBeVisible();
  await expect(page.getByText(STUB_REPLY)).toBeVisible({ timeout: 30_000 });

  // 3) 真实上传附件（对话已存在 → 立即真实 POST attachments，无路由 mock）。
  //    隐藏 input 上用 DataTransfer 派发真实 change 事件（Playwright 的
  //    setInputFiles 对 display:none + React onChange 的 input 不触发
  //    合成事件，实测不产生附件状态；DataTransfer 走真实 handler 与真实
  //    XHR 上传链路）。
  await page.getByTestId("composer-file-input").evaluate((el) => {
    const input = el as HTMLInputElement;
    const dataTransfer = new DataTransfer();
    dataTransfer.items.add(
      new File(
        ["收尾 smoke 附件正文（仅测试内容，不进入任何产物）。"],
        "closeout-notes.txt",
        { type: "text/plain" }
      )
    );
    Object.defineProperty(input, "files", {
      configurable: true,
      value: dataTransfer.files,
    });
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await expect(page.getByText("已上传，等待发送")).toBeVisible({ timeout: 20_000 });

  // 4) 携带附件发送：第二条消息 + 第二条确定性回答（真实 SSE + 权威历史）
  await composer.getByLabel("输入消息").fill("这是带附件的消息");
  await composer.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByText(STUB_REPLY)).toHaveCount(2, { timeout: 30_000 });

  // 5) 建第二个会话（真实 API）并切回第一个会话：权威历史经真实 GET
  //    /chat/conversations/:id 轮询恢复两条消息
  await page
    .getByTestId("app-sidebar")
    .getByRole("link", { name: "新聊天", exact: true })
    .click();
  // 等待新聊天首页完全就绪（避免过渡期旧页面 composer 与输入动作竞态）
  await expect(page.getByRole("heading", { name: "有什么可以帮你的？" })).toBeVisible();
  const composerHome = page.getByTestId("composer");
  await composerHome.getByLabel("输入消息").fill("第二个对话");
  await composerHome.getByRole("button", { name: "发送消息" }).click();
  await expect(page).toHaveURL(/\/chat\//);
  await expect(page.getByText(STUB_REPLY)).toHaveCount(1, { timeout: 30_000 });

  // 切回第一个会话：最近对话按 updated_at 倒序，第一条消息的会话在末尾；
  // 点击后真实 GET /chat/conversations/:id 恢复权威历史（两条回答）
  const items = page.getByTestId(/^conversation-item-/);
  await expect(items).toHaveCount(2);
  await items.last().click();
  await expect(page.getByTestId("chat-thread")).toBeVisible();
  await expect(page.getByText(STUB_REPLY)).toHaveCount(2, { timeout: 30_000 });

  // 6) 轮询状态：权威历史里的两条消息均为已完成终态（done），无流式残留
  const thread = page.getByTestId("chat-thread");
  await expect(thread.getByText("你好，BridGes", { exact: true })).toBeVisible();
  await expect(thread.getByText("这是带附件的消息", { exact: true })).toBeVisible();
  await expect(page.getByText("正在生成回答")).toHaveCount(0);
});
