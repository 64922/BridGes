import { MainContent } from "@/components/layout/MainContent";

export const metadata = {
  title: "科学项目空间 — Science Companion",
};

export default function ProjectsListPage() {
  return (
    <MainContent>
      <section className="sc-card" aria-labelledby="projects-title">
        <h1 id="projects-title" className="sc-section-title">
          科学项目空间
        </h1>
        <p style={{ color: "var(--color-text-secondary)" }}>
          项目列表、归档和跨项目待办将在这里呈现。
        </p>
      </section>
    </MainContent>
  );
}
