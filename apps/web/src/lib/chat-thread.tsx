import type {
  ChatMessage,
  ChatThinking,
  ThreadModeEvent,
} from "@/components/bridges/MessageList";
import type { ChatMessageProjection, ChatModeEventProjection } from "@/lib/api";

/**
 * 把服务端消息投影组装成线程渲染项（Issue 11/14）。
 *
 * 每轮用户消息只渲染最新一次助手尝试作为回答；历史失败/停止的尝试折叠为
 * "前 N 次尝试"，保留审计关系，不静默改写历史。助手消息携带可公开思考
 * 摘要（步骤/证据/工具/质量），耗时换算自真实生命周期 duration_ms。
 * 模式切换事件作为独立渲染项插入消息流（Issue 14，只影响后续消息）。
 */
export function buildThreadMessages(
  messages: ChatMessageProjection[],
  modeEvents: ChatModeEventProjection[] = []
): (ChatMessage | ThreadModeEvent)[] {
  const items: (ChatMessage | ThreadModeEvent)[] = [];
  const events = [...modeEvents];

  for (let index = 0; index < messages.length; index += 1) {
    const message = messages[index];
    // 消息时间之前的模式切换事件按序插入（时间相同的切换在消息前渲染）
    while (events.length > 0 && events[0].created_at <= message.created_at) {
      items.push({ kind: "mode-event", ...events.shift()! });
    }
    if (message.role === "user") {
      items.push({
        id: message.message_id,
        role: "user",
        plainText: message.content,
        attachments: message.attachments ?? [],
        content: <p style={{ whiteSpace: "pre-wrap", overflowWrap: "break-word" }}>{message.content}</p>,
      });
      continue;
    }

    // 收集同一轮用户消息之下的连续助手尝试
    const group: ChatMessageProjection[] = [message];
    while (index + 1 < messages.length && messages[index + 1].role === "assistant") {
      index += 1;
      group.push(messages[index]);
    }
    const latest = group[group.length - 1];
    const previous = group.slice(0, -1);
    items.push({
      id: latest.message_id,
      role: "assistant",
      plainText: latest.content,
      content: (
        <p style={{ whiteSpace: "pre-wrap", overflowWrap: "break-word", lineHeight: "var(--line-height-relaxed)" }}>
          {latest.content}
        </p>
      ),
      thinking: latest.thinking ? projectionThinking(latest) : undefined,
      // Issue 20：本轮分层检索轮次（含引用）；无检索作用域时为 null
      retrieval: latest.retrieval ?? null,
      webSearch: latest.web_search ?? null,
      arxivSearch: latest.arxiv_search ?? null,
      teaching: latest.teaching ?? null,
      // Issue 27：本次上下文说明披露；无披露为 null
      contextNote: latest.context_note ?? null,
      status: latest.status === "streaming" ? "streaming" : latest.status === "error" ? "error" : undefined,
      errorText: latest.status === "error" ? (latest.error_message ?? "生成失败。") : undefined,
      previousAttempts: previous.map((attempt) => ({
        attemptNumber: attempt.attempt_number,
        status: attempt.status,
        errorMessage: attempt.error_message,
      })),
    });
  }
  // 收尾：消息流之后剩余的模式切换事件
  while (events.length > 0) {
    items.push({ kind: "mode-event", ...events.shift()! });
  }
  return items;
}

/** 把服务端思考摘要投影换算为渲染形态（耗时基于真实 duration_ms）。 */
function projectionThinking(message: ChatMessageProjection): ChatThinking {
  const thinking = message.thinking;
  const seconds =
    message.duration_ms !== null && message.duration_ms !== undefined
      ? Math.max(1, Math.round(message.duration_ms / 1000))
      : null;
  return {
    steps: thinking?.steps ?? [],
    evidence: thinking?.evidence ?? [],
    tools: thinking?.tools ?? [],
    quality: thinking?.quality ?? [],
    seconds,
  };
}
