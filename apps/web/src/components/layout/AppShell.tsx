"use client";

import { useRouter } from "next/navigation";
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
  const router = useRouter();
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const { user, logout } = useAuth();

  const handleLogout = async () => {
    try {
      await logout();
      router.push("/login");
    } catch {
      // Logout failures are rare; the session will be rejected on next request.
      router.push("/login");
    }
  };

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
              aria-label={mobileNavOpen ? "关闭导航" : "打开主导航"}
              onClick={() => setMobileNavOpen(!mobileNavOpen)}
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
            BridGes
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
              minWidth: 0,
              flex: "0 1 auto",
            }}
          >
            <Icon name="user" size={18} aria-hidden />
            <span
              className="user-name mobile-hide"
              style={{
                maxWidth: "10rem",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {user?.username || "未登录"}
            </span>
            <VisuallyHidden>，当前账户</VisuallyHidden>
          </span>
          <Button
            variant="ghost"
            size="sm"
            onClick={handleLogout}
            aria-label="退出登录"
            style={{
              minWidth: "var(--target-size)",
              minHeight: "var(--target-size)",
            }}
          >
            退出
          </Button>
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
