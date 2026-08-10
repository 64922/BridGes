import { afterEach, describe, expect, it, vi } from "vitest";

import { createChatRun, startFirstTurn } from "@/lib/api";

describe("聊天写入请求合同", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("普通消息只提交正文", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({}), { status: 200 })
    );

    await createChatRun("conversation-1", "请帮我解释熵增");

    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      content: "请帮我解释熵增",
    });
  });

  it("首轮只提交会话创建所需字段，不提交手动能力字段", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({}), { status: 201 })
    );

    await startFirstTurn({
      content: "搜索量子纠错综述",
      idempotencyKey: "idempotency-key-1",
      conversationId: "conversation-1",
      mode: "study",
    });

    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      content: "搜索量子纠错综述",
      idempotency_key: "idempotency-key-1",
      conversation_id: "conversation-1",
      mode: "study",
    });
  });
});
