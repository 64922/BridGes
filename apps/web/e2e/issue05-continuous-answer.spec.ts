import { expect, test, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

/**
 * 工单 05：普通回答形成连续可阅读正文。
 *
 * 使用 test 环境的脚本化聊天适配器，经真实 API、后台执行器、SQLite 与
 * SSE 链路回放一份多段 Markdown 回答；同一份持久化内容分别经过流式窗口、
 * 刷新后的历史读取和复制路径。脚本化输出只用于确定性验证渲染与恢复机制，
 * 不代表真实模型效果。
 */

const PASSWORD = "correct-horse-issue05";
const API_PORT = process.env.API_PORT || "8000";
const API_ORIGIN = `http://127.0.0.1:${API_PORT}`;
const MATCH = "连续正文验收";

const LONG_PARAGRAPH =
  "这是一段用于验证连续阅读和滚动位置的较长说明，保持用户上滚后不被拉回底部。".repeat(
    24
  );
const LONG_CODE_LINE = `print("${"x".repeat(160)}")`;
const LONG_LINK = `https://example.com/${"a".repeat(160)}`;

const CHUNKS = [
  "## 连续正文验收\n\n第一段包含 **加粗",
  `** 与行内代码 \`x = 1\`。\n\n${LONG_PARAGRAPH}\n\n- 列表一\n- 列表二\n\n`,
  `\`\`\`python\nprint('hello')\n${LONG_CODE_LINE}\n`,
  `\`\`\`\n\n| 项目 | 值 | 说明 | 来源 | 备注 |\n| --- | --- | --- | --- | --- |\n| A | 1 | ${"b".repeat(
    90
  )} | 本地 | 无 |\n\n`,
  `[长链接](${LONG_LINK})\n\n<script>window.__markdownXss = 1</script>\n\n[危险链接](javascript:alert(1))\n\n最后一段。`,
];
const FULL_TEXT = CHUNKS.join("");

interface ApiMessage {
  message_id: string;
  role: "user" | "assistant";
  status: string;
  content: string;
}

interface ConversationView {
  messages: ApiMessage[];
}

async function configureScript(page: Page): Promise<void> {
  const response = await page.request.post(
    `${API_ORIGIN}/_test/chat-stream-script`,
    { data: { match: MATCH, chunks: CHUNKS, delay_ms: 900 } }
  );
  expect(response.ok()).toBeTruthy();
}

async function getLastAssistant(
  page: Page,
  conversationId: string
): Promise<ApiMessage> {
  const response = await page.request.get(
    `/api/chat/conversations/${conversationId}`
  );
  expect(response.ok()).toBeTruthy();
  const conversation = (await response.json()) as ConversationView;
  const assistants = conversation.messages.filter(
    (message) => message.role === "assistant"
  );
  expect(assistants.length).toBeGreaterThan(0);
  return assistants[assistants.length - 1];
}

async function sendFromHome(page: Page, text: string): Promise<string> {
  const composer = page.getByTestId("composer");
  await composer.getByLabel("输入消息").fill(text);
  await composer.getByRole("button", { name: "发送消息" }).click();
  await expect(page).toHaveURL(/\/chat\/[^/]+$/);
  return page.url().split("/chat/")[1].split(/[?#]/)[0];
}

async function installProbes(page: Page): Promise<void> {
  await page.addInitScript(() => {
    (window as unknown as { __markdownXss?: number }).__markdownXss = 0;
    (window as unknown as { __copiedText?: string }).__copiedText = undefined;
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      get: () => ({
        writeText: async (text: string) => {
          (window as unknown as { __copiedText?: string }).__copiedText = text;
        },
      }),
    });
  });
}

async function copyAssistantBody(page: Page): Promise<string> {
  const toolbar = page.getByRole("toolbar", { name: "消息操作" }).last();
  await toolbar.getByRole("button", { name: "复制", exact: true }).click();
  await expect(toolbar.getByText("已复制", { exact: true })).toBeVisible();
  const copied = await page.evaluate(
    () => (window as unknown as { __copiedText?: string }).__copiedText ?? ""
  );
  expect(copied.length).toBeGreaterThan(0);
  return copied;
}

test.describe.configure({ mode: "serial" });

test("同一份多段回答在流式与历史读取中连续、一致且安全", async ({
  page,
}) => {
  await installProbes(page);
  const credentials = uniqueCredentials("i5");
  await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);
  await configureScript(page);

  const conversationId = await sendFromHome(page, MATCH);
  const thread = page.getByTestId("chat-thread");

  await expect(
    thread.getByRole("heading", { level: 2, name: "连续正文验收" })
  ).toBeVisible({ timeout: 30_000 });
  await expect(thread.getByText("列表一")).toBeVisible({ timeout: 30_000 });

  // 用户上滚后，后续流式片段不擅自把阅读位置拉回底部。
  await thread.evaluate((element) => {
    element.scrollTop = 0;
  });
  await expect(thread.getByRole("table")).toBeVisible({ timeout: 30_000 });
  await page.waitForTimeout(300);
  expect(await thread.evaluate((element) => element.scrollTop)).toBeLessThan(50);

  // 流式更新不抢焦点，也不把正文放进 live region。
  const composerInput = page.getByTestId("composer").getByLabel("输入消息");
  await composerInput.focus();
  await expect(composerInput).toBeFocused();
  const liveText = await thread.getByRole("status").first().textContent();
  expect(liveText ?? "").not.toContain("第一段");

  await expect(thread.getByText("最后一段。")).toBeVisible({
    timeout: 30_000,
  });
  await expect(composerInput).toBeFocused();

  await expect
    .poll(async () => (await getLastAssistant(page, conversationId)).status, {
      timeout: 30_000,
    })
    .toBe("done");

  const persisted = await getLastAssistant(page, conversationId);
  expect(persisted.content).toBe(FULL_TEXT);
  expect(await copyAssistantBody(page)).toBe(FULL_TEXT);

  // 历史路径：刷新后继续以同一正文渲染，不重复段落、不丢换行或标点。
  await page.reload();
  await expect(
    thread.getByRole("heading", { level: 2, name: "连续正文验收" })
  ).toBeVisible({ timeout: 30_000 });
  await expect(thread.getByText("最后一段。")).toBeVisible();
  expect(await copyAssistantBody(page)).toBe(FULL_TEXT);

  await page.goto("/");
  await page.goto(`/chat/${conversationId}`);
  await expect(
    thread.getByRole("heading", { level: 2, name: "连续正文验收" })
  ).toBeVisible({ timeout: 30_000 });
  await expect(thread.getByText("最后一段。")).toBeVisible();
  expect(await copyAssistantBody(page)).toBe(FULL_TEXT);

  // 用户文本仍是普通气泡；助手正文没有统一大卡片。
  await expect(page.getByTestId("user-message-bubble")).toContainText(MATCH);
  const assistantStyle = await page
    .getByTestId("assistant-message")
    .last()
    .evaluate((element) => {
      const style = getComputedStyle(element);
      return {
        backgroundColor: style.backgroundColor,
        borderTopWidth: style.borderTopWidth,
        borderLeftWidth: style.borderLeftWidth,
      };
    });
  expect(assistantStyle.backgroundColor).toBe("rgba(0, 0, 0, 0)");
  expect(assistantStyle.borderTopWidth).toBe("0px");
  expect(assistantStyle.borderLeftWidth).toBe("0px");

  const listStyle = await thread
    .locator("ul")
    .first()
    .evaluate((element) => getComputedStyle(element).listStyleType);
  expect(listStyle).toBe("disc");

  // 代码与表格在自身范围内滚动；长链接换行；主阅读列不横向撑破。
  const preMetrics = await thread.locator("pre").first().evaluate((element) => ({
    scrollWidth: element.scrollWidth,
    clientWidth: element.clientWidth,
  }));
  expect(preMetrics.scrollWidth).toBeGreaterThan(preMetrics.clientWidth);
  const tableMetrics = await thread
    .locator("table")
    .first()
    .evaluate((element) => {
      const wrapper = element.parentElement;
      return {
        overflowX: wrapper ? getComputedStyle(wrapper).overflowX : "",
        scrollWidth: wrapper?.scrollWidth ?? 0,
        clientWidth: wrapper?.clientWidth ?? 0,
      };
    });
  expect(tableMetrics.overflowX).toBe("auto");
  expect(tableMetrics.scrollWidth).toBeGreaterThan(tableMetrics.clientWidth);
  const pageOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth
  );
  expect(pageOverflow).toBeLessThanOrEqual(1);

  // 不可信内容不成为可执行内容；危险 URL 不保留为可点击协议。
  const xss = await page.evaluate(
    () => (window as unknown as { __markdownXss?: number }).__markdownXss
  );
  expect(xss).toBe(0);
  const body = page.getByTestId("assistant-body").last();
  expect(await body.locator("script").count()).toBe(0);
  const hrefs = await body.locator("a").evaluateAll((links) =>
    links.map((link) => link.getAttribute("href") ?? "")
  );
  expect(hrefs.some((href) => /^javascript:/i.test(href))).toBe(false);

  // 朗读入口保持退役；复制已在上方验证完整原文。
  await expect(
    page
      .getByRole("toolbar", { name: "消息操作" })
      .last()
      .getByRole("button", { name: /朗读/ })
  ).toHaveCount(0);
});
