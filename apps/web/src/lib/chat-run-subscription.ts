import {
  getChatConversation,
  subscribeChatRunEvents,
  type ChatConversationProjection,
  type ChatStreamEvent,
} from "@/lib/api";

export interface ChatRunSubscriptionOptions {
  conversationId: string;
  messageId: string;
  cursor: number;
  signal: AbortSignal;
  /** 订阅仍属于当前会话与当前运行；切换会话或新命令后返回 false。 */
  isActive: () => boolean;
  onEvent: (event: ChatStreamEvent) => void;
  /** 断线后发现运行已终态：把权威投影交给页面收敛。 */
  onTerminalProjection: (projection: ChatConversationProjection) => void;
}

/**
 * 订阅运行事件并自动重连。
 *
 * 断线只释放订阅，不改变后台运行；重连前读取权威投影，已终态则收敛，
 * 否则从服务端最后游标续读。最多重试 30 次后向上抛出，交由调用方呈现。
 */
export async function subscribeChatRunWithReconnect({
  conversationId,
  messageId,
  cursor: startCursor,
  signal,
  isActive,
  onEvent,
  onTerminalProjection,
}: ChatRunSubscriptionOptions): Promise<void> {
  let cursor = startCursor;
  let attempt = 0;
  for (;;) {
    if (!isActive()) return;
    try {
      await subscribeChatRunEvents(conversationId, messageId, cursor, onEvent, signal);
      return;
    } catch (error) {
      if (signal.aborted || !isActive()) return;
      attempt += 1;
      if (attempt > 30) throw error;
      await new Promise((resolve) =>
        setTimeout(resolve, Math.min(300 * attempt, 3000))
      );
      if (signal.aborted || !isActive()) return;
      try {
        const projection = await getChatConversation(conversationId);
        if (!isActive()) return;
        const message = projection.messages?.find(
          (item) => item.message_id === messageId
        );
        if (!message || message.status !== "streaming") {
          onTerminalProjection(projection);
          return;
        }
        cursor = message.active_run?.cursor ?? cursor;
      } catch {
        // 读取失败时保持原游标继续重试订阅。
      }
    }
  }
}
