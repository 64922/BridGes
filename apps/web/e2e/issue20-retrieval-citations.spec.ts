import { expect, test, type Page } from "@playwright/test";

import { signUp, uniqueCredentials } from "./helpers/auth";
import { installRunEventsRoutes, runCreated } from "./helpers/chat-mock";

/**
 * Issue 20 — 交付分层本地检索、融合排序与引用。
 *
 * 协议级替身验证前端边界（与后端 pytest 替身策略一致，真实检索链路由
 * 服务级测试覆盖）：检索过程（loading → 结果卡）、引用展开与精确跳转、
 * 无依据空状态、索引失败与重试、发送前来源层面板与知识库开关。
 */

const NOW = "2026-08-04T00:00:00Z";
const CONVERSATION_ID = "conv-issue20";

interface MockMessage {
  message_id: string;
  conversation_id: string;
  role: "user" | "assistant";
  attempt_number: number;
  status: string;
  content: string;
  attachments: unknown[];
  thinking: unknown;
  retrieval: unknown;
  error_code: string | null;
  error_message: string | null;
  duration_ms: number | null;
  model_id: string | null;
  run_lock_id: string | null;
  created_at: string;
  updated_at: string;
}

function baseMessage(
  id: string,
  role: "user" | "assistant",
  content: string,
  status: string,
  retrieval: unknown = null
): MockMessage {
  return {
    message_id: id,
    conversation_id: CONVERSATION_ID,
    role,
    attempt_number: 1,
    status,
    content,
    attachments: [],
    thinking: null,
    retrieval,
    error_code: null,
    error_message: null,
    duration_ms: role === "assistant" ? 90 : null,
    model_id: role === "assistant" ? "qwen3.7-plus-2026-05-26" : null,
    run_lock_id: role === "assistant" ? "lock-mock" : null,
    created_at: NOW,
    updated_at: NOW,
  };
}

function layer(layer: string, status: string, candidates: number, note: string | null) {
  return { layer, status, candidates, note };
}

function citation(
  id: string,
  source_layer: string,
  filename: string,
  rank: number,
  overrides: Record<string, unknown> = {}
) {
  return {
    citation_id: id,
    source_layer,
    object_id: `obj-${id}`,
    filename,
    media_type: "text/plain",
    page_number: null,
    section_title: null,
    snippet: "热力学第二定律：熵在孤立系统中永不减少，这是可核对的原文片段。",
    rank,
    ...overrides,
  };
}

function retrievalRound(overrides: Record<string, unknown> = {}) {
  return {
    round_id: "rnd-1",
    message_id: "a-1",
    conversation_id: CONVERSATION_ID,
    use_knowledge_base: true,
    index_version_id: "idx-1",
    sufficiency: "sufficient",
    layers: [
      layer("attachment", "ok", 1, null),
      layer("project", "disabled", 0, "该对话未归属学习项目。"),
      layer("knowledge_base", "ok", 1, null),
    ],
    citations: [
      citation("cit-1", "attachment", "课程笔记.txt", 1, { page_number: 3 }),
      citation("cit-2", "knowledge_base", "热力学讲义.md", 2, { section_title: "第二章 熵增原理" }),
    ],
    note: "已检索到足够的本地材料。",
    created_at: NOW,
    ...overrides,
  };
}

const sseStarted = (messageId: string, userMessageId: string) =>
  `event: started\ndata: ${JSON.stringify({
    kind: "started",
    conversation_id: CONVERSATION_ID,
    user_message_id: userMessageId,
    message_id: messageId,
    attempt_number: 1,
  })}\n\n`;

const sseDelta = (messageId: string, delta: string) =>
  `event: delta\ndata: ${JSON.stringify({ kind: "delta", message_id: messageId, delta })}\n\n`;

const sseDone = (message: MockMessage) =>
  `event: done\ndata: ${JSON.stringify({ kind: "done", message_id: message.message_id, message })}\n\n`;

/**
 * 有状态的聊天 API 替身：GET 历史 / 发送 / 重试 / 引用详情均可编程。
 * ``scenario`` 决定发送后助手消息携带的检索轮次（用于无依据/索引失败场景）。
 */
