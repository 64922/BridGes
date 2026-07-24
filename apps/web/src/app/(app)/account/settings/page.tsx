import { MainContent } from "@/components/layout/MainContent";

export const metadata = {
  title: "设置、设备与同步 — Science Companion",
};

export default function SettingsPage() {
  return (
    <MainContent>
      <section className="sc-card" aria-labelledby="settings-title">
        <h1 id="settings-title" className="sc-section-title">
          设置、设备与同步
        </h1>
        <p style={{ color: "var(--color-text-secondary)" }}>
          账户、设备配对、授权、密钥时期和导出选项将在这里呈现。
        </p>
      </section>
    </MainContent>
  );
}
