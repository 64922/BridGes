import { Button } from "@/components/design-system/Button";
import { Icon, type IconName } from "@/components/design-system/Icon";

export type StateKind = "loading" | "empty" | "error" | "permission" | "success" | "recovery";

interface StateBlockProps {
  kind: StateKind;
  title: string;
  description?: string;
  actionLabel?: string;
  onAction?: () => void;
}

const kindConfig: Record<StateKind, { icon: IconName; color: string }> = {
  loading: { icon: "info", color: "var(--color-text-secondary)" },
  empty: { icon: "info", color: "var(--color-text-tertiary)" },
  error: { icon: "alert", color: "var(--color-status-error)" },
  permission: { icon: "account", color: "var(--color-status-wait)" },
  success: { icon: "check", color: "var(--color-status-success)" },
  recovery: { icon: "retry", color: "var(--color-accent-primary)" },
};

/**
 * 页面级状态块：加载中 / 空 / 错误 / 未登录（权限）/ 成功 / 恢复。
 *
 * 所有状态都有图标 + 中文文字说明，不只靠颜色表达；
 * 加载用 role="status"、错误用 role="alert" 向辅助技术播报。
 */
export function StateBlock({ kind, title, description, actionLabel, onAction }: StateBlockProps) {
  const config = kindConfig[kind];
  const role = kind === "error" ? "alert" : kind === "loading" || kind === "success" || kind === "recovery" ? "status" : undefined;

  return (
    <div
      role={role}
      data-testid={`state-${kind}`}
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: "var(--space-3)",
        padding: "var(--space-12) var(--space-6)",
        textAlign: "center",
      }}
    >
      {kind === "loading" ? (
        <span className="bg-spinner" aria-hidden="true" />
      ) : (
        <span style={{ color: config.color, display: "inline-flex" }}>
          <Icon name={config.icon} size={32} aria-hidden />
        </span>
      )}
      <p style={{ fontWeight: 600, color: "var(--color-text-primary)" }}>{title}</p>
      {description && (
        <p
          style={{
            color: kind === "error" ? "var(--color-status-error)" : "var(--color-text-secondary)",
            fontSize: "var(--text-sm)",
            maxWidth: "48ch",
          }}
        >
          {description}
        </p>
      )}
      {actionLabel && onAction && (
        <Button variant="secondary" size="sm" onClick={onAction} aria-label={actionLabel}>
          {actionLabel}
        </Button>
      )}
    </div>
  );
}
