"use client";

import { Icon } from "@/components/design-system/Icon";

interface ChatSendErrorBannerProps {
  message: string;
  /** 错误横幅的对齐方式（问候区居中 / 输入区顶部通栏） */
  align?: "center" | "stretch";
}

/**
 * 发送失败横幅：展示服务端返回的可操作中文提示。
 * GQ-02 起不再附带密钥设置页入口——认证类错误由服务端提示
 * 检查启动服务的全局百炼配置与权限。
 */
export function ChatSendErrorBanner({ message, align = "stretch" }: ChatSendErrorBannerProps) {
  return (
    <div
      role="alert"
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: align === "center" ? "center" : "flex-start",
        gap: "var(--space-2)",
        padding: "var(--space-3)",
        borderRadius: align === "center" ? "var(--radius-md)" : 0,
        border: align === "center" ? "1px solid var(--color-status-error)" : "none",
        borderTop: align === "stretch" ? "1px solid var(--color-status-error)" : undefined,
        backgroundColor: "var(--color-status-error-bg)",
        color: "var(--color-status-error)",
        fontSize: "var(--text-sm)",
        flexWrap: "wrap",
      }}
    >
      <Icon name="alert" size={16} aria-hidden />
      <span>{message}</span>
    </div>
  );
}
