import type { ChatMessageView } from "@/lib/chat-message-view";

import type { ActiveRun, PendingUserMessage } from "./chat-run";

/**
 * 把服务端历史与进行中的运行合成同一份线程视图。
 *
 * 进行中的助手消息接管同 id 的历史项，避免恢复时双份；用户气泡和停止 /
 * 失败状态在终态交接期间保持渲染，直到权威历史接管。
 */
export function composeConversationMessages(
  baseMessages: ChatMessageView[],
  activeRun: ActiveRun | null,
  pendingUser: PendingUserMessage | null
): ChatMessageView[] {
  const pendingUserId = activeRun?.kind === "send" ? pendingUser?.id : null;
  const messages = baseMessages.filter(
    (item) => item.id !== activeRun?.messageId && item.id !== pendingUserId
  );
  if (!activeRun) return messages;

  const isError = activeRun.status === "error";
  const isStopped = activeRun.status === "stopped";
  const isStreaming = activeRun.status === "streaming";
  if (pendingUser && activeRun.kind === "send") {
    messages.push({
      id: pendingUser.id,
      role: "user",
      plainText: pendingUser.text,
      attachments: pendingUser.attachments ?? [],
    });
  }
  messages.push({
    id: activeRun.messageId,
    role: "assistant",
    plainText: activeRun.content,
    thinking: activeRun.thinking ?? undefined,
    webSearch: activeRun.webSearch,
    arxivSearch: activeRun.arxivSearch,
    teaching: activeRun.teaching,
    turnResult: activeRun.turnResult,
    stage: activeRun.stage,
    node: activeRun.node,
    humanizerProcess: activeRun.humanizerProcess,
    careerProcess: activeRun.careerProcess,
    image: activeRun.imageProcess?.task ?? undefined,
    video: activeRun.videoProcess?.task ?? undefined,
    status: isError ? "error" : isStopped ? "stopped" : isStreaming ? "streaming" : undefined,
    errorText: isError ? activeRun.errorText : undefined,
  });
  return messages;
}
