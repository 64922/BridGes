import { Icon } from "./Icon";

interface ErrorSummaryProps {
  title?: string;
  errors: string[];
}

/**
 * Accessible error summary.
 *
 * Rendered with `role="alert"` so screen readers announce it immediately when
 * it appears. Used for form validation and blocking state summaries.
 */
export function ErrorSummary({ title = "请检查以下问题", errors }: ErrorSummaryProps) {
  if (errors.length === 0) {
    return null;
  }

  return (
    <div
      role="alert"
      aria-live="assertive"
      data-testid="error-summary"
      style={{
        backgroundColor: "var(--color-status-error-bg)",
        border: "1px solid var(--color-status-error)",
        borderRadius: "var(--radius-md)",
        padding: "var(--space-4)",
        color: "var(--color-status-error)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-2)",
          fontWeight: 600,
          marginBottom: "var(--space-2)",
        }}
      >
        <Icon name="alert" size={18} ariaLabel="错误" />
        {title}
      </div>
      <ul style={{ paddingLeft: "var(--space-6)", listStyle: "disc" }}>
        {errors.map((error, index) => (
          <li key={index}>{error}</li>
        ))}
      </ul>
    </div>
  );
}
