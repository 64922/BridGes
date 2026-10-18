"use client";

import type {
  ChatAttachmentProjection,
  ChatModuleId,
  CitationProjection,
  CommuteRouteProjection,
} from "@/lib/api";

import { AssistantMessage } from "./message/AssistantMessage";
import { UserMessage } from "./message/UserMessage";
import type { ChatMessageView } from "@/lib/chat-message-view";

interface MessageListProps {
  messages: ChatMessageView[];
  onRetry?: (id: string) => void;
  onStop?: () => void;
  onTeachingSkip?: (messageId: string) => void;
  onTeachingBeginnerStart?: (messageId: string) => void;
  /** 点击模块建议——以该轮用户消息原文显式启动模块 */
  onUseModuleSuggestion?: (messageId: string, moduleId: ChatModuleId) => void;
  conversationId?: string;
  /** 图片任务成功（资产落库）后刷新消息列表（正文/投影同步） */
  onRefreshMessages?: () => void;
  /** 打开该轮真实引用对应的右侧资料面板 */
  onOpenCitation?: (
    messageId: string,
    citation: CitationProjection,
    trigger: HTMLButtonElement
  ) => void;
  /** 打开消息附件的右侧预览面板 */
  onOpenAttachment?: (
    messageId: string,
    attachment: ChatAttachmentProjection,
    trigger: HTMLButtonElement
  ) => void;
  /** 打开该轮校园通勤的路线详情面板 */
  onOpenCommuteDetails?: (
    messageId: string,
    route: CommuteRouteProjection,
    trigger: HTMLButtonElement
  ) => void;
}

/**
 * 消息流：用户气泡与助手连续正文由两种消息组件承载；
 * 正文、公开过程、交付状态、来源投影和消息操作各有独立边界。
 */
export function MessageList({
  messages,
  onRetry,
  conversationId,
  onStop,
  onTeachingSkip,
  onTeachingBeginnerStart,
  onUseModuleSuggestion,
  onRefreshMessages,
  onOpenCitation,
  onOpenAttachment,
  onOpenCommuteDetails,
}: MessageListProps) {
  return (
    <ol
      role="list"
      aria-label="对话消息"
      style={{ display: "flex", flexDirection: "column", gap: "var(--space-6)" }}
    >
      {messages.map((message) => (
        <li key={message.id} id={`msg-${message.id}`}>
          {message.role === "user" ? (
            <UserMessage
              message={message}
              conversationId={conversationId}
              onOpenAttachment={onOpenAttachment}
            />
          ) : (
            <AssistantMessage
              message={message}
              conversationId={conversationId}
              onRetry={onRetry}
              onStop={onStop}
              onTeachingSkip={onTeachingSkip}
              onTeachingBeginnerStart={onTeachingBeginnerStart}
              onUseModuleSuggestion={onUseModuleSuggestion}
              onRefreshMessages={onRefreshMessages}
              onOpenCitation={onOpenCitation}
              onOpenCommuteDetails={onOpenCommuteDetails}
            />
          )}
        </li>
      ))}
    </ol>
  );
}
