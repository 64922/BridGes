"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";

import type {
  HumanizerArticleProjection,
  HumanizerResultProjection,
} from "@/lib/api";
import {
  collectRiskTypes,
  trackArticleProjectionEvent,
} from "@/lib/article-projection-telemetry";
import { copyTextToClipboard } from "@/lib/clipboard";
import { deliveredHumanizerText } from "@/lib/historical-results";

const DELIVERY_LABEL: Record<string, string> = {
  delivered: "当时已交付",
  partial: "当时交付首稿，修订未完成",
  failed: "当时未交付正文",
};

const LEGACY_STATUS_LABEL: Record<string, string> = {
  done: "旧版结果 · 已完成",
  needs_human: "旧版结果 · 需要人工确认",
  error: "旧版结果 · 失败",
};

function AuditSection({
  title,
  testId,
  children,
  onExpand,
}: {
  title: string;
  testId: string;
  children: ReactNode;
  onExpand?: () => void;
}) {
  return (
    <details
      data-testid={testId}
      onToggle={(event) => {
        if (event.currentTarget.open) onExpand?.();
      }}
      style={{
        borderTop: "1px solid var(--color-border-subtle)",
        padding: "var(--space-2) 0",
      }}
    >
      <summary
        style={{
          cursor: "pointer",
          fontWeight: 600,
          color: "var(--color-text-secondary)",
          fontSize: "var(--text-xs)",
        }}
      >
        {title}
      </summary>
      <div style={{ marginTop: "var(--space-2)", fontSize: "var(--text-sm)" }}>
        {children}
      </div>
    </details>
  );
}

function EmptyAudit({ text }: { text: string }) {
  return (
    <p style={{ margin: 0, color: "var(--color-text-tertiary)", fontSize: "var(--text-xs)" }}>
      {text}
    </p>
  );
}

function ArticleAudit({
  article,
  onExpand,
}: {
  article: HumanizerArticleProjection;
  onExpand: () => void;
}) {
  const fidelityItems = article.fidelity?.items ?? [];
  const styleItems = article.style_review?.items ?? [];
  const excisionItems = article.excision?.items ?? [];
  const evidenceItems = article.evidence ?? [];
  const confirmations = article.confirmations ?? [];

  return (
    <div>
      {article.delivery_note ? (
        <p
          data-testid="humanizer-partial-note"
          style={{
            margin: "var(--space-2) 0 0",
            color: "var(--color-status-warning)",
            fontSize: "var(--text-sm)",
          }}
        >
          {article.delivery_note}
        </p>
      ) : null}

      {article.material_state === "insufficient" ? (
        <AuditSection
          title="材料状态"
          testId="humanizer-material-state"
          onExpand={onExpand}
        >
          <p style={{ margin: 0 }}>当时材料不足，结果没有补齐缺失事实。</p>
          {article.one_question ? (
            <p style={{ margin: "var(--space-1) 0 0" }}>
              当时需要确认：{article.one_question}
            </p>
          ) : null}
        </AuditSection>
      ) : null}

      {(fidelityItems.length > 0 || (article.fidelity?.blocking_count ?? 0) > 0) ? (
        <AuditSection
          title={`来源保真（${article.fidelity?.blocking_count ?? 0} 处阻断）`}
          testId="humanizer-fidelity"
          onExpand={onExpand}
        >
          <ul style={{ margin: 0, paddingLeft: "var(--space-5)" }}>
            {fidelityItems.map((item, index) => (
              <li key={`${item.code}-${index}`}>
                {item.note}
                {item.severity === "blocking" ? "（阻断交付）" : ""}
              </li>
            ))}
          </ul>
        </AuditSection>
      ) : null}

      {styleItems.length > 0 ? (
        <AuditSection
          title={`表达审稿（${article.style_review?.warning_count ?? 0} 处警告）`}
          testId="humanizer-style-review"
          onExpand={onExpand}
        >
          <ul style={{ margin: 0, paddingLeft: "var(--space-5)" }}>
            {styleItems.map((item, index) => (
              <li key={`${item.category}-${index}`}>
                {item.evidence}
                {item.suggestion ? `：${item.suggestion}` : ""}
              </li>
            ))}
          </ul>
        </AuditSection>
      ) : null}

      {article.revision ? (
        <AuditSection
          title={`定向修订（${article.revision.remaining_count} 处未完成）`}
          testId="humanizer-revision"
          onExpand={onExpand}
        >
          <p style={{ margin: 0 }}>
            {article.revision.triggered ? "当时触发过修订。" : "当时没有触发定向修订。"}
            {article.revision.trigger_label
              ? ` 触发类别：${article.revision.trigger_label}`
              : ""}
            {article.revision.skipped_reason
              ? ` 未执行原因：${article.revision.skipped_reason}`
              : ""}
          </p>
        </AuditSection>
      ) : null}

      {article.excision ? (
        <AuditSection
          title={`机械剔除（移除 ${article.excision.removed_count} 处）`}
          testId="humanizer-excision"
          onExpand={onExpand}
        >
          {excisionItems.length > 0 ? (
            <ul style={{ margin: 0, paddingLeft: "var(--space-5)" }}>
              {excisionItems.map((item, index) => (
                <li key={`${item.code}-${index}`}>
                  {item.note}
                </li>
              ))}
            </ul>
          ) : (
            <EmptyAudit text="当时记录了剔除数量，但没有保留逐条内容。" />
          )}
        </AuditSection>
      ) : null}

      {evidenceItems.length > 0 ? (
        <AuditSection
          title={`证据与风险（${evidenceItems.length} 条）`}
          testId="humanizer-evidence"
          onExpand={onExpand}
        >
          <ul style={{ margin: 0, paddingLeft: "var(--space-5)" }}>
            {evidenceItems.map((item, index) => (
              <li key={`${item.code}-${index}`}>
                {item.kind} · {item.category}
                {item.reason ? `：${item.reason}` : ""}
              </li>
            ))}
          </ul>
        </AuditSection>
      ) : null}

      {confirmations.length > 0 ? (
        <AuditSection
          title={`待确认（${confirmations.length} 条）`}
          testId="humanizer-confirmations"
          onExpand={onExpand}
        >
          <ul style={{ margin: 0, paddingLeft: "var(--space-5)" }}>
            {confirmations.map((item, index) => (
              <li key={`${item.code}-${index}`}>
                {item.label}：{item.detail}
              </li>
            ))}
          </ul>
        </AuditSection>
      ) : null}
    </div>
  );
}

