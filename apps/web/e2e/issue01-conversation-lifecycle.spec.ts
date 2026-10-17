import { expect, test, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

/**
 * 工单 01 — 整理对话运行职责后的真实生命周期回归。
 *
 * 使用真实 API、真实后台执行器、真实 SQLite 与 SSE 游标链路；仅通过测试
 * 环境内置的脚本适配器注入确定性分块与延迟，不 mock 会话生命周期。
 */

const PASSWORD = "correct-horse-issue01";
const API_PORT = process.env.API_PORT || "8000";
const API_ORIGIN = `http://127.0.0.1:${API_PORT}`;
const STUB_REPLY = "这是一条来自本地替身模式的确定性测试回答。";

interface ApiMessage {
  message_id: string;
  role: "user" | "assistant";
  status: string;
  content: string;
  attempt_number: number;
}

interface ConversationView {
  conversation_id: string;
  messages: ApiMessage[];
}

async function configureScript(
  page: Page,
  script: { match: string; chunks: string[]; delay_ms?: number }
): Promise<void> {
  const response = await page.request.post(
    `${API_ORIGIN}/_test/chat-stream-script`,
    { data: script }
  );
  expect(response.ok()).toBeTruthy();
}

async function getConversation(
  page: Page,
  conversationId: string
): Promise<ConversationView> {
  const response = await page.request.get(
    `/api/chat/conversations/${conversationId}`
  );
  expect(response.ok()).toBeTruthy();
  return (await response.json()) as ConversationView;
}

async function getLastAssistant(
  page: Page,
  conversationId: string
): Promise<ApiMessage> {
  const conversation = await getConversation(page, conversationId);
  const assistants = conversation.messages.filter(
    (message) => message.role === "assistant"
  );
  expect(assistants.length).toBeGreaterThan(0);
  return assistants[assistants.length - 1];
}

async function waitForTerminal(
  page: Page,
  conversationId: string
): Promise<ApiMessage> {
  await expect
    .poll(async () => (await getLastAssistant(page, conversationId)).status, {
      timeout: 30_000,
    })
    .not.toBe("streaming");
  return getLastAssistant(page, conversationId);
}

async function sendFromHome(page: Page, text: string): Promise<string> {
  const composer = page.getByTestId("composer");
  await composer.getByLabel("输入消息").fill(text);
  await composer.getByRole("button", { name: "发送消息" }).click();
  await expect(page).toHaveURL(/\/chat\/[^/]+$/);
  return page.url().split("/chat/")[1].split(/[?#]/)[0];
}

async function createConversation(page: Page, text: string): Promise<string> {
  const conversationId = await sendFromHome(page, text);
  await expect(page.getByText(STUB_REPLY)).toBeVisible({ timeout: 30_000 });
  await waitForTerminal(page, conversationId);
  return conversationId;
}

function countOccurrences(text: string, needle: string): number {
  return text.split(needle).length - 1;
}

test.describe.configure({ mode: "serial", timeout: 60_000 });

test.describe("Issue 01 对话运行生命周期", () => {
  test("已有会话双击发送：只创建一条用户消息与一次运行，正文不重复", async ({
    page,
  }) => {
    const credentials = uniqueCredentials("i1a");
    await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);
    const conversationId = await createConversation(page, "双击前的首轮消息");

    await configureScript(page, {
      match: "双击发送测试",
      chunks: ["双击", "只生成", "一次。"],
      delay_ms: 600,
    });
    const composer = page.getByTestId("composer");
    await composer.getByLabel("输入消息").fill("双击发送测试");
    await composer.getByRole("button", { name: "发送消息" }).dblclick();

    const thread = page.getByTestId("chat-thread");
    await expect(thread).toContainText("双击只生成一次。", { timeout: 30_000 });
    await waitForTerminal(page, conversationId);

    const conversation = await getConversation(page, conversationId);
    expect(
      conversation.messages.filter(
        (message) =>
          message.role === "user" && message.content === "双击发送测试"
      )
    ).toHaveLength(1);
    expect(
      conversation.messages.filter((message) => message.role === "assistant")
    ).toHaveLength(2);

    const last = await getLastAssistant(page, conversationId);
    expect(last.content).toBe("双击只生成一次。");
    expect(countOccurrences(await thread.innerText(), "双击只生成一次。")).toBe(1);
  });

  test("刷新进行中的会话：已接收正文与后续事件衔接，不重复追加", async ({
    page,
  }) => {
    const credentials = uniqueCredentials("i1d");
    await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);
    const conversationId = await createConversation(page, "刷新前的首轮消息");

    await configureScript(page, {
      match: "刷新恢复测试",
      chunks: ["刷新恢复", "后继续", "完成。"],
      delay_ms: 1200,
    });

    const composer = page.getByTestId("composer");
    await composer.getByLabel("输入消息").fill("刷新恢复测试");
    await composer.getByRole("button", { name: "发送消息" }).click();
    const thread = page.getByTestId("chat-thread");
    await expect(thread).toContainText("刷新恢复", { timeout: 30_000 });

    await page.reload();
    await expect(thread).toContainText("刷新恢复后继续完成。", {
      timeout: 30_000,
    });
    const last = await waitForTerminal(page, conversationId);
    expect(last.content).toBe("刷新恢复后继续完成。");
    expect(
      countOccurrences(await thread.innerText(), "刷新恢复后继续完成。")
    ).toBe(1);
  });

  test("切换会话释放旧订阅：迟到事件不写入新会话，后台运行继续并可恢复", async ({
    page,
  }) => {
    const credentials = uniqueCredentials("i1b");
    await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);
    const conversationA = await createConversation(page, "会话甲的建立");
    await page
      .getByTestId("app-sidebar")
      .getByRole("link", { name: "新聊天", exact: true })
      .click();
    await expect(page.getByTestId("new-chat-home")).toBeVisible();
    const conversationB = await createConversation(page, "会话乙的建立");

    await configureScript(page, {
      match: "切换订阅测试",
      chunks: ["外部迟到的", "不应出现在乙。"],
      delay_ms: 1500,
    });

    const sidebar = page.getByTestId("app-sidebar");
    await sidebar.getByRole("link", { name: /会话甲的建立/ }).click();
    await expect(page).toHaveURL(new RegExp(`/chat/${conversationA}$`));

    const composer = page.getByTestId("composer");
    await composer.getByLabel("输入消息").fill("切换订阅测试");
    await composer.getByRole("button", { name: "发送消息" }).click();
    const thread = page.getByTestId("chat-thread");
    await expect(thread).toContainText("外部迟到的", { timeout: 30_000 });
    await expect(page.getByRole("button", { name: "停止生成" })).toBeVisible();

    // 切到乙会话：本地订阅释放，但后台运行不被停止。
    await sidebar.getByRole("link", { name: /会话乙的建立/ }).click();
    await expect(page).toHaveURL(new RegExp(`/chat/${conversationB}$`));
    await expect(thread).toContainText("会话乙的建立");
    await page.waitForTimeout(2000);
    const threadOnB = await thread.innerText();
    expect(threadOnB).not.toContain("外部迟到的");
    expect(threadOnB).not.toContain("不应出现在乙。");

    // 仍在生成时切回甲：从权威历史的游标恢复，最终正文完整且唯一。
    await sidebar.getByRole("link", { name: /会话甲的建立/ }).click();
    await expect(page).toHaveURL(new RegExp(`/chat/${conversationA}$`));
    await expect(thread).toContainText("外部迟到的", { timeout: 15_000 });
    await expect(thread).toContainText("外部迟到的不应出现在乙。", {
      timeout: 30_000,
    });
    const last = await waitForTerminal(page, conversationA);
    expect(last.content).toBe("外部迟到的不应出现在乙。");
    expect(
      countOccurrences(await thread.innerText(), "外部迟到的不应出现在乙。")
    ).toBe(1);
  });

  test("终态交接：正文不闪空、不重复，停止入口随完成消失", async ({ page }) => {
    const credentials = uniqueCredentials("i1c");
    await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);
    const conversationId = await createConversation(page, "交接前的首轮消息");

    await configureScript(page, {
      match: "终态交接测试",
      chunks: ["终态交接", "唯一答案。"],
      delay_ms: 800,
    });

    await page.evaluate(() => {
      const thread = document.querySelector('[data-testid="chat-thread"]');
      const samples: string[] = [];
      const observer = new MutationObserver(() => {
        samples.push(thread?.textContent ?? "");
      });
      if (thread) {
        observer.observe(thread, {
          subtree: true,
          childList: true,
          characterData: true,
        });
      }
      (window as unknown as { __issue01Samples: string[] }).__issue01Samples =
        samples;
      (window as unknown as { __issue01Observer: MutationObserver }).__issue01Observer =
        observer;
    });

    const composer = page.getByTestId("composer");
    await composer.getByLabel("输入消息").fill("终态交接测试");
    await composer.getByRole("button", { name: "发送消息" }).click();
    const thread = page.getByTestId("chat-thread");
    await expect(thread).toContainText("终态交接唯一答案。", { timeout: 30_000 });
    await waitForTerminal(page, conversationId);
    await expect(page.getByRole("button", { name: "停止生成" })).toHaveCount(0);
    await page.waitForTimeout(500);
    const samples = await page.evaluate(() => {
      const state = window as unknown as {
        __issue01Samples: string[];
        __issue01Observer: MutationObserver;
      };
      state.__issue01Observer.disconnect();
      return state.__issue01Samples;
    });

    const firstFull = samples.findIndex((sample) =>
      sample.includes("终态交接唯一答案。")
    );
    expect(firstFull).toBeGreaterThanOrEqual(0);
    for (const sample of samples.slice(firstFull)) {
      expect(sample).toContain("终态交接唯一答案。");
      expect(countOccurrences(sample, "终态交接唯一答案。")).toBeLessThanOrEqual(1);
    }
  });
});
