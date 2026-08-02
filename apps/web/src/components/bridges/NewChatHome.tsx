"use client";

import { useRouter } from "next/navigation";

import { BrandLogo } from "@/components/bridges/BrandLogo";
import { StateBlock } from "@/components/bridges/StateBlock";
import { Button } from "@/components/design-system/Button";
import { LoadingStatus } from "@/components/design-system/LoadingStatus";
import { useAuth } from "@/context/AuthContext";

/**
 * 新聊天落地页（登录后的默认入口）。
 *
 * Issue 07 只交付认证纵向切片：已登录用户访问 "/" 时落到这里。完整的对话
 * 外壳与输入区由后续 Issue（11–13）在同一路由交付。会话 Cookie 失效时给出
 * 明确的中文提示与重新登录入口，不做静默跳转。
 */
export function NewChatHome() {
  const router = useRouter();
  const { user, isAuthenticated, isLoading, logout } = useAuth();

  const handleLogout = async () => {
    await logout();
    router.push("/login?from=logout");
  };

  return (
    <div style={{ minHeight: "100vh", display: "flex", flexDirection: "column" }}>
      <header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "var(--space-4)",
          padding: "var(--space-4) var(--space-6)",
          borderBottom: "1px solid var(--color-border)",
          backgroundColor: "var(--color-surface)",
        }}
      >
        <BrandLogo variant="horizontal" width={132} />
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-3)" }}>
          {user && (
            <span style={{ color: "var(--color-text-secondary)", fontSize: "var(--text-sm)" }}>
              {user.username}
            </span>
          )}
          <Button variant="ghost" size="sm" onClick={() => router.push("/account")} aria-label="进入账户主壳">
            账户主壳
          </Button>
          <Button variant="secondary" size="sm" onClick={handleLogout} aria-label="退出登录">
            退出
          </Button>
        </div>
      </header>

      <main
        id="main-content"
        tabIndex={-1}
        data-testid="main-content"
        style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "center" }}
      >
        {isLoading ? (
          <LoadingStatus message="正在恢复会话…" />
        ) : isAuthenticated ? (
          <StateBlock
            kind="empty"
            title="新聊天"
            description={`${user?.username ?? ""}，这里是你与 BridGes 对话的起点。对话能力正在按迭代计划逐步开放，当前可以前往账户主壳管理项目与设置。`}
            actionLabel="进入账户主壳"
            onAction={() => router.push("/account")}
          />
        ) : (
          <StateBlock
            kind="permission"
            title="会话已失效"
            description="你的登录状态已过期或被撤销，请重新登录。"
            actionLabel="重新登录"
            onAction={() => router.push("/login")}
          />
        )}
      </main>
    </div>
  );
}
