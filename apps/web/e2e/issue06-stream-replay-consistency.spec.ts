import { expect, test, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";

/**
 * Issue 06（本批）— 统一流式正文、终态存储与断线重放（append-only delta）。
 *
 * 真实浏览器生命周期验收：真实 API + 真实后台执行器 + 真实 SQLite + 真实
 * SSE/游标链路，零 page route mock。服务端在 test 环境内置确定性脚本适配器
 * （ScriptedChatStreamAdapter），由 `/_test/chat-stream-script` 注入分块正文
 * 与块间延迟，用于故障注入 chunk 边界、停止与重连；模型输出是确定性的，
 * 只用于验证机制（真实模型体验按评测票验证）。
 *
 * 覆盖验收标准：
 * - UI（复制出的消息纯文本）、事件重放（游标 0 重放的 delta 拼接）与落库
 *   正文（消息投影 content）逐字一致，不存在整段伪 delta；
 * - 改变已发前缀、多个受保护片段跨 chunk、停止后迟到数据不推进正文；
 * - 中途刷新（断线重连）不重复正文；终态重新加载与流式期间所见一致；
 * - 停止不新增模型调用（尝试数/消息数不变）。
 */

// 本文件三个用例共用同一后台生成执行器：并行 worker 会让多轮生成按队列串行，
// 后两轮在默认 5s 内等不到首块而假失败（本地默认 4 workers 实测 2/3 失败）。
// 文件内串行；首块等待与仓库其他聊天 E2E 统一为 30s，容忍其他 spec 的队列占用。
// CI（workers=1）行为不变。
test.describe.configure({ mode: "serial" });

const PASSWORD = "correct-horse-issue06";
const API_PORT = process.env.API_PORT || "8000";
const API_ORIGIN = `http://127.0.0.1:${API_PORT}`;

interface ApiMessage {
  message_id: string;
  role: "user" | "assistant";
  status: string;
  content: string;
  attempt_number: number;
}

interface ConversationView {
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

/** 解析 SSE 回放正文中的 delta 载荷；其他事件（done/error/stage/ping）忽略。 */
function parseSseDeltas(body: string): string[] {
  const deltas: string[] = [];
  for (const block of body.split("\n\n")) {
    const lines = block.split("\n").filter(Boolean);
    let eventName = "";
    const data: string[] = [];
    for (const line of lines) {
      if (line.startsWith("event:")) {
        eventName = line.slice("event:".length).trim();
      } else if (line.startsWith("data:")) {
        data.push(line.slice("data:".length).trim());
      }
    }
    if (eventName !== "delta" || data.length === 0) continue;
    deltas.push(JSON.parse(data.join("\n")).delta as string);
  }
  return deltas;
}

async function getReplayBody(
  page: Page,
  conversationId: string,
  messageId: string
): Promise<string> {
  const response = await page.request.get(
    `/api/chat/conversations/${conversationId}/messages/${messageId}/events?cursor=0`
  );
  expect(response.ok()).toBeTruthy();
  return parseSseDeltas(await response.text()).join("");
}

/** 复制探针：拦截 navigator.clipboard.writeText，读取消息的 plainText。 */
async function installCopyProbe(page: Page): Promise<void> {
  await page.addInitScript(() => {
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

/** 点最后一条助手消息的「复制」，返回 UI 正文（plainText）。 */
async function copyAssistantBody(page: Page): Promise<string> {
  const toolbar = page.getByRole("toolbar", { name: "消息操作" }).last();
  const copy = toolbar.getByRole("button", { name: "复制", exact: true });
  await copy.click();
  await expect(toolbar.getByText("已复制", { exact: true })).toBeVisible();
  const copied = await page.evaluate(
    () => (window as unknown as { __copiedText?: string }).__copiedText ?? ""
  );
  expect(copied.length).toBeGreaterThan(0);
  return copied;
}

async function sendFromHome(page: Page, text: string): Promise<string> {
  const composer = page.getByTestId("composer");
  await composer.getByLabel("输入消息").fill(text);
  await composer.getByRole("button", { name: "发送消息" }).click();
  await expect(page).toHaveURL(/\/chat\/[^/]+$/);
  return page.url().split("/chat/")[1].split(/[?#]/)[0];
}

async function waitForTerminal(
  page: Page,
  conversationId: string,
  expected: string
): Promise<ApiMessage> {
  await expect
    .poll(async () => (await getLastAssistant(page, conversationId)).status, {
      timeout: 30_000,
    })
    .toBe(expected);
  return getLastAssistant(page, conversationId);
}

test.describe("Issue 06 流式正文 / 终态存储 / 断线重放", () => {
  test("跨 chunk 受保护片段：流式不泄露改写前缀，UI、重放与落库逐字一致", async ({
    page,
  }) => {
    await installCopyProbe(page);
    const credentials = uniqueCredentials("i6a");
    await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);

    await configureScript(page, {
      match: "甲 10 ms",
      // 故障注入：chunk 边界切在被保护数值与行内代码中间，模型正文整体漂移。
      chunks: ["好的，帮你顺一下：甲 1", "1 ms；乙 2", "1 ms，写法 `x =", " 2`。"],
      // 每个 chunk 的延迟要覆盖前端 dev 编译/导航时间，否则生成可能在停止
      // 按钮渲染前就结束，流式窗口不可观测（Issue 06 验收复跑发现的假失败）。
      delay_ms: 1500,
    });
    const conversationId = await sendFromHome(
      page,
      "帮我自然地讲解这段实验记录，不要改动事实：甲 10 ms；乙 20 ms，写法 `x = 1`。"
    );

    const thread = page.getByTestId("chat-thread");
    const stopButton = page.getByRole("button", { name: "停止生成" });
    await expect(stopButton).toBeVisible({ timeout: 15_000 });
    // 流式期间：已确认前缀正常下发，被改写数值绝不出现在屏幕正文。
    await expect(thread).toContainText("好的", { timeout: 30_000 });
    for (let i = 0; i < 10; i += 1) {
      await expect(thread).not.toContainText("甲 11");
      await expect(thread).not.toContainText("x = 2");
      if (!(await stopButton.isVisible().catch(() => false))) break;
      await page.waitForTimeout(100);
    }

    const stored = await waitForTerminal(page, conversationId, "done");
    expect(stored.content).toBe("好的，帮你顺一下：甲 10 ms；乙 20 ms，写法 `x = 1`。");

    // 终态重新加载：刷新后与流式期间所见一致。
    await page.reload();
    await expect(thread).toBeVisible();
    const uiBody = await copyAssistantBody(page);

    const replayed = await getReplayBody(
      page,
      conversationId,
      stored.message_id
    );
    expect(uiBody).toBe(stored.content);
    expect(replayed).toBe(stored.content);
    // 三种正文逐字一致：不存在「整段正文当伪增量重发」造成的重复。
    expect(uiBody).toBe("好的，帮你顺一下：甲 10 ms；乙 20 ms，写法 `x = 1`。");
    await expect(thread.getByText("甲 11", { exact: false })).toHaveCount(0);
  });

  test("中途刷新断线重连：按游标续读，正文不重复、终态仍逐字一致", async ({
    page,
  }) => {
    await installCopyProbe(page);
    const credentials = uniqueCredentials("i6b");
    await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);

    const chunks = [
      "第一段：甲 30 ms；",
      "第二段：乙 40 ms。",
      "第三段：继续输出。",
      "第四段：仍在继续。",
      "第五段：接近结束。",
      "第六段：结束。",
    ];
    await configureScript(page, {
      match: "甲 30 ms",
      chunks,
      delay_ms: 1200,
    });
    const conversationId = await sendFromHome(
      page,
      "按原文整理，不要改数值：甲 30 ms；乙 40 ms。"
    );
    const expected = chunks.join("");

    const thread = page.getByTestId("chat-thread");
    const stopButton = page.getByRole("button", { name: "停止生成" });
    await expect(stopButton).toBeVisible({ timeout: 15_000 });
    await expect(thread).toContainText("第一段", { timeout: 30_000 });

    // 生成中刷新：页面从权威历史的 active_run 游标续读。
    await page.reload();
    await expect(thread).toContainText("第一段");
    await expect(stopButton).toBeVisible({ timeout: 10_000 });

    const stored = await waitForTerminal(page, conversationId, "done");
    expect(stored.content).toBe(expected);
    const replayed = await getReplayBody(
      page,
      conversationId,
      stored.message_id
    );
    expect(replayed).toBe(stored.content);

    // 重连后 UI 只保留一份正文：刷新页面读取终态并以复制纯文本核对。
    await page.reload();
    await expect(thread).toBeVisible();
    const uiBody = await copyAssistantBody(page);
    expect(uiBody).toBe(stored.content);
    expect(uiBody.split("第一段").length - 1).toBe(1);
  });

  test("停止生成：已确认正文保留，迟到数据不推进正文、不新增模型调用", async ({
    page,
  }) => {
    await installCopyProbe(page);
    const credentials = uniqueCredentials("i6c");
    await signUp(page, credentials.username, credentials.qqEmail, PASSWORD);

    await configureScript(page, {
      match: "甲 50 ms",
      chunks: [
        "先说明：",
        "甲 5",
        "1 ms；",
        "乙 61 ms。",
        "结尾不应出现。",
      ],
      // 同上：保证点击停止时仍有未到达的迟到分块。
      delay_ms: 1000,
    });
    const conversationId = await sendFromHome(
      page,
      "按原文整理，不要改数值：甲 50 ms；乙 60 ms。"
    );

    const thread = page.getByTestId("chat-thread");
    const stopButton = page.getByRole("button", { name: "停止生成" });
    await expect(stopButton).toBeVisible({ timeout: 15_000 });
    await expect(thread).toContainText("先说明", { timeout: 30_000 });

    await stopButton.click();
    const stopped = await waitForTerminal(page, conversationId, "stopped");
    expect(stopped.content).not.toContain("结尾");
    expect(stopped.content).not.toContain("51 ms");
    expect(stopped.content).not.toContain("61 ms");

    // 迟到分块继续到达也不推进正文，不触发新调用（消息数/尝试数不变）。
    await page.waitForTimeout(2500);
    const afterLateChunks = await getLastAssistant(page, conversationId);
    expect(afterLateChunks.content).toBe(stopped.content);
    expect(afterLateChunks.message_id).toBe(stopped.message_id);
    expect(afterLateChunks.attempt_number).toBe(stopped.attempt_number);

    const conversation = await getConversation(page, conversationId);
    const assistants = conversation.messages.filter(
      (message) => message.role === "assistant"
    );
    expect(assistants).toHaveLength(1);

    const replayed = await getReplayBody(
      page,
      conversationId,
      stopped.message_id
    );
    expect(replayed).toBe(stopped.content);

    await page.reload();
    await expect(thread).toBeVisible();
    const uiBody = await copyAssistantBody(page);
    expect(uiBody).toBe(stopped.content);
  });
});
