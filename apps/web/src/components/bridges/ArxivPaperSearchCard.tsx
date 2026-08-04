"use client";

import { Icon } from "@/components/design-system/Icon";
import type { ArxivPaperProjection, ArxivSearchProjection } from "@/lib/api";

function dateOf(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "发布日期未知" : date.toLocaleDateString("zh-CN");
}

function PaperCitation({ paper }: { paper: ArxivPaperProjection }) {
  return (
    <li>
      <details
        data-testid={`arxiv-citation-${paper.citation_id}`}
        style={{
          border: "1px solid var(--color-border)",
          borderRadius: "var(--radius-md)",
          backgroundColor: "var(--color-surface)",
        }}
      >
        <summary
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-2)",
            minHeight: "var(--target-size)",
            padding: "var(--space-2) var(--space-3)",
            cursor: "pointer",
            fontSize: "var(--text-sm)",
            color: "var(--color-text-primary)",
          }}
        >
          <Icon name="paperSearch" size={16} aria-hidden />
          <span style={{ flex: 1, fontWeight: 600 }}>{paper.title}</span>
          <span style={{ color: "var(--color-text-tertiary)", fontSize: "var(--text-xs)" }}>
            {paper.citation_id}
          </span>
        </summary>
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: "var(--space-2)",
            padding: "0 var(--space-3) var(--space-3)",
            fontSize: "var(--text-sm)",
            color: "var(--color-text-secondary)",
          }}
        >
          <p style={{ margin: 0 }}>
            <strong>作者：</strong>{paper.authors.join("、")} · <strong>发布日期：</strong>
            {dateOf(paper.published_at)} · <strong>arXiv：</strong>{paper.arxiv_id}
          </p>
          <p style={{ margin: 0, lineHeight: "var(--line-height-relaxed)" }}>{paper.summary_zh}</p>
          <p style={{ margin: 0, lineHeight: "var(--line-height-relaxed)" }}>
            <strong>相关依据：</strong>{paper.relevance_basis}
          </p>
          <p style={{ margin: 0, lineHeight: "var(--line-height-relaxed)" }}>
            <strong>学习建议：</strong>{paper.learning_advice_zh}
          </p>
          <details style={{ borderTop: "1px solid var(--color-border)", paddingTop: "var(--space-2)" }}>
            <summary style={{ cursor: "pointer", minHeight: "var(--target-size)" }}>
              查看 arXiv 原始摘要
            </summary>
            <p style={{ margin: "var(--space-2) 0 0", whiteSpace: "pre-wrap", lineHeight: "var(--line-height-relaxed)" }}>
              {paper.abstract}
            </p>
          </details>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-2)" }}>
            <a
              href={paper.abs_url}
              target="_blank"
              rel="noreferrer"
              data-testid={`arxiv-abs-${paper.citation_id}`}
              style={{ minHeight: "var(--target-size)", display: "inline-flex", alignItems: "center", gap: "var(--space-1)", fontWeight: 600 }}
            >
              <Icon name="chevronRight" size={14} aria-hidden />打开摘要页
            </a>
            <a
              href={paper.pdf_url}
              target="_blank"
              rel="noreferrer"
              data-testid={`arxiv-pdf-${paper.citation_id}`}
              style={{ minHeight: "var(--target-size)", display: "inline-flex", alignItems: "center", gap: "var(--space-1)", fontWeight: 600 }}
            >
              <Icon name="download" size={14} aria-hidden />打开 PDF
            </a>
          </div>
        </div>
      </details>
    </li>
  );
}

