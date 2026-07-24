import { ButtonLink } from "@/components/design-system/ButtonLink";
import { Icon } from "@/components/design-system/Icon";
import { MainContent } from "@/components/layout/MainContent";

export const metadata = {
  title: "账户主壳 — Science Companion",
};

/**
 * Account-level main shell.
 *
 * The authenticated landing page that surfaces the global science companion,
 * project list, and personal centers while keeping each domain visually
 * separated.
 */
export default function AccountPage() {
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
            欢迎回来，演示用户
          </h1>
          <p style={{ color: "var(--color-text-secondary)", marginTop: "var(--space-2)", maxWidth: "60ch" }}>
            今天的下一步：继续整理示例项目的证据，或创建一个新的科学项目空间。
          </p>
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "var(--space-3)",
              marginTop: "var(--space-6)",
            }}
          >
            <ButtonLink href="/projects/demo-id" ariaLabel="进入示例项目">
              进入示例项目
            </ButtonLink>
            <ButtonLink href="/account/projects" variant="secondary" ariaLabel="查看项目列表">
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
              <article className="sc-card">
                <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", marginBottom: "var(--space-3)" }}>
                  <Icon name="project" size={20} aria-hidden />
                  <h3 style={{ fontSize: "var(--text-lg)", fontWeight: 600 }}>示例项目</h3>
                </div>
                <p style={{ color: "var(--color-text-secondary)", fontSize: "var(--text-sm)", marginBottom: "var(--space-4)" }}>
                  对象域：个人保险库 · 最近运行中
                </p>
                <ButtonLink href="/projects/demo-id" ariaLabel="打开示例项目">
                  打开项目
                </ButtonLink>
              </article>
            </li>
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
