import { MainContent } from "@/components/layout/MainContent";

import { DomainPacksList } from "./domain-packs-list";

export const metadata = {
  title: "领域包专家工作台 — BridGes",
};

export default function DomainPacksPage() {
  return (
    <MainContent>
      <section aria-labelledby="workbench-title">
        <header
          style={{
            display: "flex",
            alignItems: "baseline",
            justifyContent: "space-between",
            gap: "var(--space-4)",
            marginBottom: "var(--space-6)",
            flexWrap: "wrap",
          }}
        >
          <div>
            <p className="sc-landmark-label">治理与发行</p>
            <h1 id="workbench-title" className="sc-section-title" style={{ marginBottom: 0 }}>
              领域包专家工作台
            </h1>
            <p style={{ color: "var(--color-text-secondary)", fontSize: "var(--text-sm)" }}>
              维护者、独立复核者与平台发行者共同完成三签、语义 Diff 与灰度发行。
            </p>
          </div>
        </header>
        <DomainPacksList />
      </section>
    </MainContent>
  );
}