/** Issue 22：arXiv MCP 工具卡，展示最小查询、结果引用与全部恢复状态。 */
export function ArxivPaperSearchCard({
  search,
  streaming,
  onRetry,
  onCancel,
}: {
  search: ArxivSearchProjection | null;
  streaming: boolean;
  onRetry: () => void;
  onCancel?: () => void;
}) {
  if (!search) return null;
  const shellStyle: React.CSSProperties = {
    margin: "var(--space-3) 0 0",
    padding: "var(--space-3)",
    border: "1px solid var(--color-border)",
    borderRadius: "var(--radius-md)",
    backgroundColor: "var(--color-bg-secondary)",
    display: "flex",
    flexDirection: "column",
    gap: "var(--space-2)",
  };

  if (search.status === "loading" || search.status === "recovery") {
    return (
      <section data-testid="arxiv-search-card-loading" role="status" aria-live="polite" style={shellStyle}>
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
          <span className="bg-spinner" aria-hidden="true" />
          <strong>{search.status === "recovery" ? "正在恢复论文搜索" : "正在搜索 arXiv 论文"}</strong>
          {search.can_cancel && onCancel && (
            <button
              type="button"
              onClick={onCancel}
              data-testid="arxiv-search-cancel"
              style={{ marginLeft: "auto", minHeight: "var(--target-size)", padding: "var(--space-1) var(--space-2)", border: "1px solid var(--color-border-strong)", borderRadius: "var(--radius-md)", backgroundColor: "transparent", color: "var(--color-text-secondary)", cursor: "pointer", font: "inherit" }}
            >
              取消本轮
            </button>
          )}
        </div>
        <span style={{ fontSize: "var(--text-sm)", color: "var(--color-text-secondary)" }}>
          {search.trigger_reason} · 已确认主题：{search.query_summary}
        </span>
      </section>
    );
  }

  if (search.status === "cancelled") {
    return (
      <section data-testid="arxiv-search-card-cancelled" role="status" aria-live="polite" style={shellStyle}>
        <span style={{ color: "var(--color-text-secondary)", fontSize: "var(--text-sm)" }}>
          <Icon name="info" size={16} aria-hidden /> 已取消本轮论文搜索，未以模型记忆替代真实结果。
        </span>
      </section>
    );
  }

  const failed = search.status === "error" || search.status === "permission";
  if (failed || search.status === "empty") {
    return (
      <section
        data-testid={`arxiv-search-card-${search.status}`}
        role={failed ? "alert" : "status"}
        aria-live="polite"
        style={{ ...shellStyle, borderColor: failed ? "var(--color-status-error)" : "var(--color-border)" }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
          <Icon name={failed ? "alert" : "info"} size={16} aria-hidden />
          <strong>
            {search.status === "permission"
              ? "arXiv 网络权限未通过"
              : search.status === "empty"
                ? "没有找到匹配的 arXiv 论文"
                : "arXiv 论文搜索未完成"}
          </strong>
          {search.can_retry && (
            <button
              type="button"
              onClick={onRetry}
              data-testid="arxiv-search-retry"
              style={{ marginLeft: "auto", minHeight: "var(--target-size)", padding: "var(--space-1) var(--space-2)", border: "1px solid var(--color-status-error)", borderRadius: "var(--radius-md)", backgroundColor: "transparent", color: "var(--color-status-error)", cursor: "pointer", font: "inherit", fontWeight: 600 }}
            >
              <Icon name="retry" size={14} aria-hidden />重试论文搜索
            </button>
          )}
        </div>
        <span style={{ fontSize: "var(--text-sm)", color: "var(--color-text-secondary)" }}>
          {search.error_message ?? "请调整主题或检查网络后重试。"}
        </span>
      </section>
    );
  }

  const papers = search.papers ?? [];

  return (
    <section data-testid="arxiv-search-card" aria-live="polite" aria-busy={streaming} style={shellStyle}>
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
        <Icon name="paperSearch" size={17} aria-hidden />
        <strong>arXiv 论文搜索</strong>
        <span role="status" style={{ marginLeft: "auto", padding: "2px var(--space-2)", borderRadius: "var(--radius-full)", color: "var(--color-status-success)", backgroundColor: "var(--color-status-success-bg)", fontSize: "var(--text-xs)", fontWeight: 600 }}>
          已返回 {papers.length} 篇真实论文
        </span>
      </div>
      <span style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
        已确认主题：{search.query_summary} · 论文主张请展开引用核对
      </span>
      <ol aria-label="arXiv 论文引用" data-testid="arxiv-search-results" style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)", margin: 0, padding: 0, listStyle: "none" }}>
        {papers.map((paper) => <PaperCitation key={paper.citation_id} paper={paper} />)}
      </ol>
    </section>
  );
}
