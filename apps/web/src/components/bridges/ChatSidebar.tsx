"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { Button } from "@/components/design-system/Button";
import { Icon, type IconName } from "@/components/design-system/Icon";
import { VisuallyHidden } from "@/components/design-system/VisuallyHidden";
import { BrandLogo } from "./BrandLogo";
import { Menu } from "./Menu";

export interface RecentConversation {
  id: string;
  title: string;
  mode: "日常陪伴" | "学习";
}

interface SidebarModule {
  key: string;
  label: string;
  icon: IconName;
  href: string;
}

/** 侧边栏功能模块（与 1.txt 的侧边栏模块清单一一对应） */
export const SIDEBAR_MODULES: SidebarModule[] = [
  { key: "knowledge", label: "知识库", icon: "knowledgeBase", href: "/templates/list?section=knowledge" },
  { key: "profile", label: "用户画像", icon: "profile", href: "/templates/list?section=profile" },
];

interface ChatSidebarProps {
  activeConversation?: string;
  activeModule?: string;
  recents: RecentConversation[];
  accountName: string;
  collapsed: boolean;
  onToggleCollapsed: () => void;
}

/**
 * ChatGPT 桌面结构启发的可折叠侧栏（布局借鉴见
 * docs/design/0001-chatgpt-desktop-baseline-2026-08-02.md）。
 *
 * 自上而下：Logo 与折叠按钮、新聊天、搜索对话、功能模块
 * （知识库 / 用户画像）、最近对话、
 * 底部账户菜单（切换账号 / 个人资料 / 退出登录，GQ-06 无密钥入口）。
 * 折叠后为仅图标的窄轨，全部控件保持键盘可达；
 * Logo 点击 / 键盘激活进入新聊天（NAV-01）。
 */
