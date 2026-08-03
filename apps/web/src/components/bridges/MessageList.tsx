"use client";

import { useEffect, useRef, useState } from "react";

import { Icon, type IconName } from "@/components/design-system/Icon";
import { copyTextToClipboard } from "@/lib/clipboard";
import { BrandLogo } from "./BrandLogo";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  /** 消息的纯文本（用于复制与朗读） */
  plainText: string;
  /** 富内容（段落、代码块、公式、表格等），由页面组装 */
  content: React.ReactNode;
  thinking?: { seconds: number; steps: string[] };
  status?: "done" | "streaming" | "error";
  errorText?: string;
  /** Issue 11：该轮用户消息之下的历史助手尝试（重试保留审计，不静默改写） */
  previousAttempts?: {
    attemptNumber: number;
    status: "done" | "streaming" | "error" | "stopped";
    errorMessage?: string | null;
  }[];
}

interface MessageListProps {
  messages: ChatMessage[];
  onRetry?: (id: string) => void;
}

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
  onClick,
}: {
  icon: IconName;
  label: string;
  pressed?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      aria-pressed={pressed}
      onClick={onClick}
      style={{
        ...actionButtonStyle,
        color: pressed ? "var(--color-accent-primary)" : "var(--color-text-tertiary)",
      }}
    >
      <Icon name={icon} size={18} aria-hidden />
    </button>
  );
}

function AssistantActions({ message, onRetry }: { message: ChatMessage; onRetry?: (id: string) => void }) {
  const [copyStatus, setCopyStatus] = useState<"idle" | "success" | "error">("idle");
  const [feedback, setFeedback] = useState<"good" | "bad" | null>(null);
  const [reading, setReading] = useState(false);
  const [speechError, setSpeechError] = useState("");
  const utteranceRef = useRef<SpeechSynthesisUtterance | null>(null);

  const copy = async () => {
    const copied = await copyTextToClipboard(message.plainText);
    setCopyStatus(copied ? "success" : "error");
    window.setTimeout(() => setCopyStatus("idle"), 2000);
  };

  const toggleReading = () => {
    if (reading) {
      window.speechSynthesis.cancel();
      utteranceRef.current = null;
      setReading(false);
      return;
    }
    if (!("speechSynthesis" in window) || !("SpeechSynthesisUtterance" in window)) {
      setSpeechError("当前浏览器不支持朗读。");
      return;
    }

    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(message.plainText);
    utterance.lang = "zh-CN";
    utterance.onend = () => {
      utteranceRef.current = null;
      setReading(false);
    };
    utterance.onerror = () => {
      utteranceRef.current = null;
      setReading(false);
      setSpeechError("朗读失败，请检查系统语音设置后重试。");
    };
    setSpeechError("");
    utteranceRef.current = utterance;
    setReading(true);
    window.speechSynthesis.speak(utterance);
  };

  useEffect(
    () => () => {
      if (utteranceRef.current) window.speechSynthesis.cancel();
    },
    [],
  );

  return (
    <div
      role="toolbar"
      aria-label="消息操作"
      style={{ display: "flex", alignItems: "center", gap: "var(--space-1)", marginTop: "var(--space-2)" }}
    >
      <MessageAction icon="copy" label={copyStatus === "success" ? "已复制" : "复制"} onClick={copy} />
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
      <MessageAction icon="retry" label="重试" onClick={() => onRetry?.(message.id)} />
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
      <MessageAction
        icon="readAloud"
        label={reading ? "停止朗读" : "朗读"}
        pressed={reading}
        onClick={toggleReading}
      />
      {reading && (
        <span role="status" style={{ fontSize: "var(--text-xs)", color: "var(--color-accent-primary)" }}>
          朗读中…
        </span>
      )}
      {feedback && (
        <span role="status" style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
          已在当前页面标记为{feedback === "good" ? "有帮助" : "需改进"}
        </span>
      )}
      {speechError && (
        <span role="alert" style={{ fontSize: "var(--text-xs)", color: "var(--color-status-error)" }}>
          {speechError}
        </span>
      )}
    </div>
  );
}

/**
 * 消息流：用户消息气泡靠右，助手消息占整列并带操作行；
 * 思考摘要在生成后折叠为「已思考（用时 X 秒）」，可随时展开。
 */
