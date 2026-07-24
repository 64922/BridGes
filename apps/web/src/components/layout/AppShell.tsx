"use client";

import { useState } from "react";

import { Button } from "@/components/design-system/Button";
import { Icon } from "@/components/design-system/Icon";
import { SkipLink } from "@/components/design-system/SkipLink";
import { VisuallyHidden } from "@/components/design-system/VisuallyHidden";
import { useAuth } from "@/context/AuthContext";

import { SidebarNav } from "./SidebarNav";

interface AppShellProps {
  children: React.ReactNode;
  mode?: "account" | "project";
  projectId?: string;
}

/**
 * Authenticated application shell.
 *
 * Provides the stable top bar, skip link, and responsive sidebar navigation.
 * On desktop the navigation is a persistent left rail; on mobile it collapses
 * into a drawer toggled from the top bar.
 */
export function AppShell({ children, mode = "account", projectId }: AppShellProps) {
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const { user } = useAuth();

  return (
    <>
      <SkipLink />
      <header
        style={{
          position: "sticky",
          top: 0,
          zIndex: 20,
          height: "var(--topbar-height)",
          backgroundColor: "var(--color-surface)",
          borderBottom: "1px solid var(--color-border)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          paddingInline: "var(--space-4)",
          gap: "var(--space-4)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-3)" }}>
          <div className="mobile-nav-toggle">
            <Button
              variant="ghost"
              size="sm"
              aria-expanded={mobileNavOpen}
              aria-controls="primary-navigation"
              aria-label="打开主导航"
              onClick={() => setMobileNavOpen(true)}
              style={{
                minWidth: "var(--target-size)",
                minHeight: "var(--target-size)",
              }}
            >
              <Icon name="menu" size={20} ariaLabel="菜单" />
            </Button>
          </div>
          <a
            href="/"
            style={{
              fontFamily: "var(--font-serif)",
              fontSize: "var(--text-lg)",
              fontWeight: 600,
              color: "var(--color-text-primary)",
              textDecoration: "none",
            }}
          >
            Science Companion
          </a>
        </div>

        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-3)",
          }}
        >
          <span
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-2)",
              color: "var(--color-text-secondary)",
              fontSize: "var(--text-sm)",
            }}
          >
            <Icon name="user" size={18} aria-hidden />
            <span className="user-name">{user.name}</span>
            <VisuallyHidden>，当前账户</VisuallyHidden>
          </span>
        </div>
      </header>

      <div
        style={{
          display: "flex",
          minHeight: "calc(100vh - var(--topbar-height))",
        }}
      >
        <SidebarNav
          mode={mode}
          projectId={projectId}
          open={mobileNavOpen}
          onClose={() => setMobileNavOpen(false)}
        />
        {children}
      </div>
    </>
  );
}
