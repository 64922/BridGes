"use client";

import { Icon } from "@/components/design-system/Icon";
import type { ChatMessageView } from "@/lib/chat-message-view";

import { MarkdownBody } from "./MarkdownBody";

function PlainTextBody({ text }: { text: string }) {
  return (
    <p style={{ whiteSpace: "pre-wrap", overflowWrap: "break-word" }}>
      {text}
    </p>
  );
}

export function UserMessageBody({ message }: { message: ChatMessageView }) {
  if (message.body !== undefined) return <>{message.body}</>;
  return <PlainTextBody text={message.plainText} />;
}

export function MessageErrorAlert({
  text,
  testId,
}: {
  text: string;
  testId?: string;
}) {
  return (
    <div
      role="alert"
      data-testid={testId}
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: "var(--space-2)",
        padding: "var(--space-3)",
        borderRadius: "var(--radius-md)",
        border: "1px solid var(--color-status-error)",
        backgroundColor: "var(--color-status-error-bg)",
        color: "var(--color-status-error)",
        overflowWrap: "break-word",
      }}
    >
      <Icon name="alert" size={18} aria-hidden />
      <span style={{ fontSize: "var(--text-sm)" }}>{text}</span>
    </div>
  );
}

export function AssistantBody({
  message,
  conversationId,
}: {
  message: ChatMessageView;
  conversationId?: string;
}) {
  const body =
    message.body !== undefined ? (
      <>{message.body}</>
    ) : (
      <MarkdownBody
        text={message.plainText}
        streaming={message.status === "streaming"}
      />
    );

  if (message.status === "error" && message.skill == null) {
    return (
      <>
        {/* 部分交付的正文已经过提交门，例如总结失败前的判定反馈。 */}
        {message.turnResult?.outcome === "partial" && body}
        <MessageErrorAlert text={message.errorText ?? ""} />
      </>
    );
  }

  if (message.humanizer?.article?.delivery_status === "failed") {
    return (
      <MessageErrorAlert
        testId="humanizer-not-delivered"
        text={message.humanizer.error_message ?? "正文未交付。"}
      />
    );
  }

  if (conversationId && message.tiebaResearch) {
    // 贴吧卡片已经呈现完整证据；正文仍保留在 plainText 供复制与导出。
    return null;
  }

  return body;
}
