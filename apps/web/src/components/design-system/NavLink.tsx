"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

interface NavLinkProps {
  href: string;
  children: React.ReactNode;
  className?: string;
  activeClassName?: string;
  exact?: boolean;
  onNavigate?: () => void;
}

/**
 * Navigation link that communicates the current page to assistive technology.
 *
 * Uses `aria-current="page"` when the link matches the active pathname.
 */
export function NavLink({
  href,
  children,
  className,
  activeClassName,
  exact = false,
  onNavigate,
}: NavLinkProps) {
  const pathname = usePathname();
  const isActive = exact ? pathname === href : pathname.startsWith(href);

  return (
    <Link
      href={href}
      aria-current={isActive ? "page" : undefined}
      onClick={onNavigate}
      className={`${className ?? ""} ${isActive ? activeClassName ?? "" : ""}`.trim() || undefined}
      style={{
        display: "flex",
        alignItems: "center",
        gap: "var(--space-2)",
        padding: "var(--space-3) var(--space-4)",
        borderRadius: "var(--radius-md)",
        textDecoration: "none",
        color: isActive ? "var(--color-accent-primary)" : "var(--color-text-secondary)",
        backgroundColor: isActive ? "var(--color-bg-secondary)" : "transparent",
        fontWeight: isActive ? 600 : 400,
        transition: "background-color var(--motion-duration-fast) var(--motion-easing)",
      }}
    >
      {children}
    </Link>
  );
}
