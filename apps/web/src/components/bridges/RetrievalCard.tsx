"use client";

import { useCallback, useEffect, useState } from "react";

import { Icon, type IconName } from "@/components/design-system/Icon";
import {
  getCitationDetail,
  type CitationDetailProjection,
  type CitationProjection,
  type RetrievalDecisionProjection,
  type RetrievalLayerResult,
  type RetrievalLayerStatus,
  type RetrievalRoundProjection,
  type RetrievalSourceLayer,
  type RetrievalSufficiency,
} from "@/lib/api";

/**
 * 分层本地检索与引用卡（Issue 20）。
 *
 * 展示本轮检索的来源层状态、证据充足性信号与最终引用；点击引用按需请求
 * 证据详情（加载/失败/权限变化均有中文状态），原文可访问时给出打开入口。
 * 状态覆盖：loading（生成中/详情加载中）、empty（无命中，含引导）、
 * error（索引不可用，可重试）、permission（授权变化）、recovery（重试中）。
 * 重试复用消息级重试，不重复用户消息。
 */

const LAYER_META: Record<
  RetrievalSourceLayer,
  { label: string; icon: IconName }
> = {
  attachment: { label: "当前附件", icon: "uploadFile" },
  project: { label: "学习项目文件", icon: "learningProject" },
  knowledge_base: { label: "全局知识库", icon: "knowledgeBase" },
};

const LAYER_STATUS_META: Record<
  RetrievalLayerStatus,
  { label: string; icon: IconName; color: string; bg: string }
> = {
  ok: { label: "已检索", icon: "check", color: "var(--color-status-success)", bg: "var(--color-status-success-bg)" },
  disabled: { label: "未启用", icon: "info", color: "var(--color-text-tertiary)", bg: "var(--color-surface-muted, #f1f2f3)" },
  no_material: { label: "无材料", icon: "info", color: "var(--color-text-tertiary)", bg: "var(--color-surface-muted, #f1f2f3)" },
  index_unavailable: { label: "索引不可用", icon: "alert", color: "var(--color-status-error)", bg: "var(--color-status-error-bg)" },
  index_processing: { label: "索引处理中", icon: "info", color: "var(--color-status-wait)", bg: "var(--color-status-wait-bg)" },
  index_corrupt: { label: "索引损坏", icon: "alert", color: "var(--color-status-error)", bg: "var(--color-status-error-bg)" },
  timeout: { label: "检索超时", icon: "alert", color: "var(--color-status-error)", bg: "var(--color-status-error-bg)" },
};

const SUFFICIENCY_META: Record<
  RetrievalSufficiency,
  { label: string; icon: IconName; color: string; bg: string; role: "status" | "alert" }
> = {
  sufficient: {
    label: "已检索到足够材料",
    icon: "check",
    color: "var(--color-status-success)",
    bg: "var(--color-status-success-bg)",
    role: "status",
  },
  insufficient_coverage: {
    label: "材料覆盖不足",
    icon: "info",
    color: "var(--color-status-wait)",
    bg: "var(--color-status-wait-bg)",
    role: "status",
  },
  no_hits: {
    label: "未找到匹配材料",
    icon: "paperSearch",
    color: "var(--color-text-tertiary)",
    bg: "var(--color-surface-muted, #f1f2f3)",
    role: "status",
  },
  conflict: {
    label: "候选来源存在冲突",
    icon: "alert",
    color: "var(--color-status-wait)",
    bg: "var(--color-status-wait-bg)",
    role: "status",
  },
  index_unavailable: {
    label: "本地索引不可用",
    icon: "alert",
    color: "var(--color-status-error)",
    bg: "var(--color-status-error-bg)",
    role: "alert",
  },
};

function locationOf(citation: CitationProjection): string {
  const parts: string[] = [];
  if (citation.page_number !== null && citation.page_number !== undefined) {
    parts.push(`第 ${citation.page_number} 页`);
  }
  if (citation.section_title) {
    parts.push(`章节：${citation.section_title}`);
  }
  return parts.length > 0 ? parts.join(" · ") : "原文片段";
}

