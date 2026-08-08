import type { Page } from "@playwright/test";

/**
 * Issue 02 — 协议替身的契约适配。
 *
 * 真实服务端把「POST messages 返回 SSE 流」改为「创建响应（消息已落库、
 * 运行已入队）+ GET events 端点按游标回放持久化事件」。各 e2e 的协议
 * 替身统一按同一契约改造：POST 分支把原 SSE 正文存入 streams（key 为
 * 助手消息 id），本模块注册 events 回放路由。
 */

/** 注册 events 回放路由：按消息 id 回放已存 SSE 正文（运行终态后结束）。
 *  注意：Playwright glob 匹配包含 query string，pattern 尾部加 ``**``
 *  才能命中 ``?cursor=N`` 的订阅请求。 */
export function installRunEventsRoutes(
  page: Page,
  streams: Map<string, string>,
  conversationId = "mock-1"
): void {
  void page.route(
    `**/api/chat/conversations/${conversationId}/messages/*/events**`,
    async (route) => {
      const messageId = route.request().url().split("/messages/")[1].split("/")[0];
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        headers: { "Cache-Control": "no-cache", "X-Accel-Buffering": "no" },
        body: streams.get(messageId) ?? "",
      });
    }
  );
}

/** 构造 Issue 02 创建响应（run_id + 游标 + 两则消息投影）。 */
export function runCreated(
  runId: string,
  cursor: number,
  userMessage: unknown,
  assistantMessage: unknown
): Record<string, unknown> {
  return {
    run_id: runId,
    cursor,
    user_message: userMessage,
    assistant_message: assistantMessage,
  };
}
