"use client";

import { Icon } from "@/components/design-system/Icon";
import type {
  GithubContextSource,
  GithubFeatureMatch,
  GithubImplementationCheck,
  GithubRateLimitState,
  GithubRecommendation,
  GithubRejectedRepository,
  GithubProjectsProjection,
} from "@/lib/api";

import { QueryRecordList } from "./QueryRecordList";

/** V2 Issue 16：GitHub 项目推荐状态的中文标题（每条助手消息内如实显示）。 */
const STATUS_TITLES: Record<string, string> = {
  clarification: "需要先确认一个问题",
  searching: "正在检索公开仓库",
  success: "GitHub 项目推荐",
  metadata_only: "只取得仓库元数据（未读到 README）",
  empty: "没有找到匹配的公开仓库",
  error: "GitHub 项目推荐未完成",
  stopped: "已停止 GitHub 项目推荐",
};

const COVERAGE_LABELS: Record<string, string> = {
  whole: "整体项目",
  component: "组件项目（只覆盖一部分）",
};

const EVIDENCE_LABELS: Record<string, string> = {
  metadata: "API 元数据",
  readme: "README 自述",
  implementation: "实际读取的实现文件",
};

const README_STATUS_LABELS: Record<string, string> = {
  read: "已读到 README",
  not_found: "仓库没有 README",
  too_large: "README 超出接口单文件上限，未读取",
  not_fetched: "本轮未取 README",
  error: "README 读取失败",
};

const CHECK_STATUS_LABELS: Record<string, string> = {
  confirmed: "确认存在",
  missing: "确认缺失",
  unread: "本轮未读到",
};

/** 查询记录的来源词汇表（来源词属于本模块自己的领域）。 */
const SOURCE_LABELS: Record<string, string> = {
  github_search: "GitHub 检索",
  github_repository: "GitHub 仓库读取",
};

function formatTime(value: string | null | undefined): string {
  if (!value) return "时间未知";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "时间未知" : date.toLocaleString("zh-CN");
}

/** 单条要点的匹配结论：命中就附证据等级与原文窗口，未命中就说未命中。 */
function FeatureMatchRow({ match }: { match: GithubFeatureMatch }) {
  const terms = match.matched_terms ?? [];
  return (
    <li data-testid={`github-feature-${match.feature}`}>
      <span style={{ fontWeight: 600 }}>{match.feature}</span>
      {"："}
      {match.matched ? (
        <>
          已覆盖
          {match.evidence_kind
            ? `（依据：${EVIDENCE_LABELS[match.evidence_kind] ?? match.evidence_kind}${
                terms.length > 0 ? `，命中原词：${terms.join("、")}` : ""
              }）`
            : ""}
        </>
      ) : (
        <span style={{ color: "var(--color-status-wait)" }}>未覆盖</span>
      )}
      <span style={{ color: "var(--color-text-tertiary)" }}> · {match.evidence}</span>
    </li>
  );
}

/** README 点名路径的存在性核对：三层结论都在这里说清楚。 */
function ImplementationCheckRow({ check }: { check: GithubImplementationCheck }) {
  return (
    <li
      data-testid={`github-implementation-${check.path}`}
      style={{ color: "var(--color-text-tertiary)" }}
    >
      {check.path} · {CHECK_STATUS_LABELS[check.status] ?? check.status} · {check.evidence}
    </li>
  );
}

