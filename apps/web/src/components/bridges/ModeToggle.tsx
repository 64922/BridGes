"use client";

export type ChatMode = "companion" | "study";

const MODES: { key: ChatMode; label: string }[] = [
  { key: "companion", label: "日常陪伴" },
  { key: "study", label: "学习模式" },
];

interface ModeToggleProps {
  value: ChatMode;
  onChange?: (mode: ChatMode) => void;
  locked?: boolean;
}

/**
 * 对话模式选择与锁定展示（日常陪伴 / 学习模式）。
 *
 * 空白会话允许首轮前选择；首条消息提交后只展示持久化模式，不再提供按钮。
 */
export function ModeToggle({ value, onChange, locked = false }: ModeToggleProps) {
  const selectedLabel = MODES.find((mode) => mode.key === value)?.label ?? value;
  return (
    <div
      role="group"
      aria-label="对话模式"
      data-testid="mode-toggle"
      data-locked={locked ? "true" : "false"}
      style={{
        display: "inline-flex",
        gap: "2px",
        padding: "2px",
        borderRadius: "var(--radius-md)",
        border: "1px solid var(--color-border)",
        backgroundColor: "var(--color-bg-secondary)",
        flexShrink: 0,
      }}
    >
      {locked ? (
        <span
          data-testid="mode-display"
          style={{
            minHeight: "var(--target-size)",
            display: "inline-flex",
            alignItems: "center",
            padding: "0.25rem 0.75rem",
            color: "var(--color-text-secondary)",
            fontSize: "var(--text-sm)",
            fontWeight: 600,
          }}
        >
          当前模式：{selectedLabel}
        </span>
      ) : MODES.map((mode) => (
        <button
          key={mode.key}
          type="button"
          aria-pressed={value === mode.key}
          data-mode={mode.key}
          onClick={() => onChange?.(mode.key)}
          style={{
            minHeight: "var(--target-size)",
            padding: "0.25rem 0.75rem",
            border: "none",
            borderRadius: "var(--radius-sm)",
            fontSize: "var(--text-sm)",
            cursor: "pointer",
            backgroundColor: value === mode.key ? "var(--color-surface)" : "transparent",
            color:
              value === mode.key
                ? "var(--color-accent-primary)"
                : "var(--color-text-secondary)",
            fontWeight: value === mode.key ? 600 : 400,
            boxShadow: value === mode.key ? "var(--shadow-sm)" : "none",
          }}
        >
          {mode.label}
        </button>
      ))}
    </div>
  );
}
