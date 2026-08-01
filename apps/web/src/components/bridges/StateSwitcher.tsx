"use client";

import type { StateKind } from "./StateBlock";

export type TemplateState = "normal" | StateKind;

const STATES: { key: TemplateState; label: string }[] = [
  { key: "normal", label: "正常" },
  { key: "loading", label: "加载中" },
  { key: "empty", label: "空" },
  { key: "error", label: "错误" },
  { key: "permission", label: "未登录" },
  { key: "success", label: "成功" },
  { key: "recovery", label: "恢复" },
];

interface StateSwitcherProps {
  value: TemplateState;
  onChange: (state: TemplateState) => void;
}

/**
 * 模板状态切换器（仅设计基线模板使用）。
 * 让验收者能在同一页面检查正常 / 加载中 / 空 / 错误 / 未登录 / 成功 / 恢复状态。
 */
export function StateSwitcher({ value, onChange }: StateSwitcherProps) {
  return (
    <div
      role="group"
      aria-label="模板状态"
      data-testid="state-switcher"
      style={{
        display: "inline-flex",
        gap: "2px",
        padding: "2px",
        borderRadius: "var(--radius-md)",
        border: "1px solid var(--color-border)",
        backgroundColor: "var(--color-bg-secondary)",
      }}
    >
      {STATES.map((state) => (
        <button
          key={state.key}
          type="button"
          aria-pressed={value === state.key}
          data-state={state.key}
          onClick={() => onChange(state.key)}
          style={{
            minHeight: "var(--target-size)",
            padding: "0.25rem 0.75rem",
            border: "none",
            borderRadius: "var(--radius-sm)",
            fontSize: "var(--text-sm)",
            cursor: "pointer",
            backgroundColor:
              value === state.key ? "var(--color-surface)" : "transparent",
            color:
              value === state.key
                ? "var(--color-accent-primary)"
                : "var(--color-text-secondary)",
            fontWeight: value === state.key ? 600 : 400,
            boxShadow: value === state.key ? "var(--shadow-sm)" : "none",
          }}
        >
          {state.label}
        </button>
      ))}
    </div>
  );
}
