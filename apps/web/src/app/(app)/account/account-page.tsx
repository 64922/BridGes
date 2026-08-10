"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { ButtonLink } from "@/components/design-system/ButtonLink";
import { LoadingStatus } from "@/components/design-system/LoadingStatus";
import { MainContent } from "@/components/layout/MainContent";
import { useAuth } from "@/context/AuthContext";

export default function AccountPageClient() {
  const router = useRouter();
  const { user, isLoading: isAuthLoading } = useAuth();

  useEffect(() => {
    if (!isAuthLoading && user === null) router.replace("/login");
  }, [isAuthLoading, router, user]);

  if (isAuthLoading) {
    return (
      <MainContent>
        <LoadingStatus message="正在恢复会话…" />
      </MainContent>
    );
  }

  if (user === null) return null;

  return (
    <MainContent>
      <section className="sc-card" aria-labelledby="account-title">
        <p className="sc-landmark-label">账户首页</p>
        <h1 id="account-title" className="sc-section-title">
          欢迎回来，{user.username || "用户"}
        </h1>
        <p style={{ color: "var(--color-text-secondary)", marginTop: "var(--space-2)" }}>
          从聊天首页继续对话，或管理你的画像与账户设置。
        </p>
        <div style={{ display: "flex", gap: "var(--space-3)", marginTop: "var(--space-6)" }}>
          <ButtonLink href="/" ariaLabel="返回新聊天">
            返回新聊天
          </ButtonLink>
          <ButtonLink href="/account/profile" variant="secondary" ariaLabel="打开用户画像">
            用户画像
          </ButtonLink>
        </div>
      </section>
    </MainContent>
  );
}
