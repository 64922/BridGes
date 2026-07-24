export const metadata = {
  title: "学习实验室 — Science Companion",
};

export default function LearningLabPage() {
  return (
    <section className="sc-card" aria-labelledby="learning-title">
      <h2 id="learning-title" className="sc-section-title">
        学习实验室
      </h2>
      <p style={{ color: "var(--color-text-secondary)", maxWidth: "60ch" }}>
        当前学习胜利、先备诊断、短课、练习和学习证据将在这里呈现。
        浏览行为不会自动被视为掌握证据。
      </p>
    </section>
  );
}