/** 单条引用的展开详情：片段 + 授权状态 + 打开原文入口。 */
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
      const projection = await getCitationDetail(
        conversationId,
        messageId,
        citation.citation_id
      );
      setDetail(projection);
    } catch (error) {
      setFetchError(error instanceof Error ? error.message : "证据详情加载失败。");
    } finally {
      setLoading(false);
    }
  }, [conversationId, messageId, citation.citation_id]);

  useEffect(() => {
    if (!expanded || detail || loading) return;
    void loadDetail();
  }, [expanded, detail, loading, loadDetail]);

  const layer = LAYER_META[citation.source_layer];
  const statusMeta =
    detail?.access_status === "accessible"
      ? { label: "原文可访问", icon: "check" as IconName, color: "var(--color-status-success)", bg: "var(--color-status-success-bg)" }
      : detail?.access_status === "deleted"
        ? { label: "原文已删除", icon: "alert" as IconName, color: "var(--color-status-error)", bg: "var(--color-status-error-bg)" }
        : { label: "授权已变化", icon: "account" as IconName, color: "var(--color-status-wait)", bg: "var(--color-status-wait-bg)" };

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
        <Icon name={layer.icon} size={15} aria-hidden />
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
          <span style={{ color: "var(--color-text-tertiary)" }}> · {locationOf(citation)}</span>
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
            <span role="alert" style={{ color: "var(--color-status-error)" }}>
              证据详情加载失败：{fetchError}
            </span>
          )}
          {detail && !loading && (
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
                  alignSelf: "flex-start",
                  padding: "2px var(--space-2)",
                  borderRadius: "999px",
                  fontSize: "var(--text-xs)",
                  fontWeight: 600,
                  color: statusMeta.color,
                  backgroundColor: statusMeta.bg,
                }}
              >
                <Icon name={statusMeta.icon} size={14} aria-hidden />
                {statusMeta.label}
                {detail.access_status === "permission_changed" && "，无法打开原文"}
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
                    alignSelf: "flex-start",
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

/** 单层检索结果行：层名 + 状态 + 候选数/说明。 */
function LayerRow({ layer }: { layer: RetrievalLayerResult }) {
  const meta = LAYER_META[layer.layer];
  const status = LAYER_STATUS_META[layer.status] ?? LAYER_STATUS_META.disabled;
  const countText =
    layer.status === "ok" && layer.candidates > 0
      ? `${layer.candidates} 条候选`
      : layer.note ?? status.label;
  return (
    <div
      data-testid={`retrieval-layer-${layer.layer}`}
      style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}
    >
      <Icon name={meta.icon} size={14} aria-hidden />
      <span style={{ color: "var(--color-text-secondary)", fontWeight: 500 }}>{meta.label}</span>
      <span
        role="status"
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: "var(--space-1)",
          padding: "1px var(--space-2)",
          borderRadius: "999px",
          fontSize: "var(--text-xs)",
          fontWeight: 600,
          color: status.color,
          backgroundColor: status.bg,
        }}
      >
        {status.label}
      </span>
      {layer.status === "ok" && (
        <span style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
          {countText}
        </span>
      )}
      {layer.status !== "ok" && layer.note && (
        <span style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
          {layer.note}
        </span>
      )}
    </div>
  );
}

