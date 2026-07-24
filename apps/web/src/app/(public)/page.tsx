import { ButtonLink } from "@/components/design-system/ButtonLink";
import HealthPanel from "@/components/HealthPanel";

export const metadata = {
  title: "Science Companion — 公共入口",
};

/**
 * Public entry point.
 *
 * Unauthenticated users can view the public product description, system health
 * status, and access authentication flows. No private project data is shown.
 */
export default function PublicEntryPage() {
  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <header
        style={{
          padding: "var(--space-6)",
          borderBottom: "1px solid var(--color-border)",
          backgroundColor: "var(--color-surface)",
        }}
      >
        <div className="sc-container">
          <p className="sc-landmark-label">长期科学学习与表达伙伴</p>
          <h1
            style={{
              fontFamily: "var(--font-serif)",
              fontSize: "var(--text-3xl)",
              marginTop: "var(--space-2)",
            }}
          >
            Science Companion
          </h1>
        </div>
      </header>

      <main
        id="main-content"
        tabIndex={-1}
        data-testid="main-content"
        style={{ flex: 1, padding: "var(--space-6)" }}
      >
        <div
          className="sc-container"
          style={{
            display: "flex",
            flexDirection: "column",
            gap: "var(--space-8)",
          }}
        >
          <section className="sc-card">
            <h2 className="sc-section-title">从第一张页面开始，科学学习与表达就有根有据</h2>
            <p style={{ color: "var(--color-text-secondary)", maxWidth: "60ch" }}>
              Science Companion 是面向大学生、研究生、青年科研人员及深度科学爱好者的长期科学学习与表达伙伴。
              它在持续互动中同时提升你的科学理解、表达质量与事实可靠性。
            </p>
            <div
              style={{
                display: "flex",
                flexWrap: "wrap",
                gap: "var(--space-3)",
                marginTop: "var(--space-6)",
              }}
            >
              <ButtonLink href="/login" size="lg" ariaLabel="登录">
                登录
              </ButtonLink>
              <ButtonLink href="/register" variant="secondary" size="lg" ariaLabel="注册">
                注册
              </ButtonLink>
              <ButtonLink href="/account" variant="secondary" size="lg" ariaLabel="进入账户主壳（演示）">
                进入账户主壳（演示）
              </ButtonLink>
            </div>
          </section>

          <HealthPanel />
        </div>
      </main>

      <footer
        style={{
          padding: "var(--space-4)",
          borderTop: "1px solid var(--color-border)",
          textAlign: "center",
          color: "var(--color-text-tertiary)",
          fontSize: "var(--text-sm)",
        }}
      >
        <div className="sc-container">
          <p>© Science Companion · 公共入口不显示私人项目数据</p>
        </div>
      </footer>
    </div>
  );
}
