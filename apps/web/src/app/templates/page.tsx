import Link from "next/link";

import { BrandLogo } from "@/components/bridges/BrandLogo";
import { Icon, iconNames } from "@/components/design-system/Icon";

export const metadata = {
  title: "BridGes — 桌面设计基线模板",
};

const TEMPLATES = [
  { href: "/templates/login", label: "登录", description: "账户登录表单与错误、提交中、会话失效状态" },
  { href: "/templates/register", label: "注册", description: "新账户注册表单、字段校验与条款确认" },
  { href: "/templates/chat", label: "聊天内容", description: "消息流、思考摘要、消息操作、输入区与建议卡" },
  { href: "/templates/list", label: "列表", description: "最近对话与各模块列表、搜索过滤、条目操作" },
  { href: "/templates/detail", label: "详情", description: "能力结果详情：依据、引用、元数据与操作" },
  { href: "/templates/settings", label: "设置", description: "分区设置卡片、主题切换与危险操作对话框" },
];

const LOGO_ASSETS = [
  { src: "/brand/bridges-logo-horizontal.svg", label: "横向完整版" },
  { src: "/brand/bridges-logo-icon.svg", label: "独立图标版" },
  { src: "/brand/bridges-logo-horizontal-mono.svg", label: "单色版" },
  { src: "/brand/bridges-logo-on-light.svg", label: "浅色背景版" },
  { src: "/brand/bridges-logo-on-dark.svg", label: "深色背景版" },
];

/**
 * 设计基线索引：六类桌面模板入口 + 品牌资产与图标清单（供人工验收核对）。
 */
export default function TemplatesIndexPage() {
  return (
    <main
      id="main-content"
      tabIndex={-1}
      data-testid="main-content"
      style={{ padding: "var(--space-8) var(--space-6)" }}
    >
      <div className="sc-container" style={{ display: "flex", flexDirection: "column", gap: "var(--space-10)" }}>
        <header style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
          <BrandLogo variant="horizontal" width={180} />
          <h1 style={{ fontSize: "var(--text-2xl)" }}>桌面设计基线模板</h1>
          <p style={{ color: "var(--color-text-secondary)", maxWidth: "64ch" }}>
            以 ChatGPT 电脑端信息架构为交互基线（见 docs/design/0001），采用 Claude-inspired
            的温暖、克制、高可读性美术语言。所有品牌图形、图标与组件外观均为 BridGes 原创。
            每类模板内置正常、加载中、空、错误与未登录五种状态，可通过页面顶部的状态切换器检查。
          </p>
        </header>

        <section aria-labelledby="templates-heading">
          <h2 id="templates-heading" className="sc-section-title">
            页面模板
          </h2>
          <ul
            role="list"
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(18rem, 1fr))",
              gap: "var(--space-4)",
            }}
          >
            {TEMPLATES.map((item) => (
              <li key={item.href} className="sc-card">
                <h3 style={{ fontSize: "var(--text-lg)", marginBottom: "var(--space-1)" }}>
                  <Link href={item.href}>{item.label}模板</Link>
                </h3>
                <p style={{ color: "var(--color-text-secondary)", fontSize: "var(--text-sm)" }}>
                  {item.description}
                </p>
              </li>
            ))}
          </ul>
        </section>

        <section aria-labelledby="logo-heading">
          <h2 id="logo-heading" className="sc-section-title">
            Logo 资产
          </h2>
          <ul
            role="list"
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(16rem, 1fr))",
              gap: "var(--space-4)",
            }}
          >
            {LOGO_ASSETS.map((asset) => (
              <li key={asset.src} className="sc-card" style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={asset.src} alt={`BridGes Logo ${asset.label}`} style={{ maxWidth: "100%", height: "auto" }} />
                <p style={{ fontSize: "var(--text-sm)", color: "var(--color-text-secondary)" }}>{asset.label}</p>
              </li>
            ))}
          </ul>
          <p style={{ marginTop: "var(--space-3)", fontSize: "var(--text-sm)", color: "var(--color-text-tertiary)" }}>
            PNG 尺寸导出（16–512px）与单色 / 深色变体位于 apps/web/public/brand/，清单见
            docs/design/0003-bridges-brand-assets.md。
          </p>
        </section>

        <section aria-labelledby="icons-heading">
          <h2 id="icons-heading" className="sc-section-title">
            图标集（{iconNames.length} 个）
          </h2>
          <ul
            role="list"
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(7rem, 1fr))",
              gap: "var(--space-2)",
            }}
          >
            {iconNames.map((name) => (
              <li
                key={name}
                style={{
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  gap: "var(--space-1)",
                  padding: "var(--space-3) var(--space-2)",
                  borderRadius: "var(--radius-md)",
                  border: "1px solid var(--color-border)",
                  backgroundColor: "var(--color-surface)",
                }}
              >
                <Icon name={name} size={24} aria-hidden />
                <span style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>{name}</span>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </main>
  );
}
