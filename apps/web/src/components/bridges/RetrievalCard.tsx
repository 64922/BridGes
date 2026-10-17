"use client";

import { Icon } from "@/components/design-system/Icon";
import type { CitationProjection, RetrievalRoundProjection } from "@/lib/api";

function locationOf(citation: CitationProjection): string {
  const parts: string[] = [];
  if (citation.page_number !== null && citation.page_number !== undefined) {
    parts.push(`第 ${citation.page_number} 页`);
  }
  if (citation.section_title) parts.push(`章节：${citation.section_title}`);
  return parts.length > 0 ? parts.join(" · ") : "原文片段";
}

function CitationItem({
  citation,
  onOpenCitation,
}: {
  citation: CitationProjection;
  onOpenCitation?: (
    citation: CitationProjection,
    trigger: HTMLButtonElement
  ) => void;
}) {
  return (
    <li>
      <button
        type="button"
        onClick={(event) => onOpenCitation?.(citation, event.currentTarget)}
        aria-label={`查看资料 ${citation.filename}（${locationOf(citation)}）`}
        aria-haspopup="dialog"
        data-testid={`citation-item-${citation.rank}`}
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-2)",
          width: "100%",
          minHeight: "var(--target-size)",
          padding: "var(--space-1) var(--space-2)",
          border: "1px solid var(--color-border)",
          borderRadius: "var(--radius-md)",
          backgroundColor: "var(--color-surface)",
          color: "var(--color-text-secondary)",
          cursor: "pointer",
          font: "inherit",
          fontSize: "var(--text-sm)",
          textAlign: "left",
        }}
      >
        <Icon name="knowledgeBase" size={15} aria-hidden />
        <span
          style={{
            minWidth: 0,
            flex: 1,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
          title={citation.filename}
        >
          <span style={{ fontWeight: 600, color: "var(--color-text-primary)" }}>
            {citation.filename}
          </span>
          <span style={{ color: "var(--color-text-tertiary)" }}>
            {` · ${locationOf(citation)}`}
          </span>
        </span>
        <span style={{ color: "var(--color-text-tertiary)", fontSize: "var(--text-xs)" }}>
          查看资料
        </span>
        <Icon name="chevronRight" size={14} aria-hidden />
      </button>
    </li>
  );
}

/**
 * 只列出回答实际使用的紧凑来源入口；点击后在右侧资料面板读取详情。
 * 不解析正文里的文字编号，避免把未经核实的 `[1]` 伪造成可打开引用。
 */
export function RetrievalCard({
  retrieval,
  onOpenCitation,
}: {
  retrieval: RetrievalRoundProjection | null;
  onOpenCitation?: (
    citation: CitationProjection,
    trigger: HTMLButtonElement
  ) => void;
}) {
  const citations = retrieval?.citations ?? [];
  if (citations.length === 0) return null;

  return (
    <section
      data-testid="retrieval-citations-card"
      aria-label="回答引用"
      style={{
        margin: "var(--space-3) 0 0",
        padding: "var(--space-2) 0 0",
        borderTop: "1px solid var(--color-border)",
      }}
    >
      <ul
        role="list"
        aria-label="回答引用"
        data-testid="retrieval-citations"
        style={{
          display: "flex",
          flexDirection: "column",
          gap: "var(--space-1)",
          margin: 0,
          padding: 0,
          listStyle: "none",
        }}
      >
        {citations.map((citation) => (
          <CitationItem
            key={citation.citation_id}
            citation={citation}
            onOpenCitation={onOpenCitation}
          />
        ))}
      </ul>
    </section>
  );
}
