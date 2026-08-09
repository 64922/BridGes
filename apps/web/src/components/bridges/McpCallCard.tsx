/**
 * 消息内 MCP 调用结果卡（Issue 36）。
 *
 * 挂载在助手消息上：展示真实调用结果（成功结果摘要 / 失败原因 / 敏感
 * 操作挂起确认 / 已拒绝）。敏感挂起由确认对话框继续（approve/deny 走
 * chat 域路由，结果写回消息投影，刷新可恢复）。状态卡键盘可达，失败
 * 原因中文可操作。
 */
import { useCallback, useState } from "react";

import type { McpCallMessageProjection } from "@/lib/api";
import { Icon } from "@/components/design-system/Icon";

interface McpCallCardProps {
  call: McpCallMessageProjection;
  /** 本条消息标识（敏感确认路由按消息定位，宿主据此调真实 API）。 */
  messageId: string;
  /** 敏感操作确认（approve/deny 由宿主接入真实 API）。 */
  onConfirm?: (
    messageId: string,
    confirmationId: string,
    action: "approve" | "deny"
  ) => Promise<void> | void;
}

function statusLabel(status: McpCallMessageProjection["status"]): string {
  switch (status) {
    case "loading":
      return "调用中…";
    case "succeeded":
      return "调用成功";
    case "failed":
      return "调用失败";
    case "sensitive_pending":
      return "等待敏感操作确认";
    case "denied":
      return "已拒绝敏感操作";
  }
}

export function McpCallCard({ call, messageId, onConfirm }: McpCallCardProps) {
  const [confirming, setConfirming] = useState(false);
  const [confirmError, setConfirmError] = useState("");
  const name = call.mcp_name ?? call.mcp_id;
  const pending = call.status === "sensitive_pending" && call.confirmation !== null;

  const confirm = useCallback(
    async (action: "approve" | "deny") => {
      if (!call.confirmation || !onConfirm) return;
      setConfirming(true);
      setConfirmError("");
      try {
        await onConfirm(messageId, call.confirmation.confirmation_id, action);
      } catch (error) {
        setConfirmError(error instanceof Error ? error.message : "确认失败，请稍后重试。");
      } finally {
        setConfirming(false);
      }
    },
    [call.confirmation, messageId, onConfirm]
  );

  const tone =
    call.status === "succeeded"
      ? "var(--color-status-success)"
      : call.status === "failed" || call.status === "denied"
        ? "var(--color-status-error)"
        : call.status === "sensitive_pending"
          ? "var(--color-status-wait)"
          : "var(--color-text-secondary)";

  return (
    <div
      data-testid="mcp-call-card"
      role="status"
      aria-live="polite"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-2)",
        padding: "var(--space-3)",
        borderRadius: "var(--radius-md)",
        border: `1px solid ${tone}`,
        backgroundColor: "var(--color-bg-secondary)",
        fontSize: "var(--text-sm)",
        color: "var(--color-text-primary)",
        maxWidth: "34rem",
      }}
    >
      <span style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
        <Icon name="mcpServer" size={16} aria-hidden />
        <strong data-testid="mcp-call-status">{statusLabel(call.status)}</strong>
        <span style={{ color: "var(--color-text-secondary)" }}>
          「{name}」工具 {call.tool}
        </span>
        {call.input_summary && (
          <span
            data-testid="mcp-call-input"
            title={call.input_summary}
            style={{
              marginLeft: "auto",
              maxWidth: "12rem",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              fontSize: "var(--text-xs)",
              color: "var(--color-text-tertiary)",
            }}
          >
            {call.input_summary}
          </span>
        )}
      </span>
      {call.result_summary && (
        <pre
          data-testid="mcp-call-result"
          style={{
            margin: 0,
            padding: "var(--space-2)",
            borderRadius: "var(--radius-sm)",
            backgroundColor: "var(--color-bg-primary)",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            fontSize: "var(--text-xs)",
            color: "var(--color-text-primary)",
            fontFamily: "var(--font-mono)",
            maxHeight: "16rem",
            overflow: "auto",
          }}
        >
          {call.result_summary}
        </pre>
      )}
      {call.error_message && (
        <p
          data-testid="mcp-call-error"
          role="alert"
          style={{ margin: 0, fontSize: "var(--text-sm)", color: "var(--color-status-error)" }}
        >
          {call.error_message}
        </p>
      )}
      {pending && call.confirmation && onConfirm && (
        <div
          data-testid="mcp-call-confirmation"
          style={{
            display: "flex",
            flexDirection: "column",
            gap: "var(--space-2)",
            padding: "var(--space-2) var(--space-3)",
            borderRadius: "var(--radius-md)",
            border: "1px solid var(--color-status-wait-bg)",
            backgroundColor: "var(--color-status-wait-bg)",
          }}
        >
          <span style={{ fontSize: "var(--text-sm)", color: "var(--color-text-primary)" }}>
            {call.confirmation.kind === "write_file" && "将写入文件"}
            {call.confirmation.kind === "run_command" && "将执行外部命令"}
            {call.confirmation.kind === "send_external" && "将向外部服务提交内容"}
            {call.confirmation.target ? `：${call.confirmation.target}` : ""}
          </span>
          <span style={{ fontSize: "var(--text-xs)", color: "var(--color-text-secondary)" }}>
            {call.confirmation.impact}
          </span>
          {confirmError && (
            <p role="alert" style={{ margin: 0, color: "var(--color-status-error)", fontSize: "var(--text-xs)" }}>
              {confirmError}
            </p>
          )}
          <span style={{ display: "flex", gap: "var(--space-2)" }}>
            <button
              type="button"
              data-testid="mcp-call-deny"
              disabled={confirming}
              onClick={() => void confirm("deny")}
              style={{
                padding: "var(--space-1) var(--space-3)",
                borderRadius: "var(--radius-md)",
                border: "1px solid var(--color-border)",
                backgroundColor: "transparent",
                color: "var(--color-text-primary)",
                cursor: "pointer",
              }}
            >
              拒绝
            </button>
            <button
              type="button"
              data-testid="mcp-call-approve"
              disabled={confirming}
              onClick={() => void confirm("approve")}
              style={{
                padding: "var(--space-1) var(--space-3)",
                borderRadius: "var(--radius-md)",
                border: "none",
                backgroundColor: "var(--color-accent-primary)",
                color: "var(--color-text-on-accent)",
                cursor: "pointer",
              }}
            >
              {confirming ? "确认中…" : "确认执行（仅本次）"}
            </button>
          </span>
        </div>
      )}
    </div>
  );
}