/** 检索/引用卡主组件：加载、空、错误、权限、恢复状态全覆盖。 */
export function RetrievalCard({
  retrieval,
  retrievalDecision,
  conversationId,
  messageId,
  streaming,
  onRetry,
}: {
  retrieval: RetrievalRoundProjection | null;
  retrievalDecision?: RetrievalDecisionProjection | null;
  conversationId: string;
  messageId: string;
  streaming: boolean;
  onRetry: () => void;
}) {
  // 新合同不展示独立的本地检索过程卡；无决策字段的历史消息保留旧兼容态。
  if (
    !retrieval &&
    streaming &&
    retrievalDecision == null
  ) {
    return (
      <section
        data-testid="retrieval-card-loading"
        role="status"
        aria-live="polite"
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-2)",
          margin: "var(--space-3) 0 0",
          padding: "var(--space-2) var(--space-3)",
          borderRadius: "var(--radius-md)",
          border: "1px solid var(--color-border)",
          backgroundColor: "var(--color-bg-secondary)",
          fontSize: "var(--text-sm)",
          color: "var(--color-text-secondary)",
        }}
      >
        <span className="bg-spinner" aria-hidden="true" />
        正在检索本地材料（附件 → 项目 → 知识库）…
      </section>
    );
  }
  if (!retrieval) return null;

  const sufficiency = SUFFICIENCY_META[retrieval.sufficiency];
  const legacyLayered = retrievalDecision == null;
  const needsRetry = legacyLayered && retrieval.sufficiency === "index_unavailable";
  const hasCitations = (retrieval.citations ?? []).length > 0;
  if (!legacyLayered && !hasCitations) return null;

  return (
    <section
      data-testid="retrieval-card"
      aria-live="polite"
      style={{
        margin: "var(--space-3) 0 0",
        padding: "var(--space-3)",
        borderRadius: "var(--radius-md)",
        border: "1px solid var(--color-border)",
        backgroundColor: "var(--color-bg-secondary)",
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-2)",
      }}
    >
      {legacyLayered && (
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", flexWrap: "wrap" }}>
          <Icon name="paperSearch" size={16} aria-hidden />
          <span style={{ fontWeight: 600, fontSize: "var(--text-sm)", color: "var(--color-text-secondary)" }}>
            本地检索
          </span>
          <span
            role={sufficiency.role}
            data-testid={`retrieval-sufficiency-${retrieval.sufficiency}`}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "var(--space-1)",
              padding: "2px var(--space-2)",
              borderRadius: "999px",
              fontSize: "var(--text-xs)",
              fontWeight: 600,
              color: sufficiency.color,
              backgroundColor: sufficiency.bg,
            }}
          >
            <Icon name={sufficiency.icon} size={13} aria-hidden />
            {sufficiency.label}
          </span>
          {needsRetry && (
            <button
              type="button"
              onClick={onRetry}
              data-testid="retrieval-retry"
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: "var(--space-1)",
                marginLeft: "auto",
                padding: "var(--space-1) var(--space-2)",
                border: "1px solid var(--color-status-error)",
                borderRadius: "var(--radius-md)",
                backgroundColor: "transparent",
                color: "var(--color-status-error)",
                cursor: "pointer",
                font: "inherit",
                fontSize: "var(--text-xs)",
                fontWeight: 600,
                minHeight: "var(--target-size)",
              }}
            >
              <Icon name="retry" size={13} aria-hidden />
              重试检索
            </button>
          )}
        </div>
      )}

      {legacyLayered && (
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
          {(retrieval.layers ?? []).map((layer) => (
            <LayerRow key={layer.layer} layer={layer} />
          ))}
        </div>
      )}

      {legacyLayered && retrieval.note && (
        <p role="status" style={{ margin: 0, fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
          {retrieval.note}
        </p>
      )}

      {hasCitations ? (
        <ul
          role="list"
          aria-label="回答引用的本地材料"
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
          {(retrieval.citations ?? []).map((citation) => (
            <CitationItem
              key={citation.citation_id}
              conversationId={conversationId}
              messageId={messageId}
              citation={citation}
            />
          ))}
        </ul>
      ) : (
        <p
          role="status"
          data-testid="retrieval-empty"
          style={{ margin: 0, fontSize: "var(--text-sm)", color: "var(--color-text-secondary)" }}
        >
          {retrieval.sufficiency === "index_unavailable"
            ? "本地索引不可用，暂时无法检索；可重试本回答后再试。"
            : "本轮没有检索到与问题相关的本地材料，回答基于模型自身知识组织。"}
        </p>
      )}
    </section>
  );
}
