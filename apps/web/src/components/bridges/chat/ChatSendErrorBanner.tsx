"use client";

import { ButtonLink } from "@/components/design-system/ButtonLink";
import { Icon } from "@/components/design-system/Icon";

interface ChatSendErrorBannerProps {
  message: string;
  code?: string;
  /** 错误横幅的对齐方式（问候区居中 / 输入区顶部通栏） */
  align?: "center" | "stretch";
}

const KEY_RELATED_CODES = ["no_api_key", "capability_unavailable", "capability_probing"];

/**
 * 发送失败横幅：展示服务端预检返回的可操作中文提示，
 * 密钥相关错误附带「前往设置配置 Key」入口。
 */
export function ChatSendErrorBanner({ message, code, align = "stretch" }: ChatSendErrorBannerProps) {
  const showKeyLink = code !== undefined && KEY_RELATED_CODES.includes(code);
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
      {showKeyLink && (
        <ButtonLink href="/account/settings/keys" variant="secondary" ariaLabel="前往设置配置 Key">
          前往设置配置 Key
        </ButtonLink>
      )}
    </div>
  );
}
