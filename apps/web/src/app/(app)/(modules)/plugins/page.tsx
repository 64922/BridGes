import Link from "next/link";

import { MainContent } from "@/components/layout/MainContent";

export const metadata = {
  title: "能力管理已退役 - BridGes",
};

export default function RetiredPluginsPage() {
  return (
    <MainContent>
      <section aria-labelledby="plugins-retired-title" style={{ maxWidth: "52rem", marginInline: "auto" }}>
        <h1 id="plugins-retired-title" className="sc-section-title">
          能力管理已退役
        </h1>
        <p>用户上传、安装、启停和调用 SKILL、插件与通用 MCP 已停止。</p>
        <p>内置能力会随应用版本自动适用；论文检索仍可在聊天中直接使用。</p>
        <nav aria-label="继续使用 BridGes" style={{ display: "flex", gap: "var(--space-3)" }}>
          <Link href="/templates/chat">返回聊天</Link>
          <Link href="/templates/list?section=knowledge">打开知识库</Link>
        </nav>
      </section>
    </MainContent>
  );
}