/** 一个被推荐的仓库：链接、覆盖面、逐条功能匹配、借鉴角度、维护与许可证据。 */
function RecommendationRow({ item }: { item: GithubRecommendation }) {
  const license = item.license;
  const maintenance = item.maintenance;
  const filesRead = item.files_read ?? [];
  const checks = item.implementation_checks ?? [];
  const matches = item.feature_matches ?? [];
  const strengths = item.strengths ?? [];
  const limitations = item.limitations ?? [];
  const topics = item.topics ?? [];
  const kinds = item.evidence_kinds ?? [];

  return (
    <li
      data-testid={`github-recommendation-${item.full_name}`}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-1)",
        paddingBottom: "var(--space-3)",
        borderBottom: "1px solid var(--color-border)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
        <span
          style={{
            padding: "0 var(--space-1)",
            borderRadius: "var(--radius-sm)",
            backgroundColor: "var(--color-bg-secondary)",
            color: "var(--color-text-tertiary)",
            fontSize: "var(--text-xs)",
            fontWeight: 600,
          }}
        >
          {item.rank}
        </span>
        <a
          href={item.html_url}
          target="_blank"
          rel="noreferrer"
          style={{ fontWeight: 600 }}
          data-testid={`github-recommendation-${item.full_name}-link`}
        >
          {item.full_name}
        </a>
        <span
          data-testid={`github-recommendation-${item.full_name}-coverage`}
          style={{
            padding: "2px var(--space-2)",
            borderRadius: "var(--radius-full)",
            backgroundColor:
              item.coverage === "whole"
                ? "var(--color-status-success-bg)"
                : "var(--color-status-wait-bg)",
            color:
              item.coverage === "whole"
                ? "var(--color-status-success)"
                : "var(--color-status-wait)",
            fontSize: "var(--text-xs)",
            fontWeight: 600,
          }}
        >
          {COVERAGE_LABELS[item.coverage] ?? item.coverage}
        </span>
      </div>

      <span style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
        {item.coverage_note}
      </span>
      {item.description ? (
        <span style={{ fontSize: "var(--text-xs)" }}>项目介绍（API 元数据）：{item.description}</span>
      ) : null}
      {topics.length > 0 ? (
        <span style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
          话题：{topics.join("、")}
          {item.language ? ` · 主要语言：${item.language}` : ""}
        </span>
      ) : null}

      {matches.length > 0 && (
        <ul
          data-testid={`github-recommendation-${item.full_name}-features`}
          style={{
            margin: 0,
            paddingLeft: "var(--space-5)",
            listStyle: "disc",
            display: "flex",
            flexDirection: "column",
            gap: "var(--space-1)",
          }}
        >
          {matches.map((match) => (
            <FeatureMatchRow key={match.feature} match={match} />
          ))}
        </ul>
      )}

      <span data-testid={`github-recommendation-${item.full_name}-borrow`}>
        借鉴角度：{item.insight_zh || item.borrow_note}
      </span>
      <span style={{ fontSize: "var(--text-xs)" }}>
        维护与许可证据：许可{" "}
        <strong>{license.spdx_id || license.name || "未见"}</strong>
        {license.file_read ? "（已读取许可文件）" : license.detected ? "（元数据标注）" : ""} ·{" "}
        {maintenance.note}
      </span>
      <span style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
        证据等级：{kinds.map((kind) => EVIDENCE_LABELS[kind] ?? kind).join("、") || "无"} · README：
        {README_STATUS_LABELS[item.readme_status] ?? item.readme_status}
        {item.readme_url ? (
          <>
            {" · "}
            <a href={item.readme_url} target="_blank" rel="noreferrer">
              README 链接
            </a>
          </>
        ) : null}
      </span>
      {item.readme_excerpt ? (
        <blockquote
          data-testid={`github-recommendation-${item.full_name}-readme`}
          style={{
            margin: "var(--space-1) 0 0",
            paddingLeft: "var(--space-3)",
            borderLeft: "2px solid var(--color-border)",
            color: "var(--color-text-tertiary)",
            fontSize: "var(--text-xs)",
          }}
        >
          {item.readme_excerpt}
        </blockquote>
      ) : null}
      {filesRead.length > 0 && (
        <span style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
          已读实现文件／目录：{filesRead.map((entry) => entry.path).join("、")}
        </span>
      )}
      {checks.length > 0 && (
        <ul
          data-testid={`github-recommendation-${item.full_name}-checks`}
          style={{
            margin: 0,
            paddingLeft: "var(--space-5)",
            listStyle: "circle",
            display: "flex",
            flexDirection: "column",
            gap: "var(--space-1)",
            fontSize: "var(--text-xs)",
          }}
        >
          {checks.map((check, index) => (
            <ImplementationCheckRow key={`${check.path}-${index}`} check={check} />
          ))}
        </ul>
      )}
      <span style={{ fontSize: "var(--text-xs)" }}>选用理由：{item.reason_zh}</span>
      {strengths.length > 0 && (
        <span style={{ fontSize: "var(--text-xs)" }}>优点：{strengths.join("；")}</span>
      )}
      {limitations.length > 0 && (
        <span
          data-testid={`github-recommendation-${item.full_name}-limitations`}
          style={{ fontSize: "var(--text-xs)", color: "var(--color-status-wait)" }}
        >
          局限：{limitations.join("；")}
        </span>
      )}
    </li>
  );
}

