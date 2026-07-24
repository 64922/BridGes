export const metadata = {
  title: "证据与校验台 — Science Companion",
};

export default function EvidenceDeskPage() {
  return (
    <section className="sc-card" aria-labelledby="evidence-title">
      <h2 id="evidence-title" className="sc-section-title">
        证据与校验台
      </h2>
      <p style={{ color: "var(--color-text-secondary)", maxWidth: "60ch" }}>
        Claim、Evidence、冲突、质量门和发布资格将在这里呈现。
        来源状态变化会立即影响发布资格。
      </p>
    </section>
  );
}
