import { MainContent } from "@/components/layout/MainContent";

export const metadata = {
  title: "评测与运行中心 — BridGes",
};

export default function EvalCenterPage() {
  return (
    <MainContent>
      <section className="sc-card" aria-labelledby="eval-title">
        <h1 id="eval-title" className="sc-section-title">
          评测与运行中心
        </h1>
        <p style={{ color: "var(--color-text-secondary)" }}>
          运行记录、评测结果、版本比较和发布资格将在这里呈现。
        </p>
      </section>
    </MainContent>
  );
}