function LegacyAudit({ result }: { result: HumanizerResultProjection }) {
  const output = result.output;
  const edits = output?.edits ?? [];
  const factCheck = output?.fact_check ?? [];
  const warnings = result.quality_warnings ?? [];
  const openQuestions = output?.open_questions ?? [];
  const references = result.references ?? [];
  const genreCheck = result.genre_check ?? [];

  return (
    <div>
      <span
        data-testid="humanizer-legacy-badge"
        style={{
          display: "inline-block",
          border: "1px solid var(--color-border-strong)",
          borderRadius: 999,
          padding: "0 var(--space-2)",
          color: "var(--color-text-tertiary)",
          fontSize: "var(--text-xs)",
        }}
      >
        旧版结果
      </span>

      {edits.length > 0 ? (
        <AuditSection title={`当时改动（${edits.length} 处）`} testId="humanizer-edits">
          <ul style={{ margin: 0, paddingLeft: "var(--space-5)" }}>
            {edits.map((item, index) => (
              <li key={`${item.revised}-${index}`}>
                {item.revised}
                {item.reason ? `：${item.reason}` : ""}
              </li>
            ))}
          </ul>
        </AuditSection>
      ) : null}

      {factCheck.length > 0 ? (
        <AuditSection title="事实核对" testId="humanizer-fact-check">
          <ul style={{ margin: 0, paddingLeft: "var(--space-5)" }}>
            {factCheck.map((item, index) => (
              <li key={`${item.item}-${index}`}>
                {item.item}：{item.result}
                {item.evidence ? `（${item.evidence}）` : ""}
              </li>
            ))}
          </ul>
        </AuditSection>
      ) : null}

      {warnings.length > 0 ? (
        <AuditSection title={`质量警告（${warnings.length} 条）`} testId="humanizer-quality-warnings">
          <ul style={{ margin: 0, paddingLeft: "var(--space-5)" }}>
            {warnings.map((item, index) => (
              <li key={`${item}-${index}`}>{item}</li>
            ))}
          </ul>
        </AuditSection>
      ) : null}

      {openQuestions.length > 0 ? (
        <AuditSection title="当时待确认" testId="humanizer-open-questions">
          <ul style={{ margin: 0, paddingLeft: "var(--space-5)" }}>
            {openQuestions.map((item, index) => (
              <li key={`${item}-${index}`}>{item}</li>
            ))}
          </ul>
        </AuditSection>
      ) : null}

      {references.length > 0 ? (
        <AuditSection title="当时引用" testId="humanizer-references">
          <ul style={{ margin: 0, paddingLeft: "var(--space-5)" }}>
            {references.map((item, index) => (
              <li key={`${item.reference_id}-${index}`}>
                {item.label}
                {item.detail ? `：${item.detail}` : ""}
              </li>
            ))}
          </ul>
        </AuditSection>
      ) : null}

      {genreCheck.length > 0 ? (
        <AuditSection title="文体检查" testId="humanizer-genre-check">
          <ul style={{ margin: 0, paddingLeft: "var(--space-5)" }}>
            {genreCheck.map((item, index) => (
              <li key={`${item}-${index}`}>{item}</li>
            ))}
          </ul>
        </AuditSection>
      ) : null}
    </div>
  );
}

/**
 * 旧表达结果的历史只读入口。
 *
 * 已交付正文由消息正文承担，这里只保留交付说明与按需展开的审计记录；
 * 失败/未交付时不重复候选稿，也不提供重新生成入口。
 */
