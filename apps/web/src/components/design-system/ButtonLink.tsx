import Link from "next/link";

interface ButtonLinkProps {
  href: string;
  children: React.ReactNode;
  variant?: "primary" | "secondary";
  size?: "md" | "lg";
  ariaLabel?: string;
}

/**
 * A link styled as a button for prominent call-to-action navigation.
 *
 * Uses Next.js `Link` so client-side routing works, while keeping the
 * semantics and keyboard behavior of an anchor.
 */
export function ButtonLink({
  href,
  children,
  variant = "primary",
  size = "md",
  ariaLabel,
}: ButtonLinkProps) {
  const isPrimary = variant === "primary";
  const isLarge = size === "lg";

  return (
    <Link
      href={href}
      aria-label={ariaLabel}
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        gap: "var(--space-2)",
        borderRadius: "var(--radius-md)",
        fontWeight: 500,
        textDecoration: "none",
        minHeight: isLarge ? "3rem" : "var(--target-size)",
        padding: isLarge ? "0.75rem 1.5rem" : "0.625rem 1rem",
        fontSize: isLarge ? "var(--text-lg)" : "var(--text-base)",
        backgroundColor: isPrimary ? "var(--color-accent-primary)" : "var(--color-surface)",
        color: isPrimary ? "var(--color-text-on-accent)" : "var(--color-text-primary)",
        border: `1px solid ${isPrimary ? "var(--color-accent-primary)" : "var(--color-border-strong)"}`,
        transition:
          "background-color var(--motion-duration-base) var(--motion-easing), " +
          "border-color var(--motion-duration-base) var(--motion-easing)",
      }}
    >
      {children}
    </Link>
  );
}
