"use client";

interface FormFieldProps {
  id: string;
  label: string;
  type?: string;
  value: string;
  onChange: (value: string) => void;
  error?: string;
  hint?: string;
  required?: boolean;
  autoComplete?: string;
  placeholder?: string;
}

/**
 * 表单字段：显式 label + 输入框 + 提示 / 错误文本。
 *
 * 错误通过 aria-invalid 与 aria-describedby 暴露给辅助技术，
 * 并以文字说明问题，不只靠红色边框。
 */
export function FormField({
  id,
  label,
  type = "text",
  value,
  onChange,
  error,
  hint,
  required = false,
  autoComplete,
  placeholder,
}: FormFieldProps) {
  const describedBy = [error ? `${id}-error` : null, hint ? `${id}-hint` : null]
    .filter(Boolean)
    .join(" ") || undefined;

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
      <input
        id={id}
        name={id}
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        required={required}
        autoComplete={autoComplete}
        placeholder={placeholder}
        aria-invalid={Boolean(error) || undefined}
        aria-describedby={describedBy}
        style={{
          minHeight: "var(--target-size)",
          padding: "0.625rem var(--space-3)",
          borderRadius: "var(--radius-md)",
          border: `1px solid ${error ? "var(--color-status-error)" : "var(--color-border-strong)"}`,
          fontSize: "var(--text-base)",
          backgroundColor: "var(--color-surface)",
          color: "var(--color-text-primary)",
        }}
      />
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
