"use client";

import { useState } from "react";

interface PasswordFieldProps {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  error?: string;
  hint?: string;
  required?: boolean;
  autoComplete?: string;
  placeholder?: string;
  autoFocus?: boolean;
}

/**
 * 密码字段：显式 label + 输入框 + 键盘可达的明文显隐切换。
 *
 * 显隐按钮是原生 <button type="button">，可通过 Tab 聚焦、Enter/Space 触发，
 * 状态通过 aria-pressed 与按钮文案同时暴露。错误通过 aria-invalid 与
 * aria-describedby 关联到字段。
 */
export function PasswordField({
  id,
  label,
  value,
  onChange,
  error,
  hint,
  required = false,
  autoComplete,
  placeholder,
  autoFocus = false,
}: PasswordFieldProps) {
  const [visible, setVisible] = useState(false);
  const describedBy =
    [error ? `${id}-error` : null, hint ? `${id}-hint` : null].filter(Boolean).join(" ") ||
    undefined;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
      <label htmlFor={id}>
        {label}
        {required && (
          <span aria-hidden="true" style={{ color: "var(--color-status-error)" }}>
            {" "}
            *
          </span>
        )}
      </label>
      <div style={{ display: "flex", gap: "var(--space-2)", alignItems: "stretch" }}>
        <input
          id={id}
          name={id}
          type={visible ? "text" : "password"}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          required={required}
          autoComplete={autoComplete}
          placeholder={placeholder}
          autoFocus={autoFocus}
          aria-invalid={Boolean(error) || undefined}
          aria-describedby={describedBy}
          style={{
            flex: 1,
            minHeight: "var(--target-size)",
            padding: "0.625rem var(--space-3)",
            borderRadius: "var(--radius-md)",
            border: `1px solid ${error ? "var(--color-status-error)" : "var(--color-border-strong)"}`,
            fontSize: "var(--text-base)",
            backgroundColor: "var(--color-surface)",
            color: "var(--color-text-primary)",
          }}
        />
        <button
          type="button"
          aria-pressed={visible}
          aria-controls={id}
          onClick={() => setVisible((current) => !current)}
          style={{
            minHeight: "var(--target-size)",
            minWidth: "var(--target-size)",
            padding: "0 var(--space-3)",
            borderRadius: "var(--radius-md)",
            border: "1px solid var(--color-border-strong)",
            backgroundColor: "var(--color-surface)",
            color: "var(--color-text-secondary)",
            fontSize: "var(--text-sm)",
            cursor: "pointer",
            whiteSpace: "nowrap",
          }}
        >
          {visible ? "隐藏密码" : "显示密码"}
        </button>
      </div>
      {hint && !error && (
        <p id={`${id}-hint`} style={{ fontSize: "var(--text-sm)", color: "var(--color-text-tertiary)" }}>
          {hint}
        </p>
      )}
      {error && (
        <p id={`${id}-error`} role="alert" style={{ fontSize: "var(--text-sm)", color: "var(--color-status-error)" }}>
          {error}
        </p>
      )}
    </div>
  );
}
