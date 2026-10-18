"use client";

import type { ReactNode } from "react";
import type {
  PaperCandidateProjection,
  PaperConditionSource,
  PaperRecommendation,
  PaperRequirementEvidence,
  PaperSearchProjection,
} from "@/lib/api";

import { PaperCitationGraph } from "./PaperCitationGraph";
import { QueryRecordList } from "./QueryRecordList";
import styles from "./PaperDetailsPanel.module.css";

const SOURCE_LABELS: Record<string, string> = {
  arxiv: "arXiv",
  crossref: "Crossref",
  openalex: "OpenAlex",
};

const ROLE_LABELS: Record<string, string> = {
  survey: "综述",
  tutorial: "教程",
  foundation: "奠基工作",
  recent: "较新研究",
  related: "相关研究",
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

export function paperKey(paper: PaperRecommendation): string {
  return paper.candidate_id || paper.arxiv_id || paper.abs_url || String(paper.order);
}

export function formatYear(year: number | null | undefined): string {
  return year == null ? "年份未知" : `${year} 年`;
}

export function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] ?? source;
}

export function roleLabel(role: string): string {
  return ROLE_LABELS[role] ?? role;
}

export function relevanceLabel(relevance: string): string {
  return RELEVANCE_LABELS[relevance] ?? "相关性待核对";
}

export function readScopeLabel(scope: string): string {
  return READ_SCOPE_LABELS[scope] ?? "本轮读取范围未确认";
}

function formatCondition(condition: PaperConditionSource): string {
  let value = condition.value;
  if (condition.kind === "year") {
    const [from, to] = value.split(":");
    value = from && to ? `${from}–${to} 年` : from ? `${from} 年起` : `${to} 年及以前`;
    return `年份 ${value}`;
  }
  if (condition.kind === "count") return `篇数 ${value} 篇`;
  if (condition.kind === "source") return `来源 ${sourceLabel(value)}`;
  if (condition.kind === "paper") return `指定论文 ${value}`;
  if (condition.kind === "type" && value === "exclude_survey") return "排除综述";
  return `${condition.kind} ${value}`;
}

function formatDiscoveryPath(path: string): string {
  if (path.startsWith("citation:")) {
    return `OpenAlex 引用扩展（种子 ${path.slice("citation:".length)}）`;
  }
  const separator = path.indexOf(":");
  const source = separator < 0 ? path : path.slice(0, separator);
  const sourceId = separator < 0 ? "" : path.slice(separator + 1);
  const label = sourceLabel(source);
  return sourceId ? `${label} 检索（${sourceId}）` : `${label} 检索`;
}

export function safeExternalUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  return /^https?:\/\//i.test(value) ? value : null;
}

function ExternalLink({
  href,
  children,
  testId,
}: {
  href: string | null | undefined;
  children: ReactNode;
  testId?: string;
}) {
  const safe = safeExternalUrl(href);
  if (!safe) {
    return <span className={styles.muted}>链接不可用</span>;
  }
  return (
    <a
      className={styles.link}
      href={safe}
      target="_blank"
      rel="noopener noreferrer"
      data-testid={testId}
    >
      {children}
    </a>
  );
}

function EvidenceList({
  evidence,
  testId,
}: {
  evidence: PaperRequirementEvidence[];
  testId: string;
}) {
  if (evidence.length === 0) return null;
  return (
    <ul className={styles.compactList} data-testid={testId}>
      {evidence.map((item, index) => (
        <li key={`${item.requirement}-${item.source}-${index}`}>
          <strong>{item.requirement}</strong> · {item.source}：「{item.quote}」
        </li>
      ))}
    </ul>
  );
}