function installMockChatApi(page: Page, scenario: "success" | "no_hits" | "index_unavailable") {
  const state = {
    messages: [baseMessage("u-0", "user", "你好", "done")],
    counter: 0,
  };
  const sentBody: Array<Record<string, unknown>> = [];
  // Issue 02：消息 → 持久化事件流（POST 创建运行后由 events 端点回放）
  const eventStreams = new Map<string, string>();

  const history = () => ({
    conversation_id: CONVERSATION_ID,
    title: "Issue 20 对话",
    mode: "companion",
    pinned: false,
    project_id: null,
    created_at: NOW,
    updated_at: NOW,
    messages: state.messages,
    mode_events: [],
  });

  const roundFor = (): unknown => {
    if (scenario === "no_hits") {
      return retrievalRound({
        sufficiency: "no_hits",
        citations: [],
        note: "没有找到与问题相关的本地材料。",
      });
    }
    if (scenario === "index_unavailable") {
      return retrievalRound({
        sufficiency: "index_unavailable",
        citations: [],
        note: "本地索引不可用，暂无法检索本地材料，请稍后重试。",
      });
    }
    return retrievalRound();
  };

  return {
    sentBodies: sentBody,
    async install(): Promise<void> {
      await page.route("**/api/chat/conversations", async (route) => {
        if (route.request().method() === "POST") {
          await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify(history()) });
          return;
        }
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            conversations: [
              {
                conversation_id: CONVERSATION_ID,
                title: "Issue 20 对话",
                mode: "companion",
                message_count: state.messages.length,
                created_at: NOW,
                updated_at: NOW,
              },
            ],
          }),
        });
      });

      await page.route(`**/api/chat/conversations/${CONVERSATION_ID}`, async (route) => {
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(history()) });
      });

      // 发送：SSE 流式返回；done 事件的助手消息携带本轮检索轮次
      await page.route(`**/api/chat/conversations/${CONVERSATION_ID}/messages`, async (route) => {
        if (route.request().method() !== "POST") return;
        const body = JSON.parse(route.request().postData() ?? "{}");
        sentBody.push(body);
        state.counter += 1;
        const userMessage = baseMessage(`u-${state.counter}`, "user", String(body.content ?? ""), "done");
        const assistantMessage = baseMessage(`a-${state.counter}`, "assistant", "", "streaming");
        assistantMessage.retrieval = roundFor();
        state.messages.push(userMessage, assistantMessage);
        eventStreams.set(
          assistantMessage.message_id,
          `${sseStarted(assistantMessage.message_id, userMessage.message_id)}${sseDelta(
            assistantMessage.message_id,
            "正在生成"
          )}${sseDone({ ...assistantMessage, content: "基于材料回答。", status: "done" })}`
        );
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(
            runCreated(`run-${assistantMessage.message_id}`, 1, userMessage, assistantMessage)
          ),
        });
      });

      // 重试：复用同一轮次数据（同作用域同结果）
      await page.route(`**/api/chat/conversations/${CONVERSATION_ID}/messages/*/retry`, async (route) => {
        state.counter += 1;
        const retried = baseMessage(
          `a-${state.counter}`,
          "assistant",
          "重试后的回答。",
          "done",
          retrievalRound()
        );
        state.messages.push(retried);
        eventStreams.set(
          retried.message_id,
          `${sseStarted(retried.message_id, "u-0")}${sseDelta(retried.message_id, "重试后的")}${sseDone(retried)}`
        );
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(
            runCreated(`run-${retried.message_id}`, 1, state.messages[0], retried)
          ),
        });
      });

      // Issue 02：订阅运行事件（回放已持久化事件；运行终态后结束）
      installRunEventsRoutes(page, eventStreams, CONVERSATION_ID);

      // 引用证据详情：展开引用时按需请求
      await page.route(
        `**/api/chat/conversations/${CONVERSATION_ID}/messages/*/citations/*`,
        async (route) => {
          const segments = route.request().url().split("/");
          const citationId = segments.at(-1) ?? "cit-1";
          const found =
            retrievalRound().citations.find((item: { citation_id: string }) => item.citation_id === citationId) ??
            citation(citationId, "knowledge_base", "材料.txt", 1);
          await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
              citation: found,
              access_status: "accessible",
              access_message: "原文可访问。",
              download_url: `/api/chat/conversations/${CONVERSATION_ID}/attachments/obj-${citationId}/download`,
            }),
          });
        }
      );
    },
  };
}

async function registerAndOpenConversation(page: Page): Promise<void> {
  const credentials = uniqueCredentials("issue20");
  await signUp(page, credentials.username, credentials.qqEmail, "Passw0rd123!");
  await page.goto(`/chat/${CONVERSATION_ID}`);
  await expect(page.getByTestId("composer")).toBeVisible();
}

