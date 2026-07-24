import { Icon } from "./Icon";

type BadgeStatus =
  | "pass"
  | "fail"
  | "unknown"
  | "waiting"
  | "blocked"
  | "running"
  | "qualified"
  | "approved"
  | "conflicted"
  | "evidence_bound";

interface StatusBadgeProps {
  status: BadgeStatus;
  label?: string;
}

const statusConfig: Record<
  BadgeStatus,
  { label: string; icon: React.ComponentProps<typeof Icon>["name"] | null; color: string; bg: string }
> = {
  pass: { label: "正常", icon: "check", color: "var(--color-status-success)", bg: "var(--color-status-success-bg)" },
  fail: { label: "异常", icon: "cross", color: "var(--color-status-error)", bg: "var(--color-status-error-bg)" },
  unknown: { label: "未知", icon: "info", color: "var(--color-status-unknown)", bg: "var(--color-status-unknown-bg)" },
  waiting: { label: "等待", icon: "alert", color: "var(--color-status-wait)", bg: "var(--color-status-wait-bg)" },
  blocked: { label: "阻塞", icon: "alert", color: "var(--color-status-error)", bg: "var(--color-status-error-bg)" },
  running: { label: "运行中", icon: "info", color: "var(--color-accent-secondary)", bg: "var(--color-status-info-bg)" },
  qualified: { label: "合格", icon: "check", color: "var(--color-status-success)", bg: "var(--color-status-success-bg)" },
  approved: { label: "已批准", icon: "check", color: "var(--color-status-success)", bg: "var(--color-status-success-bg)" },
  conflicted: { label: "冲突", icon: "alert", color: "var(--color-status-wait)", bg: "var(--color-status-wait-bg)" },
  evidence_bound: { label: "已绑定证据", icon: "check", color: "var(--color-accent-secondary)", bg: "var(--color-status-info-bg)" },
};

/**
 * Status indicator that never relies on color alone.
 *
 * Always renders an icon plus a text label, and uses `aria-label` to surface
 * the status text.
 */
export function StatusBadge({ status, label }: StatusBadgeProps) {
  const config = statusConfig[status];
  const text = label ?? config.label;

  return (
    <span
      aria-label={text}
      data-testid="status-badge"
      data-status={status}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "var(--space-1)",
        padding: "0.125rem 0.5rem",
        borderRadius: "var(--radius-full)",
        fontSize: "var(--text-sm)",
        fontWeight: 500,
        color: config.color,
        backgroundColor: config.bg,
        border: `1px solid ${config.color}`,
        lineHeight: 1.25,
      }}
    >
      {config.icon && <Icon name={config.icon} size={14} aria-hidden />}
      <span>{text}</span>
    </span>
  );
}
