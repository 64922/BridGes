"use client";

import { Icon } from "@/components/design-system/Icon";
import type {
  PaperConditionSource,
  PaperCandidateProjection,
  PaperRecommendation,
  PaperSearchProjection,
} from "@/lib/api";

import { QueryRecordList } from "./QueryRecordList";
import { PaperCitationGraph } from "./PaperCitationGraph";

/** V2 Issue 11：论文模块状态的中文标题（每条助手消息内如实显示）。 */
const STATUS_TITLES: Record<string, string> = {
  clarification: "需要先确认一个问题",
  searching: "正在检索学术来源",
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
  related: "相关研究",
};

const SOURCE_LABELS: Record<string, string> = {
  arxiv: "arXiv",
  crossref: "Crossref",
  openalex: "OpenAlex",
};

const RELEVANCE_LABELS: Record<string, string> = {
  direct: "直接相关",
  partial: "覆盖部分问题",
  exploratory: "探索线索",
};

const READ_SCOPE_LABELS: Record<string, string> = {
  abstract: "本轮只读取标题与摘要",
  partial: "本轮读取了部分正文",
  full_text: "本轮读取了全文",
};

function formatCondition(condition: PaperConditionSource) {
  let value = condition.value;
  if (condition.kind === "year") {
    const [from, to] = value.split(":");
    value = from && to ? `${from}–${to} 年` : from ? `${from} 年起` : `${to} 年及以前`;
    return `年份 ${value}`;
  }
  if (condition.kind === "count") return `篇数 ${value} 篇`;
  if (condition.kind === "source") return `来源 ${SOURCE_LABELS[value] ?? value}`;
  if (condition.kind === "paper") return `指定论文 ${value}`;
  if (condition.kind === "type" && value === "exclude_survey") return "排除综述";
  return `${condition.kind} ${value}`;
}

function formatDiscoveryPath(path: string) {
  if (path.startsWith("citation:")) {
    return `OpenAlex 引用扩展（种子 ${path.slice("citation:".length)}）`;
  }
  const separator = path.indexOf(":");
  const source = separator < 0 ? path : path.slice(0, separator);
  const sourceId = separator < 0 ? "" : path.slice(separator + 1);
  const sourceLabel = SOURCE_LABELS[source] ?? source;
  return sourceId ? `${sourceLabel} 检索（${sourceId}）` : `${sourceLabel} 检索`;
}

function EvidenceList({
  evidence,
  testId,
}: {
  evidence: Array<{ requirement: string; source: string; quote: string }>;
  testId: string;
}) {
  if (evidence.length === 0) return null;
  return (
    <ul
      data-testid={testId}
      style={{
        margin: 0,
        paddingLeft: "var(--space-5)",
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-1)",
      }}
    >
      {evidence.map((item, index) => (
        <li key={`${item.requirement}-${index}`}>
          <strong>{item.requirement}</strong> · {item.source}：「{item.quote}」
        </li>
      ))}
    </ul>
  );
}

/** 一篇推荐结果：阅读顺序、来源链接、全文可得性与理由。 */
function PaperRow({ paper }: { paper: PaperRecommendation }) {
  const authors = paper.authors ?? [];
  const unverified = paper.unverified ?? [];
  const sources = paper.sources?.length ? paper.sources : [paper.source];
  const fullTextUrl = paper.pdf_url || paper.full_text_url;
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
        {sources.map((source) => SOURCE_LABELS[source] ?? source).join("、")}
        {paper.primary_category ? ` · ${paper.primary_category}` : ""}
      </span>
      <span style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
        {RELEVANCE_LABELS[paper.relevance] ?? "相关性待核对"}
        {paper.research_group ? ` · ${paper.research_group}` : ""}
        {paper.cited_by_count != null ? ` · OpenAlex/Crossref 引用记录 ${paper.cited_by_count}` : ""}
      </span>
      {authors.length > 0 && (
        <span style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
          作者：{authors.slice(0, 6).join("、")}
          {authors.length > 6 ? " 等" : ""}
        </span>
      )}
      <p style={{ margin: 0 }}>{paper.reason_zh}</p>
      <p style={{ margin: 0, color: "var(--color-text-tertiary)" }}>相关性说明：{paper.match_basis}</p>
      <EvidenceList
        evidence={paper.match_evidence ?? []}
        testId={`paper-${paper.order}-evidence`}
      />
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
        {fullTextUrl ? (
          <a
            href={fullTextUrl}
            target="_blank"
            rel="noreferrer"
            data-testid={`paper-${paper.order}-pdf`}
            style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-1)" }}
          >
            <Icon name="download" size={14} aria-hidden />
            打开全文链接
          </a>
        ) : (
          <span
            data-testid={`paper-${paper.order}-no-full-text`}
            style={{ color: "var(--color-status-wait)" }}
          >
            来源未返回全文链接
          </span>
        )}
        <span data-testid={`paper-${paper.order}-read-scope`}>
          {READ_SCOPE_LABELS[paper.read_scope] ?? "本轮读取范围未确认"}
        </span>
      </div>
      {unverified.length > 0 && (
        <span style={{ fontSize: "var(--text-xs)", color: "var(--color-status-wait)" }}>
          未核实项：{unverified.join("；")}
        </span>
      )}
    </li>
  );
}