test.describe("Issue 20 — 分层检索、引用与来源开关", () => {
  test("检索中加载态：streaming 消息展示检索进度而非空白", async ({ page }) => {
    // 初始历史直接包含一条 streaming 助手消息（无检索轮次）：确定性呈现
    // 检索中加载态（route.fulfill 不支持渐进流式，故用权威历史驱动）。
    const streaming = baseMessage("a-0", "assistant", "", "streaming");
    const state = { messages: [baseMessage("u-0", "user", "你好", "done"), streaming] };
    const history = () => ({
      conversation_id: CONVERSATION_ID,
      title: "Issue 20 对话",
      mode: "companion",
      pinned: false,
      project_id: null,
      created_at: NOW,
      updated_at: NOW,
      messages: state.messages,
      mode_events: [],
    });
    await page.route(`**/api/chat/conversations/${CONVERSATION_ID}`, async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(history()) });
    });
    await page.route("**/api/chat/conversations", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          conversations: [
            {
              conversation_id: CONVERSATION_ID,
              title: "Issue 20 对话",
              mode: "companion",
              message_count: 2,
              created_at: NOW,
              updated_at: NOW,
            },
          ],
        }),
      });
    });
    await registerAndOpenConversation(page);
    await expect(page.getByTestId("retrieval-card-loading")).toContainText("正在检索本地材料");
  });

  test("检索结果卡：来源层状态、充足性信号与引用展开/精确跳转", async ({ page }) => {
    const mock = installMockChatApi(page, "success");
    await mock.install();
    await registerAndOpenConversation(page);

    // 发送消息：done 后权威历史携带本轮检索轮次与引用
    await page.getByTestId("composer").getByLabel("输入消息").fill("热力学第二定律是什么？");
    await page.getByRole("button", { name: "发送消息" }).click();
    await expect(page.getByTestId("retrieval-card")).toBeVisible();
    await expect(page.getByTestId("retrieval-sufficiency-sufficient")).toContainText("已检索到足够材料");

    // 三层来源状态（附件/项目/知识库）
    await expect(page.getByTestId("retrieval-layer-attachment")).toContainText("当前附件");
    await expect(page.getByTestId("retrieval-layer-attachment")).toContainText("1 条候选");
    await expect(page.getByTestId("retrieval-layer-project")).toContainText("该对话未归属学习项目");
    await expect(page.getByTestId("retrieval-layer-knowledge_base")).toContainText("1 条候选");

    // 引用：文件名 + 页码/章节；点击展开证据详情
    await expect(page.getByTestId("citation-item-1")).toContainText("课程笔记.txt");
    await expect(page.getByTestId("citation-item-1")).toContainText("第 3 页");
    await expect(page.getByTestId("citation-item-2")).toContainText("热力学讲义.md");
    await expect(page.getByTestId("citation-item-2")).toContainText("第二章 熵增原理");

    await page.getByTestId("citation-item-1").click();
    await expect(page.getByTestId("citation-detail-1")).toContainText("熵在孤立系统中永不减少");
    await expect(page.getByTestId("citation-detail-1")).toContainText("原文可访问");

    // 精确跳转：打开原文链接指向授权下载入口
    const openLink = page.getByTestId("citation-open-1");
    await expect(openLink).toHaveAttribute(
      "href",
      `/api/chat/conversations/${CONVERSATION_ID}/attachments/obj-cit-1/download`
    );
  });

  test("无依据空状态：结构化呈现而非空白", async ({ page }) => {
    const mock = installMockChatApi(page, "no_hits");
    await mock.install();
    await registerAndOpenConversation(page);

    await page.getByTestId("composer").getByLabel("输入消息").fill("完全无关的问题");
    await page.getByRole("button", { name: "发送消息" }).click();
    await expect(page.getByTestId("retrieval-card")).toBeVisible();
    await expect(page.getByTestId("retrieval-sufficiency-no_hits")).toContainText("未找到匹配材料");
    await expect(page.getByTestId("retrieval-empty")).toContainText("没有检索到与问题相关的本地材料");
  });

  test("索引失败：错误状态与重试（重试不重复用户消息）", async ({ page }) => {
    const mock = installMockChatApi(page, "index_unavailable");
    await mock.install();
    await registerAndOpenConversation(page);

    await page.getByTestId("composer").getByLabel("输入消息").fill("热力学");
    await page.getByRole("button", { name: "发送消息" }).click();
    await expect(page.getByTestId("retrieval-sufficiency-index_unavailable")).toContainText("本地索引不可用");

    // 重试检索 → 消息级重试 API（不重复用户消息）
    const retryRequest = page.waitForRequest(
      (request) =>
        request.url().includes(`/api/chat/conversations/${CONVERSATION_ID}/messages/`) &&
        request.url().endsWith("/retry")
    );
    await page.getByTestId("retrieval-retry").click();
    await retryRequest;
    await expect(page.getByTestId("retrieval-sufficiency-sufficient")).toContainText("已检索到足够材料");
  });

  test("发送前来源层面板与知识库开关：关闭后请求不含知识库", async ({ page }) => {
    const mock = installMockChatApi(page, "success");
    await mock.install();
    await registerAndOpenConversation(page);

    // 来源层面板：附件/项目/知识库三层可见，知识库默认开启
    await expect(page.getByTestId("source-layer-attachment")).toContainText("当前附件");
    await expect(page.getByTestId("source-layer-project")).toContainText("当前项目");
    const kbSwitch = page.getByTestId("source-layer-knowledge-base");
    await expect(kbSwitch).toHaveAttribute("aria-checked", "true");

    // 关闭全局知识库后发送：请求体 use_knowledge_base=false
    await kbSwitch.click();
    await expect(kbSwitch).toHaveAttribute("aria-checked", "false");
    await page.getByTestId("composer").getByLabel("输入消息").fill("热力学第二定律");
    await page.getByRole("button", { name: "发送消息" }).click();
    await expect(page.getByTestId("retrieval-card")).toBeVisible();
    expect(mock.sentBodies.length).toBe(1);
    expect(mock.sentBodies[0].use_knowledge_base).toBe(false);
  });
});
