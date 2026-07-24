import { MainContent } from "@/components/layout/MainContent";

export const metadata = {
  title: "画像与记忆中心 — Science Companion",
};

export default function ProfileCenterPage() {
  return (
    <MainContent>
      <section className="sc-card" aria-labelledby="profile-title">
        <h1 id="profile-title" className="sc-section-title">
          画像与记忆中心
        </h1>
        <p style={{ color: "var(--color-text-secondary)" }}>
          画像观察、候选画像、证据化画像和学习记录将在这里呈现。
        </p>
      </section>
    </MainContent>
  );
}
