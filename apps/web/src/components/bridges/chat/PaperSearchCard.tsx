"use client";

import { Icon } from "@/components/design-system/Icon";
import type {
  ModuleQueryRecord,
  PaperRecommendation,
  PaperSearchProjection,
} from "@/lib/api";

/** V2 Issue 11：论文模块状态的中文标题（每条助手消息内如实显示）。 */
const STATUS_TITLES: Record<string, string> = {
  clarification: "需要先确认一个问题",
  searching: "正在检索 arXiv 论文",
  success: "论文搜索",
  empty: "没有找到匹配的论文",
  error: "论文搜索未完成",
  stopped: "已停止论文检索",
};

/** 阅读角色（与服务端 presenting 的中文标签一致）。 */
const ROLE_LABELS: Record<string, string> = {
  survey: "综述",
  tutorial: "教程",
  foundation: "奠基工作",
  recent: "较新研究",
};

const QUERY_STATUS_LABELS: Record<string, string> = {
  success: "成功",
  empty: "无结果",
  skipped: "未执行",
  timeout: "超时",
  cancelled: "已取消",
  rate_limited: "被上游限流",
  error: "失败",
};

const SOURCE_LABELS: Record<string, string> = {
  arxiv: "arXiv",
  crossref: "Crossref",
  openalex: "OpenAlex",
};

function formatTime(value: string | null | undefined): string {
  if (!value) return "时间未知";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "时间未知" : date.toLocaleString("zh-CN");
}

/** 一次外部调用的真实记录：来源、实际查询词、结果分类、取得时间与错误。 */
function QueryRecordRow({ record }: { record: ModuleQueryRecord }) {
  const failed = record.status === "error" || record.status === "timeout";
  return (
    <li style={{ color: failed ? "var(--color-status-error)" : "var(--color-text-secondary)" }}>
      <strong>{SOURCE_LABELS[record.source] ?? record.source}</strong>
      {" · 查询「"}
      {record.query}
      {"」 · "}
      {QUERY_STATUS_LABELS[record.status] ?? record.status}
      {`（${record.evidence_count} 条）`}
      {" · "}
      {formatTime(record.retrieved_at)}
      {/* 缓存命中/上游次数/冷却秒数是内部日志（interaction.md §6），不呈现给用户。 */}
      {record.error_message ? `：${record.error_message}` : ""}
    </li>
  );
}

/** 一篇推荐结果：阅读顺序、来源链接、全文可得性与理由。 */
function PaperRow({ paper }: { paper: PaperRecommendation }) {
  const authors = paper.authors ?? [];
  const unverified = paper.unverified ?? [];
  return (
    <li
      data-testid={`paper-result-${paper.order}`}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-1)",
        paddingBottom: "var(--space-2)",
        borderBottom: "1px solid var(--color-border)",
      }}
    >
      <p style={{ margin: 0, fontWeight: 600, color: "var(--color-text-primary)" }}>
        {paper.order}. 《{paper.title}》
      </p>
      <span style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
        {paper.published_year ?? "年份未知"} ·{" "}
        {ROLE_LABELS[paper.role] ?? paper.role} ·{" "}
        {SOURCE_LABELS[paper.source] ?? paper.source}
        {paper.primary_category ? ` · ${paper.primary_category}` : ""}
      </span>
      {authors.length > 0 && (
        <span style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
          作者：{authors.slice(0, 6).join("、")}
          {authors.length > 6 ? " 等" : ""}
        </span>
      )}
      <p style={{ margin: 0 }}>{paper.reason_zh}</p>
      <p style={{ margin: 0, color: "var(--color-text-tertiary)" }}>
        主题与来源核对：{paper.match_basis}
      </p>
      {paper.summary_zh && <p style={{ margin: 0 }}>中文概述：{paper.summary_zh}</p>}
      <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-3)" }}>
        <a
          href={paper.abs_url}
          target="_blank"
          rel="noreferrer"
          data-testid={`paper-${paper.order}-abs`}
          style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-1)" }}
        >
          <Icon name="paperSearch" size={14} aria-hidden />
          打开摘要页
        </a>
        {paper.full_text_available && paper.pdf_url ? (
          <a
            href={paper.pdf_url}
            target="_blank"
            rel="noreferrer"
            data-testid={`paper-${paper.order}-pdf`}
            style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-1)" }}
          >
            <Icon name="download" size={14} aria-hidden />
            打开全文 PDF
          </a>
        ) : (
          /* AC5：未取到全文时标明证据边界，不把摘要页说成全文。 */
          <span
            data-testid={`paper-${paper.order}-no-full-text`}
            style={{ color: "var(--color-status-wait)" }}
          >
            未确认取得全文（当前链接只到摘要页）
          </span>
        )}
      </div>
      {unverified.length > 0 && (
        <span style={{ fontSize: "var(--text-xs)", color: "var(--color-status-wait)" }}>
          未核实项：{unverified.join("；")}
        </span>
      )}
    </li>
  );
}

/**
 * 论文模块结果卡（V2 Issue 11）。
 *
 * 只渲染投影里的真实内容：解析出的原词与最终查询、每次外部调用的来源/
 * 查询/取得时间、按阅读顺序排列的真实论文与链接、以及实际数量与证据
 * 边界。澄清、无结果、失败与停止都在同一条消息内如实呈现，绝不假装
 * 检索成功或以模型记忆补足篇数。
 */