function CandidateSummary({ candidate }: { candidate: PaperCandidateProjection }) {
  const sources = candidate.sources?.length ? candidate.sources : [candidate.primary_source];
  const fullTextUrl = candidate.pdf_url || candidate.full_text_url;
  const discoveryPaths = candidate.discovery_paths ?? [];
  return (
    <li className={styles.candidateRow}>
      <strong>{candidate.title}</strong>
      <p className={styles.meta}>
        {formatYear(candidate.published_year)} · {relevanceLabel(candidate.relevance)}
        {candidate.research_group ? ` · ${candidate.research_group}` : ""} ·{" "}
        {sources.map(sourceLabel).join("、")}
      </p>
      <EvidenceList
        evidence={candidate.match_evidence ?? []}
        testId={`candidate-${candidate.candidate_id}-evidence`}
      />
      {candidate.abstract && (
        <details className={styles.abstractDetails}>
          <summary>摘要</summary>
          <p>{candidate.abstract}</p>
        </details>
      )}
      {discoveryPaths.length > 0 && (
        <p className={styles.meta} data-testid={`candidate-${candidate.candidate_id}-paths`}>
          发现路径：{discoveryPaths.map(formatDiscoveryPath).join("；")}
        </p>
      )}
      <div className={styles.linkRow}>
        <ExternalLink href={candidate.abs_url}>打开摘要页</ExternalLink>
        {fullTextUrl && (
          <ExternalLink
            href={fullTextUrl}
            testId={`candidate-${candidate.candidate_id}-fulltext`}
          >
            打开全文链接
          </ExternalLink>
        )}
      </div>
    </li>
  );
}

