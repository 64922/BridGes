"use client";

export type ChatMode = "companion" | "study";

const MODES: { key: ChatMode; label: string }[] = [
  { key: "companion", label: "日常陪伴" },
  { key: "study", label: "学习模式" },
];

interface ModeToggleProps {
  value: ChatMode;
  onChange: (mode: ChatMode) => void;
}

/**
 * 对话模式切换（日常陪伴 / 学习模式）。
 *
 * 每个对话持久化一个当前模式（见 docs/adr/0022）；
 * 切换只影响后续消息，不重写历史回答。
 */
export function ModeToggle({ value, onChange }: ModeToggleProps) {
  return (
    <div
      role="group"
      aria-label="对话模式"
      data-testid="mode-toggle"
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
      {MODES.map((mode) => (
        <button
          key={mode.key}
          type="button"
          aria-pressed={value === mode.key}
          data-mode={mode.key}
          onClick={() => onChange(mode.key)}
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
