export const metadata = {
  title: "项目材料与产物 — Science Companion",
};

export default function ProjectMaterialsPage() {
  return (
    <section className="sc-card" aria-labelledby="materials-title">
      <h2 id="materials-title" className="sc-section-title">
        项目材料与产物
      </h2>
      <p style={{ color: "var(--color-text-secondary)" }}>
        来源、文档版本、产物、版本历史和成员权限将在这里呈现。
      </p>
    </section>
  );
}
