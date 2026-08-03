import type { ChatMessage } from "@/components/bridges/MessageList";
import type { ChatMessageProjection } from "@/lib/api";

/**
 * 把服务端消息投影组装成线程渲染项（Issue 11）。
 *
 * 每轮用户消息只渲染最新一次助手尝试作为回答；历史失败/停止的尝试折叠为
 * "前 N 次尝试"，保留审计关系，不静默改写历史。
 */
export function buildThreadMessages(messages: ChatMessageProjection[]): ChatMessage[] {
  const items: ChatMessage[] = [];
  for (let index = 0; index < messages.length; index += 1) {
    const message = messages[index];
    if (message.role === "user") {
      items.push({
        id: message.message_id,
        role: "user",
        plainText: message.content,
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
      status: latest.status === "streaming" ? "streaming" : latest.status === "error" ? "error" : undefined,
      errorText: latest.status === "error" ? (latest.error_message ?? "生成失败。") : undefined,
      previousAttempts: previous.map((attempt) => ({
        attemptNumber: attempt.attempt_number,
        status: attempt.status,
        errorMessage: attempt.error_message,
      })),
    });
  }
  return items;
}