/** 前文依据：指向前一条消息里的原词（可追溯到具体消息）。 */
function ContextSourceRow({ source }: { source: GithubContextSource }) {
  return (
    <span data-testid="github-context-source" style={{ fontSize: "var(--text-xs)" }}>
      前文依据：{source.label}的原始词「{source.phrase}」
      {source.message_id ? `（消息 ${source.message_id}）` : ""}
    </span>
  );
}

function RejectedRow({ item }: { item: GithubRejectedRepository }) {
  return (
    <li style={{ color: "var(--color-text-tertiary)" }}>
      <a href={item.url} target="_blank" rel="noreferrer">
        {item.full_name}
      </a>
      ：{item.reason}
    </li>
  );
}

function RateLimitNote({ rateLimit }: { rateLimit: GithubRateLimitState }) {
  if (!rateLimit.limited && !rateLimit.note) return null;
  // 只给「是否撞上 + 面向用户的说明」：剩余额度与重置时刻是检索内部日志
  // （interaction.md §4），不呈现。
  return (
    <span
      data-testid="github-rate-limit"
      style={{ fontSize: "var(--text-xs)", color: "var(--color-status-wait)" }}
    >
      上游额度：{rateLimit.note ?? "本轮触发上游限流。"}
    </span>
  );
}

/**
 * GitHub 项目推荐结果卡（V2 Issue 16）。
 *
 * 只渲染投影里的真实内容：实际查询词、每个仓库的直达链接、覆盖面说明与逐条
 * 功能匹配（含依据等级与命中原词）、借鉴角度、维护与许可证据、被剔除的候选与
 * 理由。README 只当作项目自述展示；未读取实现文件时卡片不出现任何内部架构
 * 断言，未见许可证时不出现「可自由复用」的说法。
 */
