"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { AccountMenu } from "@/components/account/AccountMenu";
import { BrandLogo } from "@/components/bridges/BrandLogo";
import { Dialog } from "@/components/bridges/Dialog";
import { Menu } from "@/components/bridges/Menu";
import { Button } from "@/components/design-system/Button";
import { Icon, type IconName } from "@/components/design-system/Icon";
import { LearningProjectPickerDialog } from "@/components/learning-projects/LearningProjectPickerDialog";
import { RemoveFromProjectDialog } from "@/components/learning-projects/RemoveFromProjectDialog";
import { useAuth } from "@/context/AuthContext";
import {
  CHAT_LIST_CHANGED_EVENT,
  useRecentConversations,
} from "@/lib/recent-conversations";
import { useLearningProjects, changeConversationLearningProject } from "@/lib/learning-projects";
import {
  deleteChatConversation,
  updateChatConversation,
  type ChatConversationSummary,
  type LearningProjectSummary,
} from "@/lib/api";

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

function conversationTitle(conversation: ChatConversationSummary): string {
  return conversation.title || "未命名对话";
}

function conversationModeLabel(mode: ChatConversationSummary["mode"]): string {
  return mode === "study" ? "学习模式" : "日常陪伴";
}

function formatConversationTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未知";
  const now = new Date();
  const time = new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
  const sameDay = date.toDateString() === now.toDateString();
  if (sameDay) return time;
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (date.toDateString() === yesterday.toDateString()) return `昨天 ${time}`;
  if (date.getFullYear() === now.getFullYear()) {
    return `${date.getMonth() + 1}月${date.getDate()}日 ${time}`;
  }
  return `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日`;
}

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
  const router = useRouter();
  const { user, authState, refreshSession } = useAuth();
  const { conversations, loading, loadError, permissionDenied, reload } = useRecentConversations();
  // 学习项目名称解析（Issue 19）：最近对话徽标显示项目名而非通用标签
  const { projects: learningProjects } = useLearningProjects();
  const [renameTarget, setRenameTarget] = useState<ChatConversationSummary | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ChatConversationSummary | null>(null);
  const [moveTarget, setMoveTarget] = useState<ChatConversationSummary | null>(null);
  const [removeProjectTarget, setRemoveProjectTarget] = useState<ChatConversationSummary | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [operationBusy, setOperationBusy] = useState(false);
  const [operationError, setOperationError] = useState("");
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

  const reportOperationFailure = async (message: string) => {
    await reload();
    setOperationError(`${message} 列表已恢复为服务器状态。`);
  };

  const togglePinned = async (conversation: ChatConversationSummary) => {
    if (operationBusy) return;
    setOperationBusy(true);
    setOperationError("");
    try {
      await updateChatConversation(conversation.conversation_id, {
        pinned: !conversation.pinned,
      });
      window.dispatchEvent(new Event(CHAT_LIST_CHANGED_EVENT));
    } catch (error) {
      await reportOperationFailure(error instanceof Error ? error.message : "置顶操作失败。");
    } finally {
      setOperationBusy(false);
    }
  };

  const openRename = (conversation: ChatConversationSummary) => {
    setOperationError("");
    setRenameTarget(conversation);
    setRenameValue(conversation.title);
  };

  const renameConversation = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!renameTarget || operationBusy) return;
    const title = renameValue.trim();
    if (!title) {
      setOperationError("请输入会话标题。");
      return;
    }
    setOperationBusy(true);
    setOperationError("");
    try {
      await updateChatConversation(renameTarget.conversation_id, { title });
      setRenameTarget(null);
      window.dispatchEvent(new Event(CHAT_LIST_CHANGED_EVENT));
    } catch (error) {
      await reportOperationFailure(error instanceof Error ? error.message : "改名操作失败。");
    } finally {
      setOperationBusy(false);
    }
  };

  const deleteConversation = async () => {
    if (!deleteTarget || operationBusy) return;
    const target = deleteTarget;
    setOperationBusy(true);
    setOperationError("");
    try {
      await deleteChatConversation(target.conversation_id);
      setDeleteTarget(null);
      window.dispatchEvent(new Event(CHAT_LIST_CHANGED_EVENT));
      if (pathname === `/chat/${target.conversation_id}`) {
        router.push("/");
      }
    } catch (error) {
      await reportOperationFailure(error instanceof Error ? error.message : "删除操作失败。");
    } finally {
      setOperationBusy(false);
    }
  };

  /** 学习项目名称解析：项目列表未加载或项目已删除时回退为通用标签。 */
  const projectNameOf = (projectId: string | null | undefined): string | null => {
    if (!projectId) return null;
    return learningProjects.find((project) => project.project_id === projectId)?.name ?? null;
  };

  const moveToProject = async (
    conversation: ChatConversationSummary,
    project: LearningProjectSummary | null
  ) => {
    if (operationBusy) return;
    setOperationBusy(true);
    setOperationError("");
    try {
      await changeConversationLearningProject(
        conversation.conversation_id,
        project?.project_id ?? null
      );
      setMoveTarget(null);
    } catch (error) {
      await reportOperationFailure(error instanceof Error ? error.message : "移动到学习项目失败。");
    } finally {
      setOperationBusy(false);
    }
  };

  const removeFromProject = async () => {
    if (!removeProjectTarget || operationBusy) return;
    setOperationBusy(true);
    setOperationError("");
    try {
      await changeConversationLearningProject(removeProjectTarget.conversation_id, null);
      setRemoveProjectTarget(null);
    } catch (error) {
      setOperationError(error instanceof Error ? error.message : "移出学习项目失败。");
    } finally {
      setOperationBusy(false);
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
        {operationError && (
          <p
            role="alert"
            style={{
              margin: "0 var(--space-3) var(--space-2)",
              color: "var(--color-status-error)",
              fontSize: "var(--text-xs)",
            }}
          >
            {operationError}
          </p>
        )}
        {loading && conversations.length === 0 ? (
          <div role="status" aria-label="正在加载最近对话" style={{ padding: "0 var(--space-2)" }}>
            {["skeleton-1", "skeleton-2", "skeleton-3"].map((key) => (
              <div
                key={key}
                aria-hidden="true"
                style={{
                  height: "var(--target-size)",
                  marginBottom: "2px",
                  borderRadius: "var(--radius-md)",
                  backgroundColor: "var(--color-surface)",
                  opacity: 0.7,
                }}
              />
            ))}
            <span className="sc-visually-hidden">正在加载最近对话…</span>
          </div>
        ) : permissionDenied ? (
          <div
            role="alert"
            style={{
              padding: "var(--space-2) var(--space-3)",
              fontSize: "var(--text-sm)",
              color: "var(--color-text-secondary)",
            }}
          >
            当前账户没有权限查看最近对话。
            <Button variant="ghost" size="sm" onClick={() => void reload()}>
              重试
            </Button>
          </div>
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
            还没有对话记录，点击「新聊天」开始。
          </p>
        ) : (
          <ul role="list" style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
            {conversations.map((conversation) => {
              const href = `/chat/${conversation.conversation_id}`;
              const active = pathname === href;
              const title = conversationTitle(conversation);
              return (
                <li
                  key={conversation.conversation_id}
                  style={{ display: "flex", alignItems: "stretch", minWidth: 0 }}
                  data-testid={`conversation-item-${conversation.conversation_id}`}
                >
                  <Link
                    href={href}
                    aria-current={active ? "page" : undefined}
                    style={{ ...itemStyle(active), flex: 1, minWidth: 0, paddingRight: "var(--space-1)" }}
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
                      <span
                        style={{
                          display: "block",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {conversation.pinned && <span aria-label="已置顶">置顶 · </span>}
                        {title}
                      </span>
                      <span
                        style={{
                          display: "block",
                          marginTop: "2px",
                          color: "var(--color-text-tertiary)",
                          fontSize: "var(--text-xs)",
                          lineHeight: 1.3,
                        }}
                      >
                        {conversationModeLabel(conversation.mode)}
                        {conversation.project_id
                          ? ` · ${projectNameOf(conversation.project_id) ?? "学习项目"}`
                          : ""}
                        {` · ${formatConversationTime(conversation.updated_at)}`}
                      </span>
                    </span>
                  </Link>
                  <Menu
                    ariaLabel={`会话操作：${title}`}
                    trigger={<Icon name="more" size={18} aria-hidden />}
                    triggerStyle={{
                      width: "var(--target-size)",
                      minWidth: "var(--target-size)",
                      padding: "var(--space-1)",
                      justifyContent: "center",
                    }}
                    items={[
                      {
                        label: conversation.pinned ? "取消置顶" : "置顶",
                        icon: "pin",
                        onSelect: () => void togglePinned(conversation),
                      },
                      {
                        label: "改名",
                        icon: "edit",
                        returnFocus: false,
                        onSelect: () => openRename(conversation),
                      },
                      {
                        label: "移动到学习项目…",
                        icon: "learningProject",
                        returnFocus: false,
                        onSelect: () => {
                          setOperationError("");
                          setMoveTarget(conversation);
                        },
                      },
                      ...(conversation.project_id
                        ? [
                            {
                              label: "移出学习项目",
                              icon: "close" as const,
                              returnFocus: false,
                              onSelect: () => {
                                setOperationError("");
                                setRemoveProjectTarget(conversation);
                              },
                            },
                          ]
                        : []),
                      {
                        label: "删除",
                        icon: "trash",
                        danger: true,
                        returnFocus: false,
                        onSelect: () => {
                          setOperationError("");
                          setDeleteTarget(conversation);
                        },
                      },
                    ]}
                  />
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

      <Dialog
        open={renameTarget !== null}
        onClose={() => {
          if (!operationBusy) setRenameTarget(null);
        }}
        title="修改会话名称"
        description="名称会保存到当前账户，并在最近对话中保持一致。"
      >
        <form onSubmit={(event) => void renameConversation(event)}>
          <label
            htmlFor="conversation-rename-input"
            style={{ display: "block", marginBottom: "var(--space-2)", fontWeight: 600 }}
          >
            会话名称
          </label>
          <input
            id="conversation-rename-input"
            value={renameValue}
            maxLength={120}
            onChange={(event) => setRenameValue(event.target.value)}
            style={{
              width: "100%",
              minHeight: "var(--target-size)",
              padding: "var(--space-2) var(--space-3)",
              border: "1px solid var(--color-border-strong)",
              borderRadius: "var(--radius-md)",
              backgroundColor: "var(--color-surface)",
              color: "var(--color-text-primary)",
            }}
          />
          {operationError && (
            <p role="alert" style={{ marginTop: "var(--space-2)", color: "var(--color-status-error)" }}>
              {operationError}
            </p>
          )}
          <div style={{ display: "flex", justifyContent: "flex-end", gap: "var(--space-2)", marginTop: "var(--space-4)" }}>
            <Button type="button" variant="ghost" onClick={() => setRenameTarget(null)} disabled={operationBusy}>
              取消
            </Button>
            <Button type="submit" variant="primary" disabled={operationBusy}>
              {operationBusy ? "正在保存…" : "保存名称"}
            </Button>
          </div>
        </form>
      </Dialog>

      <Dialog
        open={deleteTarget !== null}
        onClose={() => {
          if (!operationBusy) setDeleteTarget(null);
        }}
        title="删除会话？"
        description="删除后会永久移除这段会话的消息、模式切换记录与附件关联，且无法恢复。"
      >
        {deleteTarget && (
          <p style={{ color: "var(--color-text-secondary)", marginBottom: "var(--space-4)" }}>
            将删除「{conversationTitle(deleteTarget)}」。
          </p>
        )}
        {operationError && (
          <p role="alert" style={{ marginBottom: "var(--space-3)", color: "var(--color-status-error)" }}>
            {operationError}
          </p>
        )}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: "var(--space-2)" }}>
          <Button type="button" variant="ghost" onClick={() => setDeleteTarget(null)} disabled={operationBusy}>
            取消
          </Button>
          <Button type="button" variant="danger" onClick={() => void deleteConversation()} disabled={operationBusy}>
            {operationBusy ? "正在删除…" : "确认删除"}
          </Button>
        </div>
      </Dialog>

      {moveTarget && (
        <LearningProjectPickerDialog
          selectedProjectId={moveTarget.project_id ?? null}
          onSelect={(project) => void moveToProject(moveTarget, project)}
          onClose={() => setMoveTarget(null)}
        />
      )}

      {removeProjectTarget && (
        <RemoveFromProjectDialog
          conversationTitle={conversationTitle(removeProjectTarget)}
          busy={operationBusy}
          error={operationError}
          onConfirm={() => void removeFromProject()}
          onClose={() => setRemoveProjectTarget(null)}
        />
      )}
    </nav>
  );
}
