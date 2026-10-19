"use client";

import { Icon } from "@/components/design-system/Icon";
import type {
  TiebaPostProjection,
  TiebaResearchProjection,
  TiebaSummaryCitation,
} from "@/lib/api";

import { TiebaEvidenceDetails } from "./TiebaEvidenceDetails";
import { citationLocation } from "./TiebaEvidenceRows";

type OpenCitation = (
  citation: TiebaSummaryCitation,
  post: TiebaPostProjection | null,
  trigger: HTMLButtonElement,
  research: TiebaResearchProjection
) => void;

export function TiebaCitationLinks({
  citations,
  onOpenCitation,
}: {
  citations: TiebaSummaryCitation[];
  onOpenCitation?: (citation: TiebaSummaryCitation, trigger: HTMLButtonElement) => void;
}) {
  if (citations.length === 0) return null;
  return (
    <div
      data-testid="tieba-citations"
      style={{
        display: "flex",
        flexWrap: "wrap",
        alignItems: "center",
        gap: "var(--space-2)",
        fontSize: "var(--text-xs)",
      }}
    >
      <span style={{ color: "var(--color-text-tertiary)" }}>依据：</span>
      {citations.map((citation) => (
        <button
          key={citation.citation_id}
          type="button"
          data-testid={`tieba-citation-${citation.citation_id}`}
          aria-label={`查看引用 ${citation.citation_id}`}
          onClick={(event) => onOpenCitation?.(citation, event.currentTarget)}
          style={{
            minHeight: "var(--target-size)",
            padding: "0 var(--space-2)",
            border: "1px solid var(--color-border-strong)",
            borderRadius: "var(--radius-full)",
            backgroundColor: "transparent",
            color: "var(--color-accent-secondary)",
            font: "inherit",
            fontSize: "var(--text-xs)",
            fontWeight: 600,
            cursor: "pointer",
          }}
        >
          {citation.citation_id} · {citationLocation(citation)}
        </button>
      ))}
    </div>
  );
}

/**
 * 贴吧回答的连续正文。
 *
 * 正文权威来源是本轮已提交的贴吧投影：有核验通过的连贯总结时只展示该总结
 * （服务端消息正文是同一份内容的纯文本载体，不再重复渲染）；没有连贯总结
 * 时退回带引用的逐条观点或确定性摘录，绝不因存在投影而隐藏整条回答。
 * 帖子依据、读取范围和官方核验由 TiebaEvidenceDetails 单独呈现。
 */
