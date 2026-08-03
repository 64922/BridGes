"use client";

import { Fragment, useState } from "react";

import { StateBlock } from "@/components/bridges/StateBlock";
import { Button } from "@/components/design-system/Button";
import { Icon } from "@/components/design-system/Icon";
import { SkipLink } from "@/components/design-system/SkipLink";
import { useAuth } from "@/context/AuthContext";

import { AppSidebar } from "./AppSidebar";
import { MainContent } from "./MainContent";
import { SidebarNav } from "./SidebarNav";

interface AppShellProps {
  children: React.ReactNode;
  mode?: "account" | "project";
  projectId?: string;
  showSkipLink?: boolean;
}

/**
 * Authenticated application shell.
 *
 * Account mode (the normal user path, Issue 12) uses the ChatGPT-desktop-style
 * global layout: collapsible `AppSidebar` + content area, no persistent top
 * bar. Project mode keeps the legacy workbench chrome (top bar + `SidebarNav`)
 * while project workbench pages are replaced incrementally (ADR-0016).
 */
export function AppShell({ children, mode = "account", projectId, showSkipLink = true }: AppShellProps) {
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const { accountRevision, authState, sessionError, refreshSession } = useAuth();

  const protectedContent =
    authState === "authenticated" ? (
      <Fragment key={accountRevision}>{children}</Fragment>
    ) : (
      <MainContent>
        {authState === "loading" ? (
          <StateBlock
            kind="loading"
            title="正在验证当前账户"
            description="确认会话有效后再显示受保护内容。"
          />
        ) : authState === "error" ? (
          <StateBlock
            kind="error"
            title="暂时无法验证会话"
            description={sessionError || "请检查连接后重试。"}
            actionLabel="重新验证"
            onAction={() => void refreshSession()}
          />
        ) : (
          <StateBlock
            kind="permission"
            title="需要重新登录"
            description="当前会话已过期或被撤销，受保护内容未被加载。"
            actionLabel="前往登录"
            onAction={() => window.location.replace("/login")}
          />
        )}
      </MainContent>
    );

  if (mode === "project") {
    return (
      <>
        {showSkipLink && <SkipLink />}
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

          <p
            className="mobile-hide"
            style={{ color: "var(--color-text-tertiary)", fontSize: "var(--text-sm)" }}
          >
            科学项目空间
          </p>
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
          {protectedContent}
        </div>
      </>
    );
  }

  return (
    <>
      {showSkipLink && <SkipLink />}
      <div
        style={
          {
            display: "flex",
            minHeight: "100vh",
            // 无顶栏外壳：内容区占满视口高度（MainContent/聊天列据此计算）
            "--shell-chrome-height": "0px",
          } as React.CSSProperties
        }
      >
        <AppSidebar />
        {protectedContent}
      </div>
    </>
  );
}