export function PaperSearchCard({
  search,
  streaming,
  onRetry,
}: {
  search: PaperSearchProjection | null;
  streaming: boolean;
  onRetry?: () => void;
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
    fontSize: "var(--text-sm)",
    color: "var(--color-text-secondary)",
  };
  const failed = search.status === "error";
  const waiting = search.status === "clarification";
  const papers = search.papers ?? [];
  const queries = search.queries ?? [];
  const notes = search.evidence_notes ?? [];
  const expansions = search.expansions ?? [];

  return (
    <section
      data-testid={`paper-search-card-${search.status}`}
      role={failed ? "alert" : "status"}
      aria-live="polite"
      aria-busy={streaming || search.status === "searching"}
      style={{ ...shellStyle, borderColor: failed ? "var(--color-status-error)" : "var(--color-border)" }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
        <Icon
          name={failed ? "alert" : waiting ? "info" : "paperSearch"}
          size={16}
          aria-hidden
        />
        <strong style={{ color: failed ? "var(--color-status-error)" : "var(--color-text-primary)" }}>
          {STATUS_TITLES[search.status] ?? "论文模块"}
        </strong>
        {search.status === "success" && (
          <span
            data-testid="paper-search-count"
            style={{
              marginLeft: "auto",
              padding: "2px var(--space-2)",
              borderRadius: "var(--radius-full)",
              backgroundColor: "var(--color-status-success-bg)",
              color: "var(--color-status-success)",
              fontSize: "var(--text-xs)",
              fontWeight: 600,
            }}
          >
            实际 {papers.length} 篇
            {search.requested_count > 0 && search.requested_count !== papers.length
              ? ` / 目标 ${search.requested_count} 篇`
              : ""}
          </span>
        )}
      </div>

      {/* AC4：保留原始术语与实际查询词（两者不同时同时呈现）。 */}
      {(search.original_phrase || search.final_query) && (
        <p style={{ margin: 0 }}>
          {search.original_phrase && (
            <>
              原始术语：<strong>{search.original_phrase}</strong>
            </>
          )}
          {search.original_phrase && search.final_query ? " · " : ""}
          {search.final_query && (
            <>
              实际查询词：<strong>{search.final_query}</strong>
            </>
          )}
        </p>
      )}
      {search.context_label && <p style={{ margin: 0 }}>语境：{search.context_label}</p>}
      {expansions.length > 0 && (
        <p style={{ margin: 0 }}>扩展词：{expansions.join("、")}</p>
      )}

      {waiting && search.pending && (
        <div
          data-testid="paper-search-clarification"
          style={{
            padding: "var(--space-2) var(--space-3)",
            border: "1px solid var(--color-border-strong)",
            borderRadius: "var(--radius-md)",
            backgroundColor: "var(--color-surface)",
          }}
        >
          <p style={{ margin: 0, fontWeight: 600, color: "var(--color-text-primary)" }}>
            {search.pending.question}
          </p>
          <p style={{ margin: "var(--space-1) 0 0", fontSize: "var(--text-xs)" }}>
            直接在下方回复即可；回复会带着「论文搜索」模块发出（输入框上方的模块标签可随时移除）。
          </p>
        </div>
      )}

      {failed && (
        <p style={{ margin: 0, color: "var(--color-status-error)" }}>
          {search.error_message ?? "本轮论文检索未完成。"}
          {search.error_code ? `（错误码：${search.error_code}）` : ""}
        </p>
      )}

      {queries.length > 0 && (
        <div>
          <p style={{ margin: 0, marginBottom: "var(--space-1)", fontWeight: 600 }}>
            本次外部调用记录
          </p>
          <ul
            data-testid="paper-search-queries"
            style={{
              margin: 0,
              paddingLeft: "var(--space-5)",
              listStyle: "disc",
              display: "flex",
              flexDirection: "column",
              gap: "var(--space-1)",
              fontSize: "var(--text-xs)",
            }}
          >
            {queries.map((record, index) => (
              <QueryRecordRow key={`${record.source}-${record.query}-${index}`} record={record} />
            ))}
          </ul>
        </div>
      )}

      {papers.length > 0 && (
        <ol
          data-testid="paper-search-results"
          style={{
            margin: 0,
            paddingLeft: "var(--space-5)",
            listStyle: "decimal",
            display: "flex",
            flexDirection: "column",
            gap: "var(--space-2)",
          }}
        >
          {papers.map((paper) => (
            <PaperRow key={`${paper.order}-${paper.title}`} paper={paper} />
          ))}
        </ol>
      )}

      {notes.length > 0 && (
        <div>
          <p style={{ margin: 0, fontWeight: 600 }}>证据边界</p>
          <ul
            data-testid="paper-search-notes"
            style={{
              margin: 0,
              paddingLeft: "var(--space-5)",
              listStyle: "disc",
              display: "flex",
              flexDirection: "column",
              gap: "var(--space-1)",
            }}
          >
            {notes.map((note, index) => (
              <li key={`${note}-${index}`}>{note}</li>
            ))}
          </ul>
        </div>
      )}

      {search.retryable && onRetry && (
        <button
          type="button"
          data-testid="paper-search-retry"
          onClick={onRetry}
          style={{
            alignSelf: "flex-start",
            minHeight: "var(--target-size)",
            padding: "var(--space-1) var(--space-3)",
            border: "1px solid var(--color-status-error)",
            borderRadius: "var(--radius-md)",
            backgroundColor: "transparent",
            color: "var(--color-status-error)",
            font: "inherit",
            fontWeight: 600,
            cursor: "pointer",
          }}
        >
          <Icon name="retry" size={14} aria-hidden /> 重试论文搜索
        </button>
      )}
    </section>
  );
}
