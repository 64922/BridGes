"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { ButtonLink } from "@/components/design-system/ButtonLink";
import { LoadingStatus } from "@/components/design-system/LoadingStatus";
import { MainContent } from "@/components/layout/MainContent";
import { useAuth } from "@/context/AuthContext";

/**
 * Account-level main shell (client component).
 */
export default function AccountPageClient() {
  const router = useRouter();
  const { user, isLoading } = useAuth();

  useEffect(() => {
    if (!isLoading && user === null) {
      router.replace("/login");
    }
  }, [isLoading, user, router]);

  if (isLoading) {
    return (
      <MainContent>
        <LoadingStatus message="正在恢复会话…" />
      </MainContent>
    );
  }

  if (user === null) {
    return null;
  }

  return (
    <MainContent>
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-6)" }}>
        <section className="sc-card">
          <p className="sc-landmark-label">全局科学伙伴</p>
          <h1
            style={{
              fontFamily: "var(--font-serif)",
              fontSize: "var(--text-2xl)",
              marginTop: "var(--space-2)",
            }}
          >
            欢迎回来，{user?.email || "用户"}
          </h1>
          <p style={{ color: "var(--color-text-secondary)", marginTop: "var(--space-2)", maxWidth: "60ch" }}>
            今天的下一步：继续整理项目的证据，或创建一个新的科学项目空间。
          </p>
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "var(--space-3)",
              marginTop: "var(--space-6)",
            }}
          >
            <ButtonLink href="/account/projects" ariaLabel="查看项目列表">
              查看项目列表
            </ButtonLink>
          </div>
        </section>

        <section aria-labelledby="project-list-title">
          <h2 id="project-list-title" className="sc-section-title">
            科学项目空间
          </h2>
          <ul
            role="list"
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(16rem, 1fr))",
              gap: "var(--space-4)",
            }}
          >
            <li>
              <article
                className="sc-card"
                style={{
                  borderStyle: "dashed",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  minHeight: "8rem",
                }}
              >
                <ButtonLink href="/account/projects" variant="secondary" ariaLabel="创建新项目">
                  + 创建新项目
                </ButtonLink>
              </article>
            </li>
          </ul>
        </section>

        <section aria-labelledby="personal-centers-title">
          <h2 id="personal-centers-title" className="sc-section-title">
            个人与系统
          </h2>
          <ul
            role="list"
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(12rem, 1fr))",
              gap: "var(--space-3)",
            }}
          >
            {[
              { label: "画像与记忆中心", href: "/account/profile" },
              { label: "评测与运行中心", href: "/account/eval" },
              { label: "设置、设备与同步", href: "/account/settings" },
            ].map((item) => (
              <li key={item.href}>
                <ButtonLink href={item.href} variant="secondary" ariaLabel={item.label}>
                  {item.label}
                </ButtonLink>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </MainContent>
  );
}