export function ChatSidebar({
  activeConversation,
  activeModule,
  recents,
  accountName,
  collapsed,
  onToggleCollapsed,
}: ChatSidebarProps) {
  const router = useRouter();
  const [query, setQuery] = useState("");

  const filteredRecents = useMemo(() => {
    const keyword = query.trim();
    if (!keyword) return recents;
    return recents.filter((item) => item.title.includes(keyword));
  }, [query, recents]);

  const moduleLinkStyle = (active: boolean): React.CSSProperties => ({
    display: "flex",
    alignItems: "center",
    justifyContent: collapsed ? "center" : "flex-start",
    gap: "var(--space-2)",
    minHeight: "var(--target-size)",
    padding: collapsed ? 0 : "var(--space-2) var(--space-3)",
    borderRadius: "var(--radius-md)",
    color: active ? "var(--color-accent-primary)" : "var(--color-text-secondary)",
    backgroundColor: active ? "var(--color-accent-primary-soft)" : "transparent",
    fontSize: "var(--text-sm)",
    fontWeight: active ? 600 : 400,
    textDecoration: "none",
  });

  return (
    <nav
      aria-label="主导航"
      data-testid="chat-sidebar"
      data-collapsed={collapsed}
      style={{
        display: "flex",
        flexDirection: "column",
        flexShrink: 0,
        width: collapsed ? "var(--sidebar-width-collapsed)" : "var(--sidebar-width)",
        height: "100vh",
        position: "sticky",
        top: 0,
        backgroundColor: "var(--color-bg-secondary)",
        borderRight: "1px solid var(--color-border)",
        padding: "var(--space-2)",
        gap: "var(--space-2)",
        overflowY: "auto",
        transition:
          "width var(--motion-duration-base) var(--motion-easing)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: collapsed ? "center" : "space-between",
          gap: "var(--space-1)",
        }}
      >
        <Link
          href="/templates/chat"
          aria-label="BridGes — 新聊天"
          style={{ display: "inline-flex", borderRadius: "var(--radius-sm)" }}
        >
          <BrandLogo variant={collapsed ? "icon" : "horizontal"} width={collapsed ? 28 : 118} />
        </Link>
        {!collapsed && (
          <Button
            variant="ghost"
            size="sm"
            onClick={onToggleCollapsed}
            aria-expanded={!collapsed}
            aria-label="收起侧边栏"
            data-testid="sidebar-toggle"
            style={{ minWidth: "2.5rem", padding: "0.375rem" }}
          >
            <Icon name="sidebarCollapse" size={20} aria-hidden />
          </Button>
        )}
      </div>
      {collapsed && (
        <Button
          variant="ghost"
          size="sm"
          onClick={onToggleCollapsed}
          aria-expanded={!collapsed}
          aria-label="展开侧边栏"
          data-testid="sidebar-toggle"
          style={{ minWidth: "2.5rem", padding: "0.375rem" }}
        >
          <Icon name="sidebarExpand" size={20} aria-hidden />
        </Button>
      )}

      <Link
        href="/templates/chat"
        aria-label="新聊天"
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: collapsed ? "center" : "flex-start",
          gap: "var(--space-2)",
          minHeight: "var(--target-size)",
          padding: collapsed ? 0 : "var(--space-2) var(--space-3)",
          borderRadius: "var(--radius-md)",
          border: "1px solid transparent",
          backgroundColor: "transparent",
          color: "var(--color-text-secondary)",
          fontSize: "var(--text-sm)",
          fontWeight: 500,
          textDecoration: "none",
        }}
      >
        <Icon name="newChat" size={18} aria-hidden />
        {!collapsed && "新聊天"}
      </Link>

      {!collapsed && (
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
          <label htmlFor="sidebar-search" style={{ position: "relative", display: "block" }}>
            <VisuallyHidden>搜索最近对话</VisuallyHidden>
            <span
              aria-hidden="true"
              style={{
                position: "absolute",
                left: "var(--space-3)",
                top: "50%",
                transform: "translateY(-50%)",
                color: "var(--color-text-tertiary)",
                display: "inline-flex",
              }}
            >
              <Icon name="search" size={16} aria-hidden />
            </span>
            <input
              id="sidebar-search"
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索对话"
              style={{
                width: "100%",
                minHeight: "2.5rem",
                padding: "var(--space-2) var(--space-3) var(--space-2) 2.25rem",
                borderRadius: "var(--radius-md)",
                border: "1px solid var(--color-border)",
                backgroundColor: "var(--color-surface)",
                color: "var(--color-text-primary)",
                fontSize: "var(--text-sm)",
              }}
            />
          </label>
        </div>
      )}

      <ul
        role="list"
        aria-label="功能模块"
        style={{ display: "flex", flexDirection: "column", gap: "2px" }}
      >
        {SIDEBAR_MODULES.map((module) => (
          <li key={module.key}>
            <Link
              href={module.href}
              aria-label={module.label}
              aria-current={activeModule === module.key ? "page" : undefined}
              style={moduleLinkStyle(activeModule === module.key)}
            >
              <Icon name={module.icon} size={18} aria-hidden />
              {!collapsed && module.label}
            </Link>
          </li>
        ))}
      </ul>

      {!collapsed && (
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
          {filteredRecents.length === 0 ? (
            <p
              style={{
                padding: "var(--space-2) var(--space-3)",
                fontSize: "var(--text-sm)",
                color: "var(--color-text-tertiary)",
              }}
            >
              没有匹配的对话
            </p>
          ) : (
            <ul role="list" style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
              {filteredRecents.map((item) => (
                <li key={item.id}>
                  <Link
                    href={`/templates/chat?conversation=${item.id}`}
                    aria-label={`${item.title}（${item.mode}模式）`}
                    aria-current={activeConversation === item.id ? "page" : undefined}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "var(--space-2)",
                      padding: "var(--space-2) var(--space-3)",
                      borderRadius: "var(--radius-md)",
                      fontSize: "var(--text-sm)",
                      color: "var(--color-text-secondary)",
                      textDecoration: "none",
                      backgroundColor:
                        activeConversation === item.id
                          ? "var(--color-accent-primary-soft)"
                          : "transparent",
                    }}
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
                      {item.title}
                    </span>
                    <span
                      style={{
                        flexShrink: 0,
                        fontSize: "var(--text-xs)",
                        padding: "0 0.375rem",
                        borderRadius: "var(--radius-full)",
                        border: "1px solid var(--color-border-strong)",
                        color: "var(--color-text-tertiary)",
                      }}
                    >
                      {item.mode}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      <div style={{ marginTop: "auto" }}>
        <Menu
          ariaLabel={`账户菜单：${accountName}`}
          openUp
          trigger={
            <>
              <Icon name="account" size={20} aria-hidden />
              {!collapsed && (
                <span
                  style={{
                    flex: 1,
                    minWidth: 0,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                    textAlign: "left",
                  }}
                >
                  {accountName}
                </span>
              )}
            </>
          }
          items={[
            { label: "切换账号", icon: "account", onSelect: () => router.push("/templates/login?from=switch") },
            { label: "个人资料", icon: "profile", onSelect: () => router.push("/templates/settings?section=profile") },
            { label: "退出登录", icon: "close", danger: true, onSelect: () => router.push("/templates/login?from=logout") },
          ]}
        />
      </div>
    </nav>
  );
}
