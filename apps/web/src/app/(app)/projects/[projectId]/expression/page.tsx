export const metadata = {
  title: "科学表达工坊 — BridGes",
};

export default function ExpressionWorkshopPage() {
  return (
    <section className="sc-card" aria-labelledby="expression-title">
      <h2 id="expression-title" className="sc-section-title">
        科学表达工坊
      </h2>
      <p style={{ color: "var(--color-text-secondary)", maxWidth: "60ch" }}>
        表达任务契约、论证结构、事实锁映射、草稿和逐条修订将在这里呈现。
        每个重要判断都可以追溯到 Claim、Evidence 和 Citation。
      </p>
    </section>
  );
}
