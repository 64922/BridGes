"use client";

import { useState } from "react";

import { Icon, type IconName } from "@/components/design-system/Icon";
import { copyTextToClipboard } from "@/lib/clipboard";

import type { ChatMessageView } from "@/lib/chat-message-view";

const actionButtonStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  minWidth: "var(--target-size)",
  minHeight: "var(--target-size)",
  border: "none",
  borderRadius: "var(--radius-md)",
  backgroundColor: "transparent",
  color: "var(--color-text-tertiary)",
  cursor: "pointer",
};

function MessageAction({
  icon,
  label,
  pressed,
  disabled,
  onClick,
}: {
  icon: IconName;
  label: string;
  pressed?: boolean;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      aria-pressed={pressed}
      disabled={disabled}
      onClick={onClick}
      style={{
        ...actionButtonStyle,
        color: pressed ? "var(--color-accent-primary)" : "var(--color-text-tertiary)",
        opacity: disabled ? 0.45 : 1,
        cursor: disabled ? "not-allowed" : "pointer",
      }}
    >
      <Icon name={icon} size={18} aria-hidden />
    </button>
  );
}

export function AssistantActions({
  message,
  onRetry,
}: {
  message: ChatMessageView;
  onRetry?: (id: string) => void;
}) {
  const [copyStatus, setCopyStatus] = useState<"idle" | "success" | "error">("idle");
  const [feedback, setFeedback] = useState<"good" | "bad" | null>(null);

  const copy = async () => {
    const copied = await copyTextToClipboard(message.plainText);
    setCopyStatus(copied ? "success" : "error");
    window.setTimeout(() => setCopyStatus("idle"), 2000);
  };

  return (
    <div
      role="toolbar"
      aria-label="消息操作"
      style={{
        display: "flex",
        alignItems: "center",
        gap: "var(--space-1)",
        marginTop: "var(--space-2)",
      }}
    >
      <MessageAction
        icon="copy"
        label={copyStatus === "success" ? "已复制" : "复制"}
        onClick={copy}
      />
      {copyStatus !== "idle" && (
        <span
          role={copyStatus === "error" ? "alert" : "status"}
          style={{
            fontSize: "var(--text-xs)",
            color:
              copyStatus === "error"
                ? "var(--color-status-error)"
                : "var(--color-status-success)",
          }}
        >
          {copyStatus === "success" ? "已复制" : "复制失败，请检查浏览器权限"}
        </span>
      )}
      {/* 人味化写路径退役，历史人味化消息不再提供重试。 */}
      {message.humanizer == null &&
        message.humanizerProcess == null &&
        message.skill == null && (
          <MessageAction
            icon="retry"
            label="重试"
            onClick={() => onRetry?.(message.id)}
          />
        )}
      <MessageAction
        icon="feedbackGood"
        label="回答有帮助"
        pressed={feedback === "good"}
        onClick={() => setFeedback((value) => (value === "good" ? null : "good"))}
      />
      <MessageAction
        icon="feedbackBad"
        label="回答需改进"
        pressed={feedback === "bad"}
        onClick={() => setFeedback((value) => (value === "bad" ? null : "bad"))}
      />
      {feedback && (
        <span
          role="status"
          style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}
        >
          已在当前页面标记为{feedback === "good" ? "有帮助" : "需改进"}
        </span>
      )}
    </div>
  );
}