export function PaperSearchOverview({
  search,
  onSelectPaper,
}: {
  search: PaperSearchProjection;
  onSelectPaper: (paperId: string) => void;
}) {
  const papers = search.papers ?? [];
  const candidates = search.candidates ?? [];
  const conditions = search.condition_sources ?? [];
  const notes = search.evidence_notes ?? [];
  const sources = search.selected_sources ?? [];

  return (
    <div className={styles.overview} data-testid="paper-search-overview">
      <section className={styles.section}>
        <h3 className={styles.sectionTitle}>本轮检索</h3>
        {search.research_question && (
          <p className={styles.sectionText}>
            研究问题：<strong>{search.research_question}</strong>
          </p>
        )}
        <p className={styles.sectionText}>
          主题：{search.normalized_term || search.original_phrase || "未标注"} · 实际查询：
          {search.final_query || "未执行"}
        </p>
        {search.context_label && (
          <p className={styles.sectionText}>语境：{search.context_label}</p>
        )}
        {(search.expansions?.length ?? 0) > 0 && (
          <p className={styles.sectionText}>扩展词：{search.expansions?.join("、")}</p>
        )}
        <p className={styles.sectionText}>
          实际调用来源：{sources.length > 0 ? sources.map(sourceLabel).join("、") : "未实际调用来源"}
        </p>
        <div data-testid="paper-search-conditions">
          {conditions.length > 0 ? (
            <>
              <p className={styles.sectionText}>来自你原话的筛选条件：</p>
              <ul className={styles.compactList}>
                {conditions.map((condition, index) => (
                  <li key={`${condition.kind}-${condition.value}-${index}`}>
                    {formatCondition(condition)}（原话：“{condition.source_phrase}”）
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p className={styles.sectionText}>本轮没有检测到额外的年份、来源、篇数或排除条件。</p>
          )}
        </div>
        <p className={styles.note}>
          主题解析、语境与扩展词来自请求理解，可能包含推断；上面列出的硬条件只来自你的原话。
        </p>
      </section>

      {papers.length > 0 && (
        <section className={styles.section} data-testid="paper-search-recommendations">
          <h3 className={styles.sectionTitle}>单篇资料</h3>
          <p className={styles.note}>
            正文已按阅读顺序推荐；这里只按需展开单篇依据、摘要与读取范围。
          </p>
          <ul className={styles.paperIndex}>
            {papers.map((paper) => (
              <li key={paperKey(paper)}>
                <button
                  type="button"
                  className={styles.paperIndexButton}
                  data-testid={`paper-panel-select-${paper.order}`}
                  aria-label={`查看论文详情：${paper.title}`}
                  onClick={() => onSelectPaper(paperKey(paper))}
                >
                  <span className={styles.paperIndexTitle}>
                    {paper.order}. {paper.title}
                  </span>
                  <span className={styles.paperIndexMeta}>
                    {formatYear(paper.published_year)} · {roleLabel(paper.role)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {candidates.length > 0 && (
        <details className={styles.candidateDetails} data-testid="paper-search-candidates">
          <summary>完整候选（{candidates.length}）</summary>
          <ul className={styles.candidateList}>
            {candidates.map((candidate) => (
              <CandidateSummary key={candidate.candidate_id} candidate={candidate} />
            ))}
          </ul>
        </details>
      )}

      <section className={styles.section}>
        <QueryRecordList
          records={search.queries ?? []}
          testId="paper-search-queries"
          sourceLabels={SOURCE_LABELS}
        />
      </section>

      <PaperCitationGraph edges={search.citation_edges ?? []} />

      {notes.length > 0 && (
        <section className={styles.section} data-testid="paper-search-notes">
          <h3 className={styles.sectionTitle}>证据边界</h3>
          <ul className={styles.compactList}>
            {notes.map((note, index) => (
              <li key={`${note}-${index}`}>{note}</li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

export function PaperDetailView({
  paper,
  search,
  onBack,
}: {
  paper: PaperRecommendation;
  search: PaperSearchProjection;
  onBack: () => void;
}) {
  const sources = paper.sources?.length ? paper.sources : [paper.source];
  const authors = paper.authors ?? [];
  const unverified = paper.unverified ?? [];
  const fullTextUrl = paper.pdf_url || paper.full_text_url;
  return (
    <div className={styles.detail} data-testid="paper-detail">
      <button
        type="button"
        className={styles.backButton}
        data-testid="paper-detail-back"
        onClick={onBack}
      >
        返回论文资料列表
      </button>

      <p className={styles.detailMeta}>
        {formatYear(paper.published_year)} · {roleLabel(paper.role)} ·{" "}
        {sources.map(sourceLabel).join("、")}
        {paper.primary_category ? ` · ${paper.primary_category}` : ""}
      </p>
      {paper.venue && <p className={styles.detailMeta}>发表 venue：{paper.venue}</p>}
      {authors.length > 0 && (
        <p className={styles.detailMeta}>
          作者：{authors.join("、")}
        </p>
      )}
      {paper.arxiv_id && <p className={styles.detailMeta}>arXiv：{paper.arxiv_id}</p>}
      {paper.doi && <p className={styles.detailMeta}>DOI：{paper.doi}</p>}
      {paper.cited_by_count != null && (
        <p className={styles.detailMeta}>引用记录：{paper.cited_by_count}</p>
      )}

      <p className={styles.readScopeBlock} data-testid="paper-detail-read-scope">
        {readScopeLabel(paper.read_scope)}
      </p>
      {fullTextUrl && paper.full_text_available ? (
        <div className={styles.linkRow}>
          <ExternalLink href={paper.abs_url} testId="paper-detail-abs">
            打开摘要页
          </ExternalLink>
          <ExternalLink href={fullTextUrl} testId="paper-detail-fulltext">
            打开全文链接
          </ExternalLink>
        </div>
      ) : (
        <div className={styles.linkRow}>
          <ExternalLink href={paper.abs_url} testId="paper-detail-abs">
            打开摘要页
          </ExternalLink>
          <span className={styles.muted} data-testid="paper-detail-no-fulltext">
            来源未返回或未核实全文链接；本轮无法读取全文。
          </span>
        </div>
      )}

      <section className={styles.section}>
        <h3 className={styles.sectionTitle}>推荐理由</h3>
        <p className={styles.sectionText}>{paper.reason_zh}</p>
        {paper.match_basis && (
          <p className={styles.sectionText}>相关性说明：{paper.match_basis}</p>
        )}
        <EvidenceList
          evidence={paper.match_evidence ?? []}
          testId={`paper-detail-${paper.order}-match-evidence`}
        />
      </section>

      {paper.summary_zh && (
        <section className={styles.section} data-testid="paper-detail-summary">
          <h3 className={styles.sectionTitle}>中文概述</h3>
          <p className={styles.sectionText}>{paper.summary_zh}</p>
          {paper.summary_evidence && (
            <p className={styles.note}>
              概述依据：{paper.summary_evidence.source}「{paper.summary_evidence.quote}」
            </p>
          )}
        </section>
      )}

      {(paper.read_evidence?.length ?? 0) > 0 && (
        <section className={styles.section}>
          <h3 className={styles.sectionTitle}>正文读取证据</h3>
          <EvidenceList
            evidence={paper.read_evidence ?? []}
            testId={`paper-detail-${paper.order}-read-evidence`}
          />
        </section>
      )}

      {unverified.length > 0 && (
        <section className={styles.section} data-testid="paper-detail-unverified">
          <h3 className={styles.sectionTitle}>未核实项</h3>
          <ul className={styles.compactList}>
            {unverified.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </section>
      )}

      {paper.source_abstract && (
        <details className={styles.abstractDetails} data-testid="paper-detail-abstract">
          <summary>来源返回的原始摘要</summary>
          <p>{paper.source_abstract}</p>
        </details>
      )}

      {search.research_question && (
        <p className={styles.note}>本轮研究问题：{search.research_question}</p>
      )}
    </div>
  );
}
