"use client";

import { Fragment, useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";

import { StateBlock } from "@/components/bridges/StateBlock";
import { SKIP_LINK_ID, SkipLink } from "@/components/design-system/SkipLink";
import { useAuth } from "@/context/AuthContext";
import { readAloudSession } from "@/lib/read-aloud";
import { saveSearchReturnFocus } from "@/lib/search-shortcut";

import { AppSidebar } from "./AppSidebar";
import { MAIN_CONTENT_ID, MainContent } from "./MainContent";

interface AppShellProps {
  children: React.ReactNode;
  showSkipLink?: boolean;
}

/**
 * Authenticated application shell.
 *
 * Issue 12 ChatGPT 桌面风格全局布局：可折叠 `AppSidebar` + 内容区，无持久
 * 顶栏。旧项目工作台外壳（顶栏 + SidebarNav）已随 Issue 41 退役。
 */
export function AppShell({ children, showSkipLink = true }: AppShellProps) {
  const { accountRevision, authState, sessionError, refreshSession } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  // Issue 39 AC3：账户切换/登出（accountRevision 变化）时立即停止并释放
  // 旧账户的朗读播放会话，防止音频在新账户页面继续播放。
  useEffect(() => {
    readAloudSession.dispose();
  }, [accountRevision]);

  // Issue 02：客户端导航后浏览器可能把焦点落在 shell 的跳转链接上。
  // 只识别固定语义 ID，不把通用的 visually-hidden 样式类当作焦点契约；
  // 用户主动 Tab 到跳转链接时 pathname 不变，因此不会触发这段纠正。
  useEffect(() => {
    const active = document.activeElement;
    if (!(active instanceof HTMLElement) || active.id !== SKIP_LINK_ID) return;

    const focusMainContent = () => {
      // 若用户已在等待期间自行移动焦点，不再抢回键盘控制权。
      if (document.activeElement?.id !== SKIP_LINK_ID) return;
      const mainContent = document.getElementById(MAIN_CONTENT_ID);
      if (mainContent instanceof HTMLElement) {
        mainContent.focus({ preventScroll: true });
      }
    };

    // 主区可能在当前路由提交后下一帧才挂载；只允许一次有界检查，
    // 避免同步命中旧路由的同名主区。
    const frame = window.requestAnimationFrame(focusMainContent);
    return () => window.cancelAnimationFrame(frame);
  }, [pathname]);

  // Issue 24：全局 Ctrl/Cmd+K 打开统一搜索页并记录触发元素（Esc 返回时
  // 归还焦点）；已在搜索页时改为聚焦搜索输入框，不与输入框内行为冲突。
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== "k") return;
      event.preventDefault();
      if (pathname === "/search") {
        document.querySelector<HTMLInputElement>('[data-testid="search-input"]')?.focus();
        return;
      }
      saveSearchReturnFocus(document.activeElement as HTMLElement | null);
      router.push("/search");
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [pathname, router]);

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