export function GithubProjectsCard({
  projects,
  streaming,
  onRetry,
}: {
  projects: GithubProjectsProjection | null;
  streaming: boolean;
  onRetry?: () => void;
}) {
  if (!projects) return null;
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
  const failed = projects.status === "error";
  const waiting = projects.status === "clarification";
  const degraded = projects.status === "metadata_only";
  const recommendations = projects.recommendations ?? [];
  const rejected = projects.rejected ?? [];
  const queries = projects.queries ?? [];
  const notes = projects.evidence_boundary ?? [];
  const features = projects.features ?? [];
  const techTerms = projects.tech_terms ?? [];
  const coversParts = projects.component_terms ?? [];

  return (
    <section
      data-testid={`github-projects-card-${projects.status}`}
      role={failed ? "alert" : "status"}
      aria-live="polite"
      aria-busy={streaming || projects.status === "searching"}
      style={{
        ...shellStyle,
        borderColor: failed ? "var(--color-status-error)" : "var(--color-border)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
        <Icon name={failed ? "alert" : waiting ? "info" : "githubRepo"} size={16} aria-hidden />
        <strong
          style={{ color: failed ? "var(--color-status-error)" : "var(--color-text-primary)" }}
        >
          {STATUS_TITLES[projects.status] ?? "GitHub 项目推荐"}
        </strong>
        {projects.status === "success" && (
          <span
            data-testid="github-projects-count"
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
            推荐 {recommendations.length} 个仓库
          </span>
        )}
        {degraded && (
          <span
            data-testid="github-projects-degraded"
            style={{
              marginLeft: "auto",
              padding: "2px var(--space-2)",
              borderRadius: "var(--radius-full)",
              backgroundColor: "var(--color-status-wait-bg)",
              color: "var(--color-status-wait)",
              fontSize: "var(--text-xs)",
              fontWeight: 600,
            }}
          >
            仅元数据
          </span>
        )}
      </div>

      {/* AC1：核心场景与要点都是用户原词，逐字展示。 */}
      <p style={{ margin: 0 }} data-testid="github-projects-idea">
        核心场景：<strong>{projects.scenario || "（本轮没有给出场景）"}</strong>
        {features.length > 0 ? (
          <>
            {" · 必要功能："}
            <strong>{features.join("、")}</strong>
          </>
        ) : null}
      </p>
      {techTerms.length > 0 ? (
        <span style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
          可选技术词（只用于排序参考）：{techTerms.join("、")}
        </span>
      ) : null}
      {coversParts.length > 0 ? (
        <span style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
          你本轮指名的组件：{coversParts.join("、")}
        </span>
      ) : null}
      {projects.context_source ? <ContextSourceRow source={projects.context_source} /> : null}

      {waiting && projects.pending && (
        <div
          data-testid="github-projects-clarification"
          style={{
            padding: "var(--space-2) var(--space-3)",
            border: "1px solid var(--color-border-strong)",
            borderRadius: "var(--radius-md)",
            backgroundColor: "var(--color-surface)",
          }}
        >
          <p style={{ margin: 0, fontWeight: 600, color: "var(--color-text-primary)" }}>
            {projects.pending.question}
          </p>
          <p style={{ margin: "var(--space-1) 0 0", fontSize: "var(--text-xs)" }}>
            直接在下方回复即可；回复会带着「GitHub 项目推荐」模块发出（输入框上方的模块标签可随时移除）。
          </p>
        </div>
      )}

      {failed && (
        <p style={{ margin: 0, color: "var(--color-status-error)" }}>
          {projects.error_message ?? "本轮 GitHub 项目推荐未完成。"}
          {projects.error_code ? `（错误码：${projects.error_code}）` : ""}
        </p>
      )}

      {/* 失败时的原因已在上面按错误码展示，这里不重复。 */}
      {!failed && projects.empty_reason && (
        <p style={{ margin: 0 }}>{projects.empty_reason}</p>
      )}

      {recommendations.length > 0 && (
        <ul
          data-testid="github-recommendations"
          style={{
            margin: 0,
            paddingLeft: "var(--space-5)",
            listStyle: "disc",
            display: "flex",
            flexDirection: "column",
            gap: "var(--space-3)",
          }}
        >
          {recommendations.map((item) => (
            <RecommendationRow key={item.full_name} item={item} />
          ))}
        </ul>
      )}

      {rejected.length > 0 && (
        <div>
          <p style={{ margin: 0, marginBottom: "var(--space-1)", fontWeight: 600 }}>
            已检索但未纳入推荐的候选
          </p>
          <ul
            data-testid="github-rejected"
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
            {rejected.map((item, index) => (
              <RejectedRow key={`${item.full_name}-${index}`} item={item} />
            ))}
          </ul>
        </div>
      )}

      <RateLimitNote rateLimit={projects.rate_limit} />

      <QueryRecordList
        records={queries}
        testId="github-queries"
        sourceLabels={SOURCE_LABELS}
      />

      {notes.length > 0 && (
        <div>
          <p style={{ margin: 0, fontWeight: 600 }}>证据边界</p>
          <ul
            data-testid="github-evidence-boundary"
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
            {notes.map((note, index) => (
              <li key={`note-${index}`}>{note}</li>
            ))}
          </ul>
        </div>
      )}

      <p style={{ margin: 0, fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
        本轮完成于 {formatTime(projects.completed_at)}
      </p>

      {projects.retryable && onRetry && (
        <button
          type="button"
          data-testid="github-projects-retry"
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
          <Icon name="retry" size={14} aria-hidden /> 重试 GitHub 项目推荐
        </button>
      )}
    </section>
  );
}