export function TiebaAnswer({
  research,
  streaming,
  fallbackText,
  onOpenCitation,
  onRetry,
  showRetry,
}: {
  research: TiebaResearchProjection;
  streaming?: boolean;
  fallbackText?: string;
  onOpenCitation?: OpenCitation;
  onRetry?: () => void;
  showRetry?: boolean;
}) {
  const posts = research.confirmed_posts ?? [];
  const citations = research.summary_citations ?? [];
  const claims = research.summary_claims ?? [];
  const sections = research.sections ?? [];
  const citationsById = new Map(
    citations.map((citation) => [citation.citation_id, citation])
  );
  const postByUrl = new Map(posts.map((post) => [post.url, post]));
  const waiting =
    research.status === "clarification" || research.status === "waiting_auth";
  const failed = research.status === "error";
  const stopped = research.status === "stopped";
  const recovery = research.recovery;
  const hasSummary = Boolean(research.summary);
  const hasClaims = claims.length > 0;
  const fallback = fallbackText?.trim() ?? "";

  const openCitation = (citation: TiebaSummaryCitation, trigger: HTMLButtonElement) =>
    onOpenCitation?.(citation, postByUrl.get(citation.url) ?? null, trigger, research);

  return (
    <div
      data-testid="tieba-answer"
      aria-busy={streaming || research.status === "searching" || undefined}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-2)",
        color: "var(--color-text-primary)",
        overflowWrap: "break-word",
      }}
    >
      {waiting && research.pending && (
        <div
          data-testid="tieba-pending"
          role="status"
          aria-live="polite"
          style={{
            padding: "var(--space-2) var(--space-3)",
            border: "1px solid var(--color-border-strong)",
            borderRadius: "var(--radius-md)",
            backgroundColor: "var(--color-bg-secondary)",
          }}
        >
          <p style={{ margin: 0, fontWeight: 600 }}>
            {research.status === "waiting_auth"
              ? "等待贴吧登录或安全验证"
              : "需要先确认一个问题"}
          </p>
          <p style={{ margin: "var(--space-1) 0 0" }}>{research.pending.question}</p>
          <p
            style={{
              margin: "var(--space-2) 0 0",
              fontSize: "var(--text-xs)",
              color: "var(--color-text-secondary)",
            }}
          >
            {research.status === "waiting_auth"
              ? recovery?.scope_note
                ? `${recovery.scope_note}不会读取系统浏览器凭据，也不会绕过验证。`
                : "请在当前账户专用的贴吧会话中完成登录或安全验证，然后在聊天里回复“继续”；系统会沿用原话题。不会读取系统浏览器凭据，也不会绕过验证。"
              : "直接在下方回复即可；回复会带着「贴吧信息搜集」模块发出（输入框上方的模块标签可随时移除）。"}
          </p>
        </div>
      )}

      {research.status === "searching" && (
        <p
          data-testid="tieba-searching"
          role="status"
          aria-live="polite"
          style={{ margin: 0, color: "var(--color-text-secondary)" }}
        >
          正在检索贴吧帖子，完成后再呈现总结。
        </p>
      )}

      {failed && (
        <p
          role="alert"
          data-testid="tieba-error"
          style={{ margin: 0, color: "var(--color-status-error)" }}
        >
          {research.error_message ?? "本轮贴吧检索未完成。"}
          {research.error_code ? `（错误码：${research.error_code}）` : ""}
        </p>
      )}

      {stopped && (
        <p
          data-testid="tieba-stopped"
          role="status"
          aria-live="polite"
          style={{ margin: 0, color: "var(--color-text-secondary)" }}
        >
          {recovery?.scope_note ?? "本轮已停止贴吧检索，已读范围与引用保留；不会自动继续。"}
        </p>
      )}

      {recovery &&
        recovery.action !== "none" &&
        recovery.action !== "continue_auth" &&
        recovery.scope_note && (
          <p
            data-testid="tieba-recovery"
            role="status"
            style={{
              margin: 0,
              color: "var(--color-text-secondary)",
              fontSize: "var(--text-xs)",
            }}
          >
            {recovery.scope_note}
          </p>
        )}

      {hasSummary && (
        <>
          <p
            data-testid="tieba-answer-kicker"
            style={{
              margin: 0,
              color: "var(--color-text-tertiary)",
              fontSize: "var(--text-xs)",
              fontWeight: 600,
            }}
          >
            吧友讨论总结（只依据本轮读到的帖子原文）
          </p>
          <p
            data-testid="tieba-summary"
            style={{ margin: 0, lineHeight: "var(--line-height-relaxed)" }}
          >
            {research.summary}
          </p>
          <TiebaCitationLinks
            citations={citations}
            onOpenCitation={openCitation}
          />
        </>
      )}

      {!hasSummary && hasClaims && (
        <>
          <p
            data-testid="tieba-answer-kicker"
            style={{
              margin: 0,
              color: "var(--color-text-tertiary)",
              fontSize: "var(--text-xs)",
              fontWeight: 600,
            }}
          >
            吧友讨论要点（逐条对应本轮已读原文）
          </p>
          <ul
            data-testid="tieba-summary-claims"
            style={{
              margin: 0,
              paddingLeft: "var(--space-5)",
              display: "flex",
              flexDirection: "column",
              gap: "var(--space-1)",
              lineHeight: "var(--line-height-relaxed)",
            }}
          >
            {claims.map((claim, index) => (
              <li key={`claim-${index}`}>
                {claim.text}{" "}
                {(claim.citation_ids ?? []).map((citationId) => {
                  const citation = citationsById.get(citationId);
                  if (!citation) return null;
                  return (
                    <button
                      key={citationId}
                      type="button"
                      data-testid={`tieba-claim-citation-${citationId}`}
                      aria-label={`查看引用 ${citationId}`}
                      onClick={(event) => openCitation(citation, event.currentTarget)}
                      style={{
                        padding: 0,
                        border: "none",
                        background: "none",
                        color: "var(--color-accent-secondary)",
                        font: "inherit",
                        fontWeight: 600,
                        textDecoration: "underline",
                        cursor: "pointer",
                      }}
                    >
                      [{citationId}]
                    </button>
                  );
                })}
              </li>
            ))}
          </ul>
        </>
      )}

      {!hasSummary && !hasClaims && sections.length > 0 && (
        <div data-testid="tieba-fallback-sections">
          <p
            style={{
              margin: 0,
              color: "var(--color-text-tertiary)",
              fontSize: "var(--text-xs)",
              fontWeight: 600,
            }}
          >
            本轮未生成模型总结，以下为按规则摘录的已读原文
          </p>
          <ul style={{ margin: "var(--space-1) 0 0", paddingLeft: "var(--space-5)" }}>
            {sections.map((section, index) => (
              <li key={`fallback-section-${index}`}>{section}</li>
            ))}
          </ul>
        </div>
      )}

      {!hasSummary &&
        !hasClaims &&
        sections.length === 0 &&
        fallback.length > 0 &&
        research.status === "success" && (
          <div
            data-testid="tieba-fallback-body"
            style={{ whiteSpace: "pre-wrap", lineHeight: "var(--line-height-relaxed)" }}
          >
            {fallback}
          </div>
        )}

      {research.summary_note && (
        <p
          data-testid="tieba-summary-note"
          style={{
            margin: 0,
            color: "var(--color-status-wait)",
            fontSize: "var(--text-xs)",
          }}
        >
          {research.summary_note}
        </p>
      )}

      <TiebaEvidenceDetails
        research={research}
        showSections={hasSummary || hasClaims}
      />

      {showRetry && onRetry && research.retryable && (
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
          <button
            type="button"
            data-testid="tieba-legacy-retry"
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
            <Icon name="retry" size={14} aria-hidden />{" "}
            {recovery?.action === "retry_incomplete_stage"
              ? "重试未完成阶段"
              : "重跑本轮"}
          </button>
          <span style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
            {recovery?.scope_note ??
              "会重跑整轮对话（包含贴吧检索），不会只补缺失回复。"}
          </span>
        </div>
      )}
    </div>
  );
}

/**
 * 复合回答中的贴吧依据：正文保持服务端已提交的跨模块综合回答，这里只补
 * 真实引用入口与读取范围，不再重复一份贴吧总结。
 */
export function TiebaCompositeEvidence({
  research,
  onOpenCitation,
}: {
  research: TiebaResearchProjection;
  onOpenCitation?: OpenCitation;
}) {
  const posts = research.confirmed_posts ?? [];
  const postByUrl = new Map(posts.map((post) => [post.url, post]));
  const citations = research.summary_citations ?? [];
  return (
    <div
      data-testid="tieba-composite-evidence"
      style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}
    >
      <TiebaCitationLinks
        citations={citations}
        onOpenCitation={(citation, trigger) =>
          onOpenCitation?.(
            citation,
            postByUrl.get(citation.url) ?? null,
            trigger,
            research
          )
        }
      />
      <TiebaEvidenceDetails research={research} />
    </div>
  );
}
