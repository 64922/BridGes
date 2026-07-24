import { TaskStage } from "@/components/project/TaskStage";

export const metadata = {
  title: "项目总览 — Science Companion",
};

/**
 * Project overview workbench.
 *
 * Shows the learning mission, scientific growth loop, next steps, running
 * tasks, and recent artifacts.
 */
export default function ProjectOverviewPage() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-6)" }}>
      <TaskStage />

      <section className="sc-card" aria-labelledby="mission-title">
        <h2 id="mission-title" className="sc-section-title">
          学习使命
        </h2>
        <p style={{ color: "var(--color-text-secondary)", maxWidth: "60ch" }}>
          在这个示例项目中，你将学习如何把科学来源转化为可定位的 Claim—Evidence—Citation
          关系，并生成一份事实锁定的科学表达草稿。
        </p>
      </section>

      <section className="sc-card" aria-labelledby="recent-artifacts-title">
        <h2 id="recent-artifacts-title" className="sc-section-title">
          最近产物
        </h2>
        <p style={{ color: "var(--color-text-secondary)" }}>
          暂无已批准产物。当前任务正在建立 Claim。
        </p>
      </section>
    </div>
  );
}
