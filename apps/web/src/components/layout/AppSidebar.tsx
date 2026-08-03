"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { AccountMenu } from "@/components/account/AccountMenu";
import { BrandLogo } from "@/components/bridges/BrandLogo";
import { Button } from "@/components/design-system/Button";
import { Icon, type IconName } from "@/components/design-system/Icon";
import { useAuth } from "@/context/AuthContext";
import { useRecentConversations } from "@/lib/recent-conversations";

/** 侧栏收起状态的本地持久化键（与根布局内联脚本共用，避免刷新闪烁）。 */
export const SIDEBAR_COLLAPSED_KEY = "bridges-sidebar-collapsed";

interface SidebarModule {
  label: string;
  icon: IconName;
  href: string;
}

/** 普通用户侧栏功能模块（固定顺序，见 Issue 12）。 */
const SIDEBAR_MODULES: SidebarModule[] = [
  { label: "本地知识库", icon: "knowledgeBase", href: "/knowledge-base" },
  { label: "学习项目", icon: "learningProject", href: "/account/projects" },
  { label: "任务安排", icon: "tasks", href: "/tasks" },
  { label: "插件", icon: "plugins", href: "/plugins" },
  { label: "用户画像", icon: "profile", href: "/account/profile" },
];

const itemBaseStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-2)",
  width: "100%",
  minHeight: "var(--target-size)",
  padding: "var(--space-2) var(--space-3)",
  borderRadius: "var(--radius-md)",
  fontSize: "var(--text-sm)",
  textDecoration: "none",
  transition: "background-color var(--motion-duration-fast) var(--motion-easing)",
};

function itemStyle(active: boolean): React.CSSProperties {
  return {
    ...itemBaseStyle,
    color: active ? "var(--color-accent-primary)" : "var(--color-text-secondary)",
    backgroundColor: active ? "var(--color-accent-primary-soft)" : "transparent",
    fontWeight: active ? 600 : 400,
  };
}

const iconOnlyStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  minWidth: "var(--target-size)",
  minHeight: "var(--target-size)",
  padding: "0.375rem",
  borderRadius: "var(--radius-md)",
  color: "var(--color-text-secondary)",
};

/**
 * 普通用户全局侧栏（Issue 12，ChatGPT 桌面结构启发、BridGes 原创视觉）。
 *
 * 自上而下固定顺序：BridGes Logo（进入新聊天）、搜索、收起侧边栏、新聊天、
 * 本地知识库、学习项目、任务安排、插件、用户画像、最近对话（真实数据）、
 * 底部账户菜单（Issue 08）。收起后侧栏完全隐藏，内容区左上角保留
 * 「展开侧边栏 + 新聊天」恢复入口；状态持久化在 localStorage，刷新与
 * 路由切换后保持（无闪烁由根布局内联脚本 + data-sidebar-collapsed 规则保证）。
 * 激活语义：任一时刻最多一个 aria-current="page"。
 */
