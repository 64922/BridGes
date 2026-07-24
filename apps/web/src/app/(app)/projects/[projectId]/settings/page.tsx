export const metadata = {
  title: "项目设置 — Science Companion",
};

export default function ProjectSettingsPage() {
  return (
    <section className="sc-card" aria-labelledby="project-settings-title">
      <h2 id="project-settings-title" className="sc-section-title">
        项目设置
      </h2>
      <p style={{ color: "var(--color-text-secondary)" }}>
        项目名称、成员、对象域、同步策略和版本设置将在这里呈现。
      </p>
    </section>
  );
}
