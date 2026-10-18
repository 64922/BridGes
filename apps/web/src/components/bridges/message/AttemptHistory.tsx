"use client";

import type { ChatPreviousAttempt } from "@/lib/chat-message";

export function AttemptHistory({
  attempts,
}: {
  attempts: ChatPreviousAttempt[];
}) {
  return (
    <details
      data-testid="attempt-history"
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
        此问题的前 {attempts.length} 次尝试
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
        {attempts.map((attempt) => (
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
  );
}