export function AppSidebar() {
  const pathname = usePathname();
  const { user, authState, refreshSession } = useAuth();
  const { conversations, loading, loadError, reload } = useRecentConversations();
  // 始终以展开态做首渲染（与 SSR 一致），水合后从 localStorage 同步，
  // 避免 hydration mismatch；持久化值已由内联脚本写入
  // documentElement.dataset.sidebarCollapsed 提前隐藏。
  const [collapsed, setCollapsed] = useState(false);
  const collapseButtonRef = useRef<HTMLButtonElement>(null);
  const expandButtonRef = useRef<HTMLButtonElement>(null);
  // 仅用户主动切换时移动焦点；水合后从 localStorage 同步不劫持焦点
  const focusTargetRef = useRef<"expand" | "collapse" | null>(null);

  useEffect(() => {
    try {
      setCollapsed(window.localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "1");
    } catch {
      // localStorage 不可用时保持展开
    }
  }, []);

  // 收起后焦点移到「展开侧边栏」恢复按钮，展开后回到「收起侧边栏」：
  // 触发按钮在状态切换时被卸载，不把焦点丢到 body。
  useEffect(() => {
    const target = focusTargetRef.current;
    focusTargetRef.current = null;
    if (target === "expand") {
      expandButtonRef.current?.focus();
    } else if (target === "collapse") {
      collapseButtonRef.current?.focus();
    }
  }, [collapsed]);

  const setCollapsedPersisted = (next: boolean) => {
    focusTargetRef.current = next ? "expand" : "collapse";
    setCollapsed(next);
    try {
      window.localStorage.setItem(SIDEBAR_COLLAPSED_KEY, next ? "1" : "0");
      if (next) {
        document.documentElement.dataset.sidebarCollapsed = "1";
      } else {
        delete document.documentElement.dataset.sidebarCollapsed;
      }
    } catch {
      // 忽略持久化失败，内存态仍然生效
    }
  };

  if (collapsed) {
    return (
      <div
        style={{
          position: "fixed",
          top: "var(--space-3)",
          left: "var(--space-3)",
          zIndex: 30,
          display: "flex",
          gap: "var(--space-1)",
          padding: "var(--space-1)",
          backgroundColor: "var(--color-surface)",
          border: "1px solid var(--color-border)",
          borderRadius: "var(--radius-lg)",
          boxShadow: "var(--shadow-sm)",
        }}
      >
        <button
          ref={expandButtonRef}
          type="button"
          aria-label="展开侧边栏"
          aria-expanded={false}
          aria-controls="app-sidebar-nav"
          data-testid="sidebar-expand"
          onClick={() => setCollapsedPersisted(false)}
          style={{
            ...iconOnlyStyle,
            border: "1px solid transparent",
            backgroundColor: "transparent",
            cursor: "pointer",
          }}
        >
          <Icon name="sidebarExpand" size={20} aria-hidden />
        </button>
        <Link href="/" aria-label="新聊天" style={iconOnlyStyle}>
          <Icon name="newChat" size={20} aria-hidden />
        </Link>
      </div>
    );
  }

  const isActive = (href: string) => pathname === href || pathname.startsWith(`${href}/`);

  return (
    <nav
      id="app-sidebar-nav"
      aria-label="主导航"
      data-testid="app-sidebar"
      style={{
        display: "flex",
        flexDirection: "column",
        flexShrink: 0,
        width: "var(--sidebar-width)",
        height: "100vh",
        position: "sticky",
        top: 0,
        backgroundColor: "var(--color-bg-secondary)",
        borderRight: "1px solid var(--color-border)",
        padding: "var(--space-2)",
        gap: "2px",
      }}
    >
      {/* 1. BridGes Logo：点击进入新聊天，不创建对话、永不标记当前页 */}
      <div style={{ padding: "var(--space-2) var(--space-3)" }}>
        <Link
          href="/"
          aria-label="BridGes — 新聊天"
          style={{ display: "inline-flex", borderRadius: "var(--radius-sm)" }}
        >
          <BrandLogo variant="horizontal" width={118} />
        </Link>
      </div>

      {/* 2. 搜索 */}
      <Link href="/search" aria-current={isActive("/search") ? "page" : undefined} style={itemStyle(isActive("/search"))}>
        <Icon name="search" size={18} aria-hidden />
        搜索
      </Link>

      {/* 3. 收起侧边栏 */}
      <button
        ref={collapseButtonRef}
        type="button"
        aria-label="收起侧边栏"
        aria-expanded={true}
        aria-controls="app-sidebar-nav"
        data-testid="sidebar-collapse"
        onClick={() => setCollapsedPersisted(true)}
        style={{
          ...itemStyle(false),
          border: "1px solid transparent",
          backgroundColor: "transparent",
          cursor: "pointer",
          textAlign: "left",
        }}
      >
        <Icon name="sidebarCollapse" size={18} aria-hidden />
        收起侧边栏
      </button>

      {/* 4. 新聊天：主操作样式，仅在 `/` 精确匹配时标记当前页 */}
      <Link
        href="/"
        aria-current={pathname === "/" ? "page" : undefined}
        style={{
          ...itemBaseStyle,
          border: "1px solid var(--color-border-strong)",
          backgroundColor: "var(--color-surface)",
          color: "var(--color-text-primary)",
          fontWeight: 500,
        }}
      >
        <Icon name="newChat" size={18} aria-hidden />
        新聊天
      </Link>

      {/* 5–9. 功能模块 */}
      <ul
        role="list"
        aria-label="功能模块"
        style={{ display: "flex", flexDirection: "column", gap: "2px", marginTop: "var(--space-2)" }}
      >
        {SIDEBAR_MODULES.map((module) => (
          <li key={module.href}>
            <Link
              href={module.href}
              aria-current={isActive(module.href) ? "page" : undefined}
              style={itemStyle(isActive(module.href))}
            >
              <Icon name={module.icon} size={18} aria-hidden />
              <span
                style={{
                  flex: 1,
                  minWidth: 0,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {module.label}
              </span>
            </Link>
          </li>
        ))}
      </ul>

      {/* 10. 最近对话（真实数据，可滚动区，底部菜单始终可见） */}
      <section
        aria-label="最近对话"
        style={{ flex: 1, minHeight: 0, overflowY: "auto", marginTop: "var(--space-2)" }}
      >
        <h2
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-1)",
            fontFamily: "var(--font-sans)",
            fontSize: "var(--text-xs)",
            fontWeight: 600,
            color: "var(--color-text-tertiary)",
            padding: "0 var(--space-3)",
            marginBottom: "var(--space-1)",
          }}
        >
          <Icon name="recent" size={14} aria-hidden />
          最近对话
        </h2>
        {loading && conversations.length === 0 ? (
          <p
            role="status"
            style={{
              padding: "var(--space-2) var(--space-3)",
              fontSize: "var(--text-sm)",
              color: "var(--color-text-tertiary)",
            }}
          >
            正在加载对话…
          </p>
        ) : loadError ? (
          <div
            role="alert"
            style={{
              padding: "var(--space-2) var(--space-3)",
              fontSize: "var(--text-sm)",
              color: "var(--color-status-error)",
            }}
          >
            {loadError}
            <Button variant="ghost" size="sm" onClick={() => void reload()}>
              重试
            </Button>
          </div>
        ) : conversations.length === 0 ? (
          <p
            style={{
              padding: "var(--space-2) var(--space-3)",
              fontSize: "var(--text-sm)",
              color: "var(--color-text-tertiary)",
            }}
          >
            还没有对话，点击「新聊天」开始。
          </p>
        ) : (
          <ul role="list" style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
            {conversations.map((conversation) => {
              const href = `/chat/${conversation.conversation_id}`;
              const active = pathname === href;
              return (
                <li key={conversation.conversation_id}>
                  <Link
                    href={href}
                    aria-current={active ? "page" : undefined}
                    style={itemStyle(active)}
                  >
                    <span
                      style={{
                        flex: 1,
                        minWidth: 0,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {conversation.title || "新对话"}
                    </span>
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      {/* 11. 底部账户菜单（Issue 08，固定四项） */}
      <div
        style={{
          marginTop: "auto",
          paddingTop: "var(--space-2)",
          borderTop: "1px solid var(--color-border)",
        }}
      >
        {authState === "authenticated" && user ? (
          <AccountMenu user={user} />
        ) : authState === "loading" ? (
          <div role="status" aria-live="polite" style={{ padding: "var(--space-2) var(--space-3)", fontSize: "var(--text-sm)", color: "var(--color-text-tertiary)" }}>
            正在读取账户…
          </div>
        ) : authState === "error" ? (
          <div style={{ padding: "var(--space-2) var(--space-3)", fontSize: "var(--text-sm)", color: "var(--color-text-secondary)" }}>
            账户信息读取失败
            <Button variant="ghost" size="sm" onClick={() => void refreshSession()}>
              重试
            </Button>
          </div>
        ) : (
          <div style={{ padding: "var(--space-2) var(--space-3)", fontSize: "var(--text-sm)", color: "var(--color-text-secondary)" }}>
            需要重新登录
            <Button variant="ghost" size="sm" onClick={() => window.location.replace("/login")}>
              去登录
            </Button>
          </div>
        )}
      </div>
    </nav>
  );
}