export function MessageList({ messages, onRetry }: MessageListProps) {
  return (
    <ol
      role="list"
      aria-label="对话消息"
      style={{ display: "flex", flexDirection: "column", gap: "var(--space-6)" }}
    >
      {messages.map((message) => (
        <li key={message.id}>
          {message.role === "user" ? (
            <div style={{ display: "flex", justifyContent: "flex-end" }}>
              <div
                style={{
                  maxWidth: "85%",
                  padding: "var(--space-3) var(--space-4)",
                  borderRadius: "var(--radius-xl)",
                  backgroundColor: "var(--color-accent-primary-soft)",
                  color: "var(--color-text-primary)",
                  overflowWrap: "break-word",
                }}
              >
                {message.content}
              </div>
            </div>
          ) : (
            <article style={{ display: "flex", gap: "var(--space-3)", minWidth: 0 }}>
              <span style={{ flexShrink: 0, paddingTop: "var(--space-1)" }} aria-hidden="true">
                <BrandLogo variant="icon" width={24} />
              </span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <p
                  style={{
                    fontWeight: 600,
                    fontSize: "var(--text-sm)",
                    color: "var(--color-text-secondary)",
                    marginBottom: "var(--space-1)",
                  }}
                >
                  BridGes
                </p>

                {message.previousAttempts && message.previousAttempts.length > 0 && (
                  <details
                    style={{
                      marginBottom: "var(--space-3)",
                      border: "1px solid var(--color-border)",
                      borderRadius: "var(--radius-md)",
                      padding: "var(--space-2) var(--space-3)",
                      backgroundColor: "var(--color-bg-secondary)",
                      fontSize: "var(--text-sm)",
                    }}
                  >
                    <summary style={{ cursor: "pointer", color: "var(--color-text-secondary)" }}>
                      此问题的前 {message.previousAttempts.length} 次尝试
                    </summary>
                    <ol
                      style={{
                        marginTop: "var(--space-2)",
                        paddingLeft: "var(--space-4)",
                        display: "flex",
                        flexDirection: "column",
                        gap: "var(--space-2)",
                      }}
                    >
                      {message.previousAttempts.map((attempt) => (
                        <li key={attempt.attemptNumber} style={{ color: "var(--color-text-secondary)" }}>
                          {attempt.status === "error" || attempt.status === "stopped" ? (
                            <span style={{ color: "var(--color-status-error)" }}>
                              第 {attempt.attemptNumber} 次尝试（
                              {attempt.status === "stopped" ? "已停止" : "失败"}）
                              {attempt.errorMessage ? `：${attempt.errorMessage}` : ""}
                            </span>
                          ) : (
                            <span>第 {attempt.attemptNumber} 次尝试</span>
                          )}
                        </li>
                      ))}
                    </ol>
                  </details>
                )}

                {message.thinking && (
                  <details
                    style={{
                          marginBottom: "var(--space-3)",
                          border: "1px solid var(--color-border)",
                          borderRadius: "var(--radius-md)",
                          padding: "var(--space-2) var(--space-3)",
                          backgroundColor: "var(--color-bg-secondary)",
                        }}
                  >
                    <summary
                      style={{
                        cursor: "pointer",
                        fontSize: "var(--text-sm)",
                        color: "var(--color-text-secondary)",
                      }}
                    >
                      已思考（用时 {message.thinking.seconds} 秒）
                    </summary>
                    <ol
                      style={{
                        marginTop: "var(--space-2)",
                        paddingLeft: "var(--space-5)",
                        listStyle: "decimal",
                        fontSize: "var(--text-sm)",
                        color: "var(--color-text-secondary)",
                        display: "flex",
                        flexDirection: "column",
                        gap: "var(--space-1)",
                      }}
                    >
                      {message.thinking.steps.map((step) => (
                        <li key={step}>{step}</li>
                      ))}
                    </ol>
                  </details>
                )}

                {message.status === "error" ? (
                  <div
                    role="alert"
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
                    <span style={{ fontSize: "var(--text-sm)" }}>{message.errorText}</span>
                  </div>
                ) : (
                  message.content
                )}

                {message.status === "streaming" && (
                  <p role="status" style={{ fontSize: "var(--text-sm)", color: "var(--color-text-tertiary)" }}>
                    正在生成回答…
                  </p>
                )}

                <AssistantActions message={message} onRetry={onRetry} />
              </div>
            </article>
          )}
        </li>
      ))}
    </ol>
  );
}
