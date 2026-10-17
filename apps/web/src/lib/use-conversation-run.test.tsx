import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  createChatRun,
  startFirstTurn,
  stopChatMessage,
  type ChatConversationProjection,
  type ChatFirstTurnResponse,
  type ChatRunStartedResponse,
  type ChatStreamEvent,
} from "@/lib/api";

import { subscribeChatRunWithReconnect } from "./chat-run-subscription";
import {
  useConversationRun,
  type UseConversationRunOptions,
} from "./use-conversation-run";

vi.mock("@/lib/recent-conversations", () => ({
  CHAT_LIST_CHANGED_EVENT: "test:chat-list-changed",
}));

vi.mock("@/lib/api", () => ({
  createChatRun: vi.fn(),
  isChatStreamEventOf: (
    event: { data: { kind: string } },
    kind: string
  ) => event.data.kind === kind,
  retryChatRun: vi.fn(),
  startFirstTurn: vi.fn(),
  stopChatMessage: vi.fn(),
}));

vi.mock("./chat-run-subscription", () => ({
  subscribeChatRunWithReconnect: vi.fn(),
}));

function message(overrides: Record<string, unknown> = {}) {
  return {
    message_id: "a-1",
    conversation_id: "c-1",
    role: "assistant",
    status: "streaming",
    content: "",
    attachments: [],
    thinking: null,
    web_search: null,
    arxiv_search: null,
    teaching: null,
    turn_result: null,
    ...overrides,
  };
}

function conversation(messages: unknown[]): ChatConversationProjection {
  return {
    conversation_id: "c-1",
    mode: "companion",
    messages,
  } as unknown as ChatConversationProjection;
}

function firstTurnResponse(): ChatFirstTurnResponse {
  return {
    conversation: conversation([
      message({ message_id: "u-1", role: "user", content: "首轮" }),
      message({ message_id: "a-1" }),
    ]),
    user_message: message({ message_id: "u-1", role: "user", content: "首轮" }),
    assistant_message: message({ message_id: "a-1" }),
    cursor: 0,
  } as unknown as ChatFirstTurnResponse;
}

function startedResponse(): ChatRunStartedResponse {
  return {
    user_message: message({ message_id: "u-2", role: "user", content: "继续" }),
    assistant_message: message({ message_id: "a-2" }),
    cursor: 3,
  } as unknown as ChatRunStartedResponse;
}

function streamEvent(data: Record<string, unknown>): ChatStreamEvent {
  return { event: data.kind, data } as unknown as ChatStreamEvent;
}

function options(
  conversationValue: ChatConversationProjection
): UseConversationRunOptions {
  return {
    conversationId: "c-1",
    conversation: conversationValue,
    ready: true,
    revision: 1,
    load: vi.fn().mockResolvedValue(null),
    setConversation: vi.fn(),
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(subscribeChatRunWithReconnect).mockRejectedValue(
    new Error("流式连接中断")
  );
});

afterEach(cleanup);

describe("对话运行状态所有者", () => {
  it("首轮订阅失败后重发复用首轮幂等键，不改走普通运行创建", async () => {
    const firstTurn = firstTurnResponse();
    vi.mocked(startFirstTurn).mockResolvedValue(firstTurn);
    const initial = options(conversation([]));
    const { result, rerender } = renderHook(
      (props: UseConversationRunOptions) => useConversationRun(props),
      { initialProps: initial }
    );

    await act(async () => {
      await result.current.sendMessage("首轮");
    });
    expect(startFirstTurn).toHaveBeenCalledTimes(1);

    rerender({
      ...initial,
      conversation: firstTurn.conversation,
      revision: 2,
    });
    await act(async () => {
      await result.current.sendMessage("首轮");
    });

    const calls = vi.mocked(startFirstTurn).mock.calls;
    expect(calls).toHaveLength(2);
    expect(calls[1][0].idempotencyKey).toBe(calls[0][0].idempotencyKey);
    expect(createChatRun).not.toHaveBeenCalled();
  });

  it("停止成功后立即显示停止状态，不等待历史刷新", async () => {
    vi.mocked(createChatRun).mockResolvedValue(startedResponse());
    vi.mocked(subscribeChatRunWithReconnect).mockImplementation(
      (subscription) =>
        new Promise<void>((resolve) => {
          subscription.signal.addEventListener("abort", () => resolve(), {
            once: true,
          });
        })
    );
    vi.mocked(stopChatMessage).mockResolvedValue({} as never);

    const { result } = renderHook(() =>
      useConversationRun(
        options(
          conversation([
            message({ message_id: "u-1", role: "user", content: "旧问题" }),
            message({ message_id: "a-1", role: "assistant", status: "done" }),
          ])
        )
      )
    );

    let sending: Promise<boolean> | null = null;
    act(() => {
      sending = result.current.sendMessage("继续");
    });
    await waitFor(() =>
      expect(subscribeChatRunWithReconnect).toHaveBeenCalled()
    );
    expect(result.current.activeRun?.status).toBe("streaming");

    await act(async () => {
      await result.current.stop();
      await sending;
    });
    expect(result.current.activeRun?.status).toBe("stopped");
  });

  it("停止等待期间收到终态事件时不回退正文与错误状态", async () => {
    vi.mocked(createChatRun).mockResolvedValue(startedResponse());
    let onEvent: ((event: ChatStreamEvent) => void) | null = null;
    vi.mocked(subscribeChatRunWithReconnect).mockImplementation(
      (subscription) =>
        new Promise<void>((resolve) => {
          onEvent = subscription.onEvent;
          subscription.signal.addEventListener("abort", () => resolve(), {
            once: true,
          });
        })
    );
    let resolveStop: (() => void) | null = null;
    vi.mocked(stopChatMessage).mockReturnValue(
      new Promise<void>((resolve) => {
        resolveStop = resolve;
      }) as never
    );

    const { result, unmount } = renderHook(() =>
      useConversationRun(
        options(
          conversation([
            message({ message_id: "u-1", role: "user", content: "旧问题" }),
            message({ message_id: "a-1", role: "assistant", status: "done" }),
          ])
        )
      )
    );

    let sending: Promise<boolean> | null = null;
    act(() => {
      sending = result.current.sendMessage("继续");
    });
    await waitFor(() =>
      expect(subscribeChatRunWithReconnect).toHaveBeenCalled()
    );
    expect(result.current.activeRun?.status).toBe("streaming");

    let stopping: Promise<void> | null = null;
    act(() => {
      stopping = result.current.stop();
    });
    expect(stopChatMessage).toHaveBeenCalledWith("c-1", "a-2");

    act(() => {
      onEvent?.(
        streamEvent({
          kind: "delta",
          message_id: "a-2",
          delta: "停止前已到达的正文",
        })
      );
      onEvent?.(
        streamEvent({
          kind: "error",
          message_id: "a-2",
          error: {
            code: "stream_interrupted",
            message: "生成中断。",
            retryable: true,
          },
          thinking: null,
          duration_ms: 1200,
          web_search: null,
          arxiv_search: null,
          teaching: null,
        })
      );
    });
    expect(result.current.activeRun?.status).toBe("error");

    await act(async () => {
      resolveStop?.();
      await stopping;
    });
    expect(result.current.activeRun?.status).toBe("error");
    expect(result.current.activeRun?.content).toBe("停止前已到达的正文");
    expect(result.current.activeRun?.errorText).toBe("生成中断。");

    await act(async () => {
      unmount();
      await sending;
    });
  });
});