function CandidateRow({ candidate }: { candidate: PaperCandidateProjection }) {
  const sources = candidate.sources?.length
    ? candidate.sources
    : [candidate.primary_source];
  const fullTextUrl = candidate.pdf_url || candidate.full_text_url;
  const discoveryPaths = candidate.discovery_paths ?? [];
  return (
    <li style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
      <strong style={{ color: "var(--color-text-primary)" }}>{candidate.title}</strong>
      <span style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
        {candidate.published_year ?? "年份未知"} · {RELEVANCE_LABELS[candidate.relevance] ?? "相关性待核对"}
        {candidate.research_group ? ` · ${candidate.research_group}` : ""} ·{" "}
        {sources.map((source) => SOURCE_LABELS[source] ?? source).join("、")}
      </span>
      <EvidenceList
        evidence={candidate.match_evidence ?? []}
        testId={`candidate-${candidate.candidate_id}-evidence`}
      />
      {candidate.abstract && <p style={{ margin: 0 }}>摘要：{candidate.abstract}</p>}
      {discoveryPaths.length > 0 && (
        <p data-testid={`candidate-${candidate.candidate_id}-paths`} style={{ margin: 0 }}>
          发现路径：{discoveryPaths.map(formatDiscoveryPath).join("；")}
        </p>
      )}
      <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-3)" }}>
        <a href={candidate.abs_url} target="_blank" rel="noreferrer">打开摘要页</a>
        {fullTextUrl && (
          <a
            href={fullTextUrl}
            target="_blank"
            rel="noreferrer"
            data-testid={`candidate-${candidate.candidate_id}-pdf`}
          >
            打开全文链接（本轮读取范围见重点推荐）
          </a>
        )}
      </div>
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
  const candidates = search.candidates ?? [];
  const citationEdges = search.citation_edges ?? [];
  const conditionSources = search.condition_sources ?? [];

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
      {search.research_question && (
        <p data-testid="paper-search-question" style={{ margin: 0 }}>
          本轮理解的研究问题：<strong>{search.research_question}</strong>
        </p>
      )}
      {(search.selected_sources?.length ?? 0) > 0 && (
        <p data-testid="paper-search-sources" style={{ margin: 0 }}>
          检索范围：{(search.selected_sources ?? [])
            .map((source) => SOURCE_LABELS[source] ?? source)
            .join("、")}
        </p>
      )}
      <p data-testid="paper-search-conditions" style={{ margin: 0 }}>
        明确条件：{conditionSources.length > 0
          ? conditionSources.map((condition) =>
            `${formatCondition(condition)}（原话：“${condition.source_phrase}”）`
          ).join("；")
          : "未检测到额外的年份、来源、篇数或排除条件"}
      </p>

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

      <QueryRecordList
        records={queries}
        testId="paper-search-queries"
        sourceLabels={SOURCE_LABELS}
      />

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

      {candidates.length > 0 && (
        <details data-testid="paper-search-candidates">
          <summary style={{ minHeight: "var(--target-size)", cursor: "pointer", color: "var(--color-text-primary)" }}>
            完整候选（{candidates.length}）
          </summary>
          <ol
            style={{
              margin: "var(--space-2) 0 0",
              paddingLeft: "var(--space-5)",
              display: "flex",
              flexDirection: "column",
              gap: "var(--space-3)",
            }}
          >
            {candidates.map((candidate) => (
              <CandidateRow key={candidate.candidate_id} candidate={candidate} />
            ))}
          </ol>
        </details>
      )}

      <PaperCitationGraph edges={citationEdges} />

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
