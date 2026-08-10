"use client";

import { useCallback, useEffect, useState } from "react";

import { Icon, type IconName } from "@/components/design-system/Icon";
import {
  getCitationDetail,
  type CitationDetailProjection,
  type CitationProjection,
  type RetrievalRoundProjection,
} from "@/lib/api";

function locationOf(citation: CitationProjection): string {
  const parts: string[] = [];
  if (citation.page_number !== null && citation.page_number !== undefined) {
    parts.push(`第 ${citation.page_number} 页`);
  }
  if (citation.section_title) parts.push(`章节：${citation.section_title}`);
  return parts.length > 0 ? parts.join(" · ") : "原文片段";
}

function accessMeta(status: CitationDetailProjection["access_status"]): {
  label: string;
  icon: IconName;
  color: string;
  background: string;
} {
  if (status === "accessible") {
    return {
      label: "原文可访问",
      icon: "check",
      color: "var(--color-status-success)",
      background: "var(--color-status-success-bg)",
    };
  }
  if (status === "deleted") {
    return {
      label: "原文已删除",
      icon: "alert",
      color: "var(--color-status-error)",
      background: "var(--color-status-error-bg)",
    };
  }
  return {
    label: "授权已变化",
    icon: "account",
    color: "var(--color-status-wait)",
    background: "var(--color-status-wait-bg)",
  };
}

function CitationItem({
  conversationId,
  messageId,
  citation,
}: {
  conversationId: string;
  messageId: string;
  citation: CitationProjection;
}) {
  const [expanded, setExpanded] = useState(false);
  const [detail, setDetail] = useState<CitationDetailProjection | null>(null);
  const [loading, setLoading] = useState(false);
  const [fetchError, setFetchError] = useState("");

  const loadDetail = useCallback(async () => {
    setLoading(true);
    setFetchError("");
    try {
      setDetail(
        await getCitationDetail(conversationId, messageId, citation.citation_id)
      );
    } catch {
      setFetchError("证据详情加载失败，请稍后重试。");
    } finally {
      setLoading(false);
    }
  }, [conversationId, messageId, citation.citation_id]);

  useEffect(() => {
    if (expanded && !detail && !loading) void loadDetail();
  }, [expanded, detail, loading, loadDetail]);

  const meta = detail ? accessMeta(detail.access_status) : null;

  return (
    <li>
      <button
        type="button"
        aria-expanded={expanded}
        aria-controls={`citation-detail-${citation.citation_id}`}
        onClick={() => setExpanded((value) => !value)}
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
        <Icon name={expanded ? "chevronDown" : "chevronRight"} size={14} aria-hidden />
      </button>
      {expanded && (
        <div
          id={`citation-detail-${citation.citation_id}`}
          data-testid={`citation-detail-${citation.rank}`}
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "flex-start",
            gap: "var(--space-2)",
            marginTop: "var(--space-1)",
            padding: "var(--space-2) var(--space-3)",
            borderRadius: "var(--radius-md)",
            backgroundColor: "var(--color-bg-secondary)",
            fontSize: "var(--text-sm)",
            color: "var(--color-text-secondary)",
          }}
        >
          {loading && (
            <span role="status" style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-1)" }}>
              <span className="bg-spinner" aria-hidden="true" />加载证据详情…
            </span>
          )}
          {fetchError && (
            <span
              role="alert"
              data-testid={"citation-detail-error-" + citation.rank}
              style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-2)", color: "var(--color-status-error)" }}
            >
              <span>证据详情加载失败：{fetchError}</span>
              <button
                type="button"
                onClick={() => void loadDetail()}
                data-testid={"citation-detail-retry-" + citation.rank}
                aria-label={`重试证据详情 ${citation.filename}`}
                style={{
                  minHeight: "var(--target-size)",
                  padding: "var(--space-1) var(--space-2)",
                  border: "1px solid currentColor",
                  borderRadius: "var(--radius-md)",
                  backgroundColor: "transparent",
                  color: "inherit",
                  cursor: "pointer",
                  font: "inherit",
                  fontSize: "var(--text-xs)",
                }}
              >
                重试
              </button>
            </span>
          )}
          {detail && !loading && meta && (
            <>
              <blockquote
                style={{
                  margin: 0,
                  paddingLeft: "var(--space-3)",
                  borderLeft: "3px solid var(--color-border-strong)",
                  color: "var(--color-text-primary)",
                  whiteSpace: "pre-wrap",
                  overflowWrap: "break-word",
                }}
              >
                {detail.citation.snippet}
              </blockquote>
              <span
                role={detail.access_status === "accessible" ? "status" : "alert"}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "var(--space-1)",
                  padding: "2px var(--space-2)",
                  borderRadius: "999px",
                  fontSize: "var(--text-xs)",
                  fontWeight: 600,
                  color: meta.color,
                  backgroundColor: meta.background,
                }}
              >
                <Icon name={meta.icon} size={14} aria-hidden />
                {meta.label}
              </span>
              {detail.access_message && detail.access_status !== "accessible" && (
                <span style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
                  {detail.access_message}
                </span>
              )}
              {detail.download_url && (
                <a
                  href={detail.download_url}
                  data-testid={`citation-open-${citation.rank}`}
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: "var(--space-1)",
                    padding: "var(--space-1) var(--space-2)",
                    border: "1px solid var(--color-border-strong)",
                    borderRadius: "var(--radius-md)",
                    color: "var(--color-accent-secondary)",
                    font: "inherit",
                    fontSize: "var(--text-sm)",
                    fontWeight: 600,
                    textDecoration: "none",
                    minHeight: "var(--target-size)",
                  }}
                >
                  <Icon name="download" size={14} aria-hidden />打开原文
                </a>
              )}
            </>
          )}
        </div>
      )}
    </li>
  );
}

/** 只展示回答实际使用的紧凑引用；检索过程和空状态不再进入会话线程。 */
export function RetrievalCard({
  retrieval,
  conversationId,
  messageId,
}: {
  retrieval: RetrievalRoundProjection | null;
  conversationId: string;
  messageId: string;
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
            conversationId={conversationId}
            messageId={messageId}
            citation={citation}
          />
        ))}
      </ul>
    </section>
  );
}