export function HistoricalExpression({ result }: { result: HumanizerResultProjection }) {
  const [copyStatus, setCopyStatus] = useState<"idle" | "success" | "error">("idle");
  const legacyTracked = useRef(false);
  const expandTracked = useRef(false);
  const deliveredText = deliveredHumanizerText(result);
  const article = result.article;

  useEffect(() => {
    if (result.article == null && !legacyTracked.current) {
      legacyTracked.current = true;
      trackArticleProjectionEvent(
        {
          projection_version: null,
          delivery_status: result.status,
          risk_types: [],
          legacy: true,
        },
        "legacy_read"
      );
    }
  }, [result]);

  const telemetryBase = article
    ? {
        projection_version: article.projection_version ?? null,
        delivery_status: article.delivery_status,
        risk_types: collectRiskTypes({
          fidelityBlocking: article.fidelity?.blocking_count ?? 0,
          needsConfirmation: article.confirmations?.length ?? 0,
          styleWarnings: article.style_review?.warning_count ?? 0,
          evidenceKinds: (article.evidence ?? []).map((item) => item.kind),
          revisionTriggered: article.revision?.triggered ?? false,
        }),
        legacy: false,
      }
    : null;

  function trackExpand(): void {
    if (!telemetryBase || expandTracked.current) return;
    expandTracked.current = true;
    trackArticleProjectionEvent(telemetryBase, "expand");
  }

  async function copyText(): Promise<void> {
    if (!deliveredText) return;
    const ok = await copyTextToClipboard(deliveredText);
    setCopyStatus(ok ? "success" : "error");
    if (ok && telemetryBase) {
      trackArticleProjectionEvent(telemetryBase, "copy");
    }
    window.setTimeout(() => setCopyStatus("idle"), 2000);
  }

  return (
    <section
      data-testid="humanizer-result-card"
      style={{
        marginTop: "var(--space-3)",
        borderTop: "1px solid var(--color-border-subtle)",
        paddingTop: "var(--space-3)",
      }}
    >
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: "var(--space-2)",
          alignItems: "baseline",
          justifyContent: "space-between",
        }}
      >
        <h3 style={{ margin: 0, fontSize: "var(--text-sm)", fontWeight: 600 }}>
          文章人味化
          <span
            style={{
              marginLeft: "var(--space-2)",
              color: "var(--color-text-tertiary)",
              fontSize: "var(--text-xs)",
              fontWeight: 400,
            }}
          >
            历史结果 · 只读
          </span>
        </h3>
        <button
          type="button"
          data-testid="humanizer-copy-text"
          disabled={!deliveredText}
          onClick={copyText}
          style={{
            border: "1px solid var(--color-border-strong)",
            borderRadius: "var(--radius-md)",
            background: "transparent",
            color: deliveredText
              ? "var(--color-text-secondary)"
              : "var(--color-text-tertiary)",
            padding: "var(--space-1) var(--space-3)",
            cursor: deliveredText ? "pointer" : "not-allowed",
            opacity: deliveredText ? 1 : 0.5,
          }}
        >
          {copyStatus === "success" ? "已复制最终正文" : "复制最终正文"}
        </button>
      </div>

      <p
        role="status"
        data-testid="humanizer-delivery-status"
        data-delivery-status={article?.delivery_status ?? result.status}
        style={{
          margin: "var(--space-2) 0 0",
          color:
            result.article?.delivery_status === "failed"
              ? "var(--color-status-error)"
              : "var(--color-text-secondary)",
          fontSize: "var(--text-sm)",
        }}
      >
        {article
          ? DELIVERY_LABEL[article.delivery_status]
          : LEGACY_STATUS_LABEL[result.status] ?? "旧版结果 · 状态未核实"}
        {deliveredText
          ? "；正文保留在消息正文中。"
          : "；没有可恢复的已交付正文。"}
      </p>

      {article?.delivery_status === "failed" && result.error_message ? (
        <p
          data-testid="humanizer-error-detail"
          style={{
            margin: "var(--space-1) 0 0",
            color: "var(--color-status-error)",
            fontSize: "var(--text-xs)",
          }}
        >
          {result.error_message}
        </p>
      ) : null}

      {copyStatus === "error" ? (
        <p role="alert" style={{ margin: "var(--space-1) 0 0", color: "var(--color-status-error)" }}>
          复制失败，请检查浏览器权限。
        </p>
      ) : null}

      {article ? (
        <ArticleAudit article={article} onExpand={trackExpand} />
      ) : (
        <LegacyAudit result={result} />
      )}

      <p
        style={{
          margin: "var(--space-2) 0 0",
          color: "var(--color-text-tertiary)",
          fontSize: "var(--text-xs)",
        }}
      >
        该能力已退役。以上内容来自当时保存的结果，页面不会重新执行或自动恢复；正文与审计记录均未重新核对。
      </p>
    </section>
  );
}
