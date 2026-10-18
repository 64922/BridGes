import { expect, test } from "@playwright/test";

import { signIn, signOut, signUp, uniqueCredentials } from "./helpers/auth";
import {
  attachFile,
  backToNewChat,
  chooseStudyMode,
  configureScript,
  expectComposerInsideViewport,
  expectNoHorizontalOverflow,
  getConversation,
  messageInput,
  PASSWORD,
  screenshot,
  sendFromHome,
  sendInConversation,
  thread,
  VIEWPORTS,
  waitForSidebar,
} from "./helpers/issue16";

/**
 * Issue 16：连续对话呈现的真实链路桌面验收。
 *
 * 只经正式 HTTP（认证、首轮、执行器、SSE、持久化）驱动，不使用 page.route。
 * 模型正文由 test 环境的 ScriptedChatStreamAdapter 控制分块，以便稳定覆盖
 * 停止/继续与刷新重开；外部证据来源与真实供应商不在本文件断言范围内。
 */

test.describe.configure({ timeout: 300_000 });

test("真实链路：多轮、停止继续、刷新、会话切换与账户隔离", async ({ page }, testInfo) => {
  testInfo.setTimeout(240_000);
  const accountA = uniqueCredentials("i16-life");
  await signUp(page, accountA.username, accountA.qqEmail, PASSWORD);
  await expect(page.getByTestId("new-chat-home")).toBeVisible();
  await page.emulateMedia({ reducedMotion: "reduce" });

  await configureScript(page, {
    chunks: ["第一轮正文第一段。", "\n\n第一轮正文第二段。"],
    delay_ms: 20,
    match: "验收：第一轮连续正文",
  });
  const firstId = await sendFromHome(page, "验收：第一轮连续正文");
  await expect(thread(page)).toContainText("第一轮正文第一段。", { timeout: 60_000 });
  await expect(thread(page)).toContainText("第一轮正文第二段。");
  await expect(page.getByTestId("public-process").last()).toHaveAttribute("data-status", "done");
  await expect(page.getByTestId("public-process-details")).toHaveCount(0);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  const transitionDuration = await page
    .getByTestId("public-process-toggle")
    .evaluate((el) => getComputedStyle(el).transitionDuration);
  expect(transitionDuration).toMatch(/0\.001ms|1e-06s/);

  // 中文输入法组合期间 Enter 只确认候选，不发送消息。
  await configureScript(page, {
    chunks: ["输入法提交成功。"],
    delay_ms: 10,
    match: "验收：输入法提交",
  });
  const input = messageInput(page);
  await input.fill("输入法草稿");
  await input.dispatchEvent("compositionstart");
  await input.press("Enter");
  await expect(input).toHaveValue(/输入法草稿/);
  await expect
    .poll(async () => {
      const body = await getConversation(page, firstId);
      return body.messages.some(
        (entry) => entry.role === "user" && entry.content === "输入法草稿"
      );
    })
    .toBe(false);
  await input.dispatchEvent("compositionend");
  await input.fill("验收：输入法提交");
  await input.press("Enter");
  await expect(thread(page)).toContainText("输入法提交成功。", { timeout: 60_000 });

  // 停止：只保留已到块；继续：从原消息产生新尝试，最终正文完整且不重复。
  await configureScript(page, {
    chunks: ["停止前的半段内容。", "停止后的完整内容。"],
    delay_ms: 1_000,
    match: "验收：停止与继续",
  });
  await sendInConversation(page, "验收：停止与继续");
  await expect(thread(page).locator('p[role="status"]')).toContainText("正在生成回答");
  await expect(page.getByTestId("public-process").last()).toHaveAttribute(
    "data-status",
    "streaming"
  );
  await expect(thread(page)).toContainText("停止前的半段内容。", { timeout: 60_000 });
  await page.getByRole("button", { name: "停止生成" }).click();
  await expect(thread(page).locator('p[role="status"]')).toContainText("已停止生成");
  await expect(page.getByTestId("delivery-status").last()).toHaveAttribute(
    "data-outcome",
    "cancelled"
  );
  await expect(thread(page)).toContainText("本轮已停止");
  await expect(thread(page)).not.toContainText("停止后的完整内容。");
  await page.waitForTimeout(1_500);
  await expect(thread(page)).not.toContainText("停止后的完整内容。");

  await configureScript(page, {
    chunks: ["停止后的完整内容。"],
    match: "验收：停止与继续",
  });
  await page.getByRole("button", { name: "继续生成" }).click();
  await expect(thread(page)).toContainText("停止后的完整内容。", { timeout: 60_000 });
  await expect
    .poll(async () => {
      const body = await getConversation(page, firstId);
      const assistants = body.messages.filter((entry) => entry.role === "assistant");
      return assistants.at(-1)?.content ?? "";
    })
    .toContain("停止后的完整内容。");

  // 刷新后重开：历史正文与默认折叠都保持。
  await page.reload();
  await expect(thread(page)).toContainText("停止后的完整内容。", { timeout: 60_000 });
  await expect(page.getByTestId("public-process-details")).toHaveCount(0);
  await expect(page.getByRole("dialog")).toHaveCount(0);

  // 会话切换：两个会话各自只显示本轮内容。
  await backToNewChat(page);
  await configureScript(page, {
    chunks: ["第二个会话的回答。"],
    match: "验收：第二个会话",
  });
  const secondId = await sendFromHome(page, "验收：第二个会话");
  await expect(thread(page)).toContainText("第二个会话的回答。", { timeout: 60_000 });
  await page
    .getByTestId(`conversation-item-${firstId}`)
    .getByRole("link")
    .click();
  await expect(page).toHaveURL(new RegExp(firstId));
  await expect(thread(page)).toContainText("停止后的完整内容。", { timeout: 60_000 });
  await expect(thread(page)).not.toContainText("第二个会话的回答。");

  // 账户隔离：B 看不到 A 的会话；重新登录 A 后两个会话恢复。
  await signOut(page);
  const accountB = uniqueCredentials("i16-other");
  await signUp(page, accountB.username, accountB.qqEmail, PASSWORD);
  await expect(page.getByTestId("new-chat-home")).toBeVisible();
  await expect(page.getByTestId(/^conversation-item-/)).toHaveCount(0);
  await signOut(page);
  await signIn(page, accountA.username, PASSWORD);
  await expect(page.getByTestId(`conversation-item-${firstId}`)).toBeVisible({
    timeout: 60_000,
  });
  await expect(page.getByTestId(`conversation-item-${secondId}`)).toBeVisible();

  // 三个桌面视口：在主会话上留下最终证据。
  await page.getByTestId(`conversation-item-${firstId}`).getByRole("link").click();
  await expect(page).toHaveURL(new RegExp(firstId));
  await expect(thread(page)).toContainText("停止后的完整内容。", { timeout: 60_000 });
  await expect(page.getByTestId("new-chat-home")).toHaveCount(0);
  await waitForSidebar(page);
  for (const viewport of VIEWPORTS) {
    await page.setViewportSize(viewport);
    await expectComposerInsideViewport(page);
    await expectNoHorizontalOverflow(page);
    await screenshot(page, `lifecycle-${viewport.width}x${viewport.height}`);
  }
});

