"use client";

import { Icon } from "@/components/design-system/Icon";
import type { ChatAttachmentProjection } from "@/lib/api";
import { chatModuleIcon, chatModuleLabel } from "@/lib/chat-modules";

import { MessageAttachments } from "./MessageAttachments";
import { UserMessageBody } from "./MessageBody";
import type { ChatMessageView } from "@/lib/chat-message-view";

export function UserMessage({
  message,
  conversationId,
  onOpenAttachment,
}: {
  message: ChatMessageView;
  conversationId?: string;
  onOpenAttachment?: (
    messageId: string,
    attachment: ChatAttachmentProjection,
    trigger: HTMLButtonElement
  ) => void;
}) {
  const attachments = message.attachments ?? [];
  return (
    <div style={{ display: "flex", justifyContent: "flex-end" }}>
      <div
        data-testid="user-message-bubble"
        style={{
          maxWidth: "85%",
          padding: "var(--space-3) var(--space-4)",
          borderRadius: "var(--radius-xl)",
          backgroundColor: "var(--color-accent-primary-soft)",
          color: "var(--color-text-primary)",
          overflowWrap: "break-word",
        }}
      >
        {/* 本轮显式选择的模块标识（只读消息记录——重开后历史标识
            不随输入区的新选择改变）。 */}
        {chatModuleLabel(message.moduleId) && (
          <span
            data-testid="user-module-label"
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "var(--space-1)",
              marginBottom: "var(--space-1)",
              padding: "2px var(--space-2)",
              border: "1px solid var(--color-accent-primary)",
              borderRadius: "var(--radius-full)",
              fontSize: "var(--text-xs)",
              fontWeight: 600,
              color: "var(--color-text-secondary)",
            }}
          >
            <Icon name={chatModuleIcon(message.moduleId)} size={12} aria-hidden />
            {chatModuleLabel(message.moduleId)}
          </span>
        )}
        {attachments.length > 0 && (
          <MessageAttachments
            attachments={attachments}
            conversationId={conversationId}
            onOpenAttachment={
              onOpenAttachment
                ? (attachment, trigger) =>
                    onOpenAttachment(message.id, attachment, trigger)
                : undefined
            }
          />
        )}
        <UserMessageBody message={message} />
      </div>
    </div>
  );
}
