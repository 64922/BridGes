"use client";

import { useRouter } from "next/navigation";

import { StateBlock } from "@/components/bridges/StateBlock";
import { AppShell } from "@/components/layout/AppShell";
import { MainContent } from "@/components/layout/MainContent";
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
  const { user } = useAuth();

  return (
    <AppShell mode="account" showSkipLink={false}>
      <MainContent>
        <div style={{ minHeight: "calc(100vh - var(--topbar-height))", display: "grid", placeItems: "center" }}>
          <StateBlock
            kind="empty"
            title="新聊天"
            description={`${user?.username ?? ""}，这里是你与 BridGes 对话的起点。对话能力正在按迭代计划逐步开放，当前可以前往账户主壳管理项目与设置。`}
            actionLabel="进入账户主壳"
            actionHref="/account"
            onAction={() => router.push("/account")}
          />
        </div>
      </MainContent>
    </AppShell>
  );
}
