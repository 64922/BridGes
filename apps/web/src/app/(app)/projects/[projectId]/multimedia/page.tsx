export const metadata = {
  title: "多模态科学实验室 — Science Companion",
};

export default function MultimediaLabPage() {
  return (
    <section className="sc-card" aria-labelledby="multimedia-title">
      <h2 id="multimedia-title" className="sc-section-title">
        多模态科学实验室
      </h2>
      <p style={{ color: "var(--color-text-secondary)", maxWidth: "60ch" }}>
        媒体选择、结构化分镜、可编辑源、沙箱运行和预览将在这里呈现。
        每个视觉产物都具备替代文本或等价数据表。
      </p>
    </section>
  );
}
