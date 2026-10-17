"use client";

export type ChatMode = "companion" | "study";

export const CHAT_MODE_LABELS: Record<ChatMode, string> = {
  companion: "日常陪伴",
  study: "学习模式",
};

const MODES: { key: ChatMode; label: string }[] = [
  { key: "companion", label: CHAT_MODE_LABELS.companion },
  { key: "study", label: CHAT_MODE_LABELS.study },
];

interface ModeToggleProps {
  value: ChatMode;
  onChange?: (mode: ChatMode) => void;
  locked?: boolean;
  disabled?: boolean;
  studyUnavailable?: boolean;
  /** 输入区工具栏内的紧凑形态：仅收紧水平内边距，点按目标仍为 44px。 */
  compact?: boolean;
}

/**
 * 对话模式选择与锁定展示（日常陪伴 / 学习模式）。
 *
 * 空白会话允许首轮前选择；首条消息提交后只展示持久化模式，不再提供按钮。
 */
export function ModeToggle({
  value,
  onChange,
  locked = false,
  disabled = false,
  studyUnavailable = false,
  compact = false,
}: ModeToggleProps) {
  const selectedLabel = MODES.find((mode) => mode.key === value)?.label ?? value;
  const itemPadding = compact ? "0.125rem 0.5rem" : "0.25rem 0.75rem";
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
            padding: itemPadding,
            color: "var(--color-text-secondary)",
            fontSize: "var(--text-sm)",
            fontWeight: 600,
          }}
        >
          当前模式：{selectedLabel}
        </span>
      ) : (
        MODES.map((mode) => {
          const unavailable = mode.key === "study" && studyUnavailable;
          return (
            <button
              key={mode.key}
              type="button"
              aria-pressed={value === mode.key}
              data-mode={mode.key}
              disabled={disabled || unavailable}
              title={unavailable ? "学习模式将在对应学习切片验收后开放。" : undefined}
              onClick={() => onChange?.(mode.key)}
              style={{
                minHeight: "var(--target-size)",
                padding: itemPadding,
                border: "none",
                borderRadius: "var(--radius-sm)",
                fontSize: "var(--text-sm)",
                cursor: disabled || unavailable ? "not-allowed" : "pointer",
                opacity: disabled || unavailable ? 0.65 : 1,
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
          );
        })
      )}
    </div>
  );
}