test("真实链路：附件预览与学习详情默认关闭", async ({ page }, testInfo) => {
  testInfo.setTimeout(240_000);
  const account = uniqueCredentials("i16-doc");
  await signUp(page, account.username, account.qqEmail, PASSWORD);

  await attachFile(
    page,
    "验收笔记.txt",
    "text/plain",
    Buffer.from("桌面验收附件正文：用于核对真实上传、解析与预览。", "utf-8")
  );
  await configureScript(page, {
    chunks: ["附件已经收到。"],
    match: "验收：附件消息",
  });
  await sendFromHome(page, "验收：附件消息");
  await expect(thread(page)).toContainText("附件已经收到。", { timeout: 60_000 });
  await expect(page.getByTestId(/^ingestion-status-(queued|processing)$/)).toHaveCount(0, {
    timeout: 120_000,
  });
  await expect(
    page.getByTestId(/^ingestion-status-(ready|none|permission)$/).first()
  ).toBeVisible({ timeout: 120_000 });

  const attachmentTrigger = page.getByTestId(/^attachment-open-/).first();
  await expect(attachmentTrigger).toBeVisible();
  await attachmentTrigger.click();
  const attachmentPanel = page.getByTestId("attachment-viewer");
  await expect(attachmentPanel).toBeVisible();
  await expect(page.getByTestId("attachment-viewer-text")).toContainText(
    "桌面验收附件正文"
  );
  await expect(page.getByTestId("composer")).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await screenshot(page, "attachment-panel-1280x720");
  await page.keyboard.press("Escape");
  await expect(attachmentPanel).toHaveCount(0);
  await expect(attachmentTrigger).toBeFocused();

  // 学习模式：详情默认关闭；打开不遮断输入；Esc 关闭并把焦点还给触发器。
  await backToNewChat(page);
  await chooseStudyMode(page);
  await attachFile(
    page,
    "书页-1.png",
    "image/png",
    Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==",
      "base64"
    )
  );
  await page.getByLabel("输入消息").fill("验收：本节书页，请开始预习");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page).toHaveURL(/\/chat\/[A-Za-z0-9_-]+/, { timeout: 60_000 });
  await expect(page.getByTestId("study-status-bar")).toBeVisible({ timeout: 120_000 });
  await expect(page.getByTestId("study-details-panel")).toHaveCount(0);
  await expect(page.getByTestId("public-process-details")).toHaveCount(0);
  const studyTrigger = page.getByTestId("study-details-open");
  await studyTrigger.click();
  await expect(page.getByTestId("study-details-panel")).toBeVisible();
  await expect(messageInput(page)).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await screenshot(page, "study-panel-1280x720");
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("study-details-panel")).toHaveCount(0);
  await expect(studyTrigger).toBeFocused();

  await configureScript(page, {
    chunks: ["学习追问已收到。"],
    match: "验收：学习模式追问",
  });
  await messageInput(page).fill("验收：学习模式追问");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(thread(page)).toContainText("学习追问已收到。", { timeout: 60_000 });
  const currentId = page.url().match(/\/chat\/([A-Za-z0-9_-]+)/)?.[1];
  expect(currentId, `未从 URL 取得会话标识：${page.url()}`).toBeTruthy();
  await expect
    .poll(
      async () => {
        const body = await getConversation(page, currentId!);
        return body.messages
          .filter((entry) => entry.role === "user")
          .map((entry) => entry.content);
      },
      { timeout: 30_000 }
    )
    .toContain("验收：学习模式追问");
});
