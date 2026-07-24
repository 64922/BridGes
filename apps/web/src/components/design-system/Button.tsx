import type { ButtonHTMLAttributes } from "react";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md" | "lg";
  isLoading?: boolean;
}

/**
 * Accessible button using design tokens.
 *
 * - Minimum touch target is honored via padding.
 * - Focus is visible via :focus-visible.
 * - Loading state is announced through aria-busy and a hidden status message.
 */
export function Button({
  children,
  variant = "primary",
  size = "md",
  isLoading = false,
  disabled,
  style,
  ...rest
}: ButtonProps) {
  return (
    <button
      type="button"
      disabled={disabled || isLoading}
      aria-busy={isLoading || undefined}
      data-variant={variant}
      data-size={size}
      data-testid="sc-button"
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        gap: "var(--space-2)",
        border: "1px solid transparent",
        borderRadius: "var(--radius-md)",
        fontWeight: 500,
        lineHeight: 1,
        cursor: disabled || isLoading ? "not-allowed" : "pointer",
        opacity: disabled || isLoading ? 0.6 : 1,
        transition:
          "background-color var(--motion-duration-base) var(--motion-easing), " +
          "border-color var(--motion-duration-base) var(--motion-easing), " +
          "color var(--motion-duration-base) var(--motion-easing)",
        ...variantStyles[variant],
        ...sizeStyles[size],
        ...style,
      }}
      {...rest}
    >
      {children}
      {isLoading && (
        <span className="sc-visually-hidden" role="status" aria-live="polite">
          加载中
        </span>
      )}
    </button>
  );
}

const variantStyles: Record<NonNullable<ButtonProps["variant"]>, React.CSSProperties> = {
  primary: {
    backgroundColor: "var(--color-accent-primary)",
    color: "var(--color-text-on-accent)",
    borderColor: "var(--color-accent-primary)",
  },
  secondary: {
    backgroundColor: "var(--color-surface)",
    color: "var(--color-text-primary)",
    borderColor: "var(--color-border-strong)",
  },
  ghost: {
    backgroundColor: "transparent",
    color: "var(--color-text-secondary)",
    borderColor: "transparent",
  },
  danger: {
    backgroundColor: "var(--color-status-error-bg)",
    color: "var(--color-status-error)",
    borderColor: "var(--color-status-error)",
  },
};

const sizeStyles: Record<NonNullable<ButtonProps["size"]>, React.CSSProperties> = {
  sm: {
    minHeight: "2.25rem",
    padding: "0.375rem 0.75rem",
    fontSize: "var(--text-sm)",
  },
  md: {
    minHeight: "var(--target-size)",
    padding: "0.625rem 1rem",
    fontSize: "var(--text-base)",
  },
  lg: {
    minHeight: "3rem",
    padding: "0.75rem 1.5rem",
    fontSize: "var(--text-lg)",
  },
};
