import { ButtonLink } from "@/components/design-system/ButtonLink";
import { Icon } from "@/components/design-system/Icon";
import { MainContent } from "@/components/layout/MainContent";

export const metadata = {
  title: "设置、设备与同步 — BridGes",
};

export default function SettingsPage() {
  return (
    <MainContent>
      <div className="sc-container" style={{ display: "grid", gap: "var(--space-6)" }}>
        <header style={{ maxWidth: "64ch" }}>
          <p className="sc-landmark-label">账户与连接</p>
          <h1
            id="settings-title"
            style={{
              marginTop: "var(--space-2)",
              fontFamily: "var(--font-serif)",
              fontSize: "var(--text-3xl)",
            }}
          >
            设置
          </h1>
          <p
            style={{
              marginTop: "var(--space-3)",
              color: "var(--color-text-secondary)",
              lineHeight: "var(--line-height-relaxed)",
            }}
          >
            管理当前账户的公开资料与模型连接状态。所有入口都绑定稳定账户 ID，
            不使用用户名或头像判断数据归属。
          </p>
        </header>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(18rem, 1fr))",
            gap: "var(--space-5)",
          }}
        >
          <section className="sc-card" aria-labelledby="personal-settings-title">
            <Icon name="profile" size={28} aria-hidden />
            <h2 id="personal-settings-title" className="sc-section-title" style={{ marginTop: "var(--space-4)" }}>
              个人资料
            </h2>
            <p style={{ color: "var(--color-text-secondary)", minHeight: "3rem" }}>
              修改头像和用户名，查看不可变的账户 ID 与 QQ 邮箱归属。
            </p>
            <div style={{ marginTop: "var(--space-5)" }}>
              <ButtonLink href="/account/settings/profile" ariaLabel="打开个人资料设置">
                打开个人资料
              </ButtonLink>
            </div>
          </section>

          <section className="sc-card" aria-labelledby="key-settings-title">
            <Icon name="settings" size={28} aria-hidden />
            <h2 id="key-settings-title" className="sc-section-title" style={{ marginTop: "var(--space-4)" }}>
              密钥设置
            </h2>
            <p style={{ color: "var(--color-text-secondary)", minHeight: "3rem" }}>
              通过近期密码确认后，读取当前账户真实的百炼密钥配置状态。
            </p>
            <div style={{ marginTop: "var(--space-5)" }}>
              <ButtonLink href="/account/settings/keys" ariaLabel="打开密钥设置">
                打开密钥设置
              </ButtonLink>
            </div>
          </section>
        </div>
      </div>
    </MainContent>
  );
}
