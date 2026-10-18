"use client";

import { Icon } from "@/components/design-system/Icon";
import type { PaperSearchProjection } from "@/lib/api";

function partialNotice(search: PaperSearchProjection): string | null {
  if (search.status !== "success") return null;
  const limited = (search.queries ?? []).some((record) =>
    ["error", "timeout", "rate_limited", "cancelled"].includes(record.status)
  );
  if (limited) {
    return "部分来源未成功返回；以下推荐只基于本轮实际取得的记录。";
  }
  const papers = search.papers?.length ?? 0;
  if (search.requested_count > papers) {
    return `目标 ${search.requested_count} 篇，本轮实际 ${papers} 篇；未用模型记忆补足。`;
  }
  return null;
}

function hasInspectableDetails(search: PaperSearchProjection): boolean {
  return (
    (search.papers?.length ?? 0) > 0 ||
    (search.candidates?.length ?? 0) > 0 ||
    (search.queries?.length ?? 0) > 0 ||
    (search.citation_edges?.length ?? 0) > 0 ||
    (search.condition_sources?.length ?? 0) > 0 ||
    (search.evidence_notes?.length ?? 0) > 0
  );
}

export function PaperDetailsEntry({
  search,
  onOpen,
}: {
  search: PaperSearchProjection | null;
  onOpen?: (trigger: HTMLButtonElement) => void;
}) {
  if (!search || search.status === "searching" || !hasInspectableDetails(search)) {
    return null;
  }
  const notice = partialNotice(search);
  const hasPapers = (search.papers?.length ?? 0) > 0;
  return (
    <div
      data-testid="paper-details-entry"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-1)",
        margin: "var(--space-3) 0 0",
        paddingTop: "var(--space-2)",
        borderTop: "1px solid var(--color-border)",
        fontSize: "var(--text-sm)",
        color: "var(--color-text-secondary)",
      }}
    >
      {notice && (
        <p
          role="status"
          data-testid="paper-source-notice"
          style={{
            margin: 0,
            color: "var(--color-status-wait)",
            fontSize: "var(--text-xs)",
            lineHeight: "var(--line-height-relaxed)",
          }}
        >
          {notice}
        </p>
      )}
      <button
        type="button"
        data-testid="paper-details-open"
        onClick={(event) => onOpen?.(event.currentTarget)}
        style={{
          alignSelf: "flex-start",
          display: "inline-flex",
          alignItems: "center",
          gap: "var(--space-1)",
          minHeight: "var(--target-size)",
          padding: "var(--space-1) 0",
          border: "none",
          background: "transparent",
          color: "var(--color-accent-secondary)",
          cursor: "pointer",
          font: "inherit",
          fontSize: "var(--text-sm)",
          fontWeight: 600,
        }}
      >
        <Icon name="paperSearch" size={15} aria-hidden />
        {hasPapers ? "查看论文依据、摘要与检索过程" : "查看本轮检索记录与依据"}
      </button>
    </div>
  );
}
