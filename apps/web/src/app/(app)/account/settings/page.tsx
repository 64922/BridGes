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
            管理当前账户的公开资料与数据隐私。所有入口都绑定稳定账户 ID，
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

          <section className="sc-card" aria-labelledby="data-privacy-settings-title">
            <Icon name="download" size={28} aria-hidden />
            <h2 id="data-privacy-settings-title" className="sc-section-title" style={{ marginTop: "var(--space-4)" }}>
              数据与隐私
            </h2>
            <p style={{ color: "var(--color-text-secondary)", minHeight: "3rem" }}>
              导出账户数据、创建加密备份与恢复，或删除当前账户及其全部本地数据。
            </p>
            <div style={{ marginTop: "var(--space-5)" }}>
              <ButtonLink href="/account/settings/data" ariaLabel="打开数据与隐私设置">
                打开数据与隐私
              </ButtonLink>
            </div>
          </section>

          <section className="sc-card" aria-labelledby="key-model-settings-title">
            <Icon name="search" size={28} aria-hidden />
            <h2 id="key-model-settings-title" className="sc-section-title" style={{ marginTop: "var(--space-4)" }}>
              密钥与模型管理
            </h2>
            <p style={{ color: "var(--color-text-secondary)", minHeight: "3rem" }}>
              查看和验证 Qwen、Tavily、高德服务端路线及浏览器地图的凭据状态，并手填验证 Qwen 主模型 ID。
            </p>
            <div style={{ marginTop: "var(--space-5)" }}>
              <ButtonLink href="/account/settings/models" ariaLabel="打开密钥与模型管理">
                管理密钥与模型
              </ButtonLink>
            </div>
          </section>
        </div>
      </div>
    </MainContent>
  );
}
