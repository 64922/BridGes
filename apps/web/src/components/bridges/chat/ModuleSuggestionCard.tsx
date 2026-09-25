"use client";

import { Icon } from "@/components/design-system/Icon";
import { chatModuleIcon, chatModuleLabel } from "@/lib/chat-modules";
import type { ModuleSuggestionProjection } from "@/lib/api";

/**
 * 普通聊天里的模块建议（V2 Issue 11）。
 *
 * 只提示可以一键以**原文**启动某个模块：这里没有任何检索动作，服务端也
 * 只在用户点击后才派发子图。点击后复用该轮用户消息原文，不新增用户消息、
 * 不改写历史模块标识。
 */
export function ModuleSuggestionCard({
  suggestion,
  onUse,
}: {
  suggestion: ModuleSuggestionProjection | null;
  onUse: (suggestion: ModuleSuggestionProjection) => void;
}) {
  if (!suggestion) return null;
  const label = suggestion.label || `使用${chatModuleLabel(suggestion.module_id) ?? "模块"}`;
  return (
    <section
      data-testid="module-suggestion"
      style={{
        margin: "var(--space-3) 0 0",
        padding: "var(--space-3)",
        border: "1px solid var(--color-border-strong)",
        borderRadius: "var(--radius-md)",
        backgroundColor: "var(--color-bg-secondary)",
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-2)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
        <Icon name={chatModuleIcon(suggestion.module_id)} size={16} aria-hidden />
        <span style={{ fontSize: "var(--text-sm)", color: "var(--color-text-secondary)" }}>
          {suggestion.reason}
        </span>
      </div>
      <button
        type="button"
        data-testid="module-suggestion-use"
        onClick={() => onUse(suggestion)}
        style={{
          alignSelf: "flex-start",
          minHeight: "var(--target-size)",
          padding: "var(--space-1) var(--space-3)",
          border: "1px solid var(--color-accent-primary)",
          borderRadius: "var(--radius-md)",
          backgroundColor: "var(--color-accent-primary-soft)",
          color: "var(--color-text-primary)",
          font: "inherit",
          fontWeight: 600,
          cursor: "pointer",
        }}
      >
        {label}
      </button>
      <span style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
        点击后以原文「{suggestion.text}」启动，不会自动检索。
        {suggestion.needs_disambiguation
          ? "该术语有多个语境，模块会先问你一个问题。"
          : ""}
      </span>
    </section>
  );
}
