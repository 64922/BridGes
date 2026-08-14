"use client";

import { useEffect, useRef, useState } from "react";

import { Icon } from "@/components/design-system/Icon";
import {
  collectRiskTypes,
  trackArticleProjectionEvent,
} from "@/lib/article-projection-telemetry";
import type {
  ArticleEvidenceItem,
  HumanizerResultProjection,
} from "@/lib/api";
import { copyTextToClipboard } from "@/lib/clipboard";

/**
 * 文章人味化结果卡（Issue 28 + Issue 08 正文优先交付界面）。
 *
 * 有版本化文章投影（``result.article``）时渲染正文优先界面：首屏只呈现
 * 已交付的最终正文（正文本身在消息主体），审计信息（来源保真、表达审稿、
 * 定向修订、证据风险、待确认）按需展开，每分区带计数与状态；硬门失败
 * 时正文区域明确「未交付」，不把违规候选显示为最终稿。旧结果（无
 * ``article``）按 legacy 投影展示并标注「旧版结果」，不伪造新来源硬门
 * 或新审稿通过状态。
 */
//: Issue 08 Observability：前端能力开关（localStorage，灰度/回滚用）。
//: 置 "1" 时新投影也按 legacy 展示；回滚恢复旧展示但保留新结构化记录。
const ARTICLE_PROJECTION_LEGACY_FLAG = "bridges:article-projection:legacy";

function forceLegacyDisplay(): boolean {
  try {
    return window.localStorage.getItem(ARTICLE_PROJECTION_LEGACY_FLAG) === "1";
  } catch {
    return false;
  }
}

export function HumanizerResultCard({
  result,
  onRetry,
}: {
  result: HumanizerResultProjection;
  /** Issue 28：失败（停止交付）后从原任务重试（输入保留）。 */
  onRetry?: () => void;
}) {
  // Issue 08：legacy 读取遥测只在挂载后上报（避免渲染期副作用）。
  const legacyTracked = useRef(false);
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
  if (result.article != null && !forceLegacyDisplay()) {
    return (
      <ArticleResultCard
        result={result}
        article={result.article}
        onRetry={onRetry}
      />
    );
  }
  return <LegacyHumanizerResultCard result={result} onRetry={onRetry} />;
}

/** 审计分区折叠区（Issue 08：正文优先，审计按需展开）。 */
function ArticleResultCard({
  result,
  article,
  onRetry,
}: {
  result: HumanizerResultProjection;
  article: NonNullable<HumanizerResultProjection["article"]>;
  onRetry?: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [copyStatus, setCopyStatus] = useState<"idle" | "success" | "error">(
    "idle"
  );
  const expandTracked = useRef(false);

  const failed = article.delivery_status === "failed";
  // Issue 06 第七轮：修订未完成仅交付首稿 → 部分交付（成功终态，附说明）。
  const partial = article.delivery_status === "partial";
  const hasConfirmations = (article.confirmations?.length ?? 0) > 0;
  const hasEvidence = (article.evidence?.length ?? 0) > 0;
  const styleWarnings = article.style_review?.warning_count ?? 0;
  const revisionRemaining = article.revision?.remaining_count ?? 0;
  const hasWarnings =
    styleWarnings > 0 || revisionRemaining > 0 || hasEvidence || hasConfirmations;

  const statusLabel = failed
    ? "未交付"
    : partial
      ? "已交付首稿（未完成修订）"
      : hasConfirmations
        ? "已交付（含待确认项）"
        : hasWarnings
          ? "已交付（含警告）"
          : "已交付";
  const statusColor = failed
    ? "var(--color-status-error)"
    : partial
      ? "var(--color-status-warning)"
      : hasConfirmations
        ? "var(--color-status-warning)"
        : hasWarnings
          ? "var(--color-status-warning)"
          : "var(--color-status-success)";

  const telemetryBase = {
    projection_version: article.projection_version ?? null,
    delivery_status: article.delivery_status,
    risk_types: collectRiskTypes({
      fidelityBlocking: article.fidelity?.blocking_count ?? 0,
      needsConfirmation: (article.confirmations?.length ?? 0) > 0 ? 1 : 0,
      styleWarnings,
      evidenceKinds: (article.evidence ?? []).map((item) => item.kind),
      revisionTriggered: article.revision?.triggered ?? false,
    }),
    legacy: false,
  };

  function toggleExpanded(): void {
    const next = !expanded;
    setExpanded(next);
    if (next && !expandTracked.current) {
      expandTracked.current = true;
      trackArticleProjectionEvent(telemetryBase, "expand");
    }
  }

  async function copyText(): Promise<void> {
    const ok = await copyTextToClipboard(article.final_text ?? "");
    setCopyStatus(ok ? "success" : "error");
    if (ok) {
      trackArticleProjectionEvent(telemetryBase, "copy");
    }
    window.setTimeout(() => setCopyStatus("idle"), 2000);
  }

  const retryButton = result.status === "error" && onRetry && (
    <button
      type="button"
      data-testid="humanizer-result-retry"
      onClick={onRetry}
      style={{
        marginLeft: "var(--space-2)",
        padding: "var(--space-1) var(--space-3)",
        border: "1px solid var(--color-border-strong)",
        borderRadius: "var(--radius-md)",
        background: "transparent",
        color: "var(--color-text-primary)",
        cursor: "pointer",
        fontSize: "var(--text-sm)",
        whiteSpace: "nowrap",
      }}
    >
      重试（原任务输入已保留）
    </button>
  );

  return (
    <section data-testid="humanizer-result-card" style={{ marginTop: "var(--space-2)", borderRadius: "var(--radius-md)", border: "1px solid var(--color-border)", backgroundColor: "var(--color-surface)", overflow: "hidden" }}>
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", padding: "var(--space-2) var(--space-3)" }}>
        <button
          type="button"
          onClick={toggleExpanded}
          aria-expanded={expanded}
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-2)",
            flex: 1,
            border: "none",
            background: "transparent",
            color: "var(--color-text-primary)",
            cursor: "pointer",
            fontSize: "var(--text-sm)",
            textAlign: "left",
            padding: 0,
          }}
        >
          <span style={{ color: statusColor, display: "inline-flex" }}>
            <Icon name="humanize" size={16} aria-hidden />
          </span>
          <strong>{statusLabel}</strong>
          <AuditSummary article={article} />
          <span style={{ flex: 1 }} />
          <span style={{ color: "var(--color-text-secondary)", whiteSpace: "nowrap" }}>SKILL {result.skill_version}</span>
          <span style={{ display: "inline-flex", transition: "transform var(--motion-duration-fast) var(--motion-easing)", transform: expanded ? "rotate(180deg)" : "none" }}>
            <Icon name="chevronDown" size={16} aria-hidden />
          </span>
        </button>
        {/* Issue 08：失败（未交付）时没有最终正文可复制，不显示复制入口 */}
        {!failed && (
          <button
            type="button"
            data-testid="humanizer-copy-text"
            onClick={() => void copyText()}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "var(--space-1)",
              border: "1px solid var(--color-border-strong)",
              borderRadius: "var(--radius-md)",
              background: "transparent",
              color: "var(--color-text-primary)",
              cursor: "pointer",
              fontSize: "var(--text-sm)",
              padding: "var(--space-1) var(--space-3)",
              whiteSpace: "nowrap",
            }}
            aria-label="复制最终正文"
          >
            <Icon name="copy" size={14} aria-hidden />
            {copyStatus === "success" ? "已复制" : copyStatus === "error" ? "复制失败" : "复制正文"}
          </button>
        )}
        {retryButton}
      </div>

      {expanded && (
        <div>
          {failed && (
            <div
              role="alert"
              data-testid="humanizer-error-detail"
              style={{
                display: "flex",
                alignItems: "flex-start",
                gap: "var(--space-2)",
                padding: "var(--space-3)",
                borderTop: "1px solid var(--color-border)",
                backgroundColor: "var(--color-status-error-bg)",
                color: "var(--color-status-error)",
                fontSize: "var(--text-sm)",
                overflowWrap: "break-word",
              }}
            >
              <Icon name="alert" size={16} aria-hidden />
              <span>{result.error_message ?? "任务未完成，请重试。"}</span>
            </div>
          )}

          {/* Issue 06 第七轮：部分交付（已交付首稿，未完成修订）的明确标注 */}
          {partial && article.delivery_note && (
            <div
              role="status"
              data-testid="humanizer-partial-note"
              style={{
                display: "flex",
                alignItems: "flex-start",
                gap: "var(--space-2)",
                padding: "var(--space-3)",
                borderTop: "1px solid var(--color-border)",
                backgroundColor: "var(--color-status-wait-bg)",
                color: "var(--color-status-wait)",
                fontSize: "var(--text-sm)",
                overflowWrap: "break-word",
              }}
            >
              <Icon name="alert" size={16} aria-hidden />
              <span>{article.delivery_note}</span>
            </div>
          )}

          {article.material_state === "insufficient" && (
            <AuditSection title="材料不足" data-testid="humanizer-material-state" tone="warning">
              {/* Issue 08 AC5：只显示一个最高价值问题，不堆叠通用建议 */}
              <p style={sectionBodyStyle}>
                {article.one_question ??
                  result.error_message ??
                  "材料不足，请补充后重试。"}
              </p>
            </AuditSection>
          )}

          {article.fidelity && (article.fidelity.items?.length ?? 0) > 0 && (
            <AuditSection
              title={`来源保真（${article.fidelity.blocking_count} 项未通过${article.fidelity.needs_confirmation_count > 0 ? `，${article.fidelity.needs_confirmation_count} 项待确认` : ""}）`}
              data-testid="humanizer-fidelity"
              tone={article.fidelity.blocking_count > 0 ? "error" : "warning"}
            >
              <ul role="list" style={listStyle}>
                {article.fidelity.items?.map((item, index) => (
                  <li key={`${item.code}-${index}`} style={{ color: item.severity === "blocking" ? "var(--color-status-error)" : "var(--color-status-warning)" }}>
                    <strong>{item.category}</strong>：{item.note}
                  </li>
                ))}
              </ul>
            </AuditSection>
          )}

          {article.style_review && (article.style_review.items?.length ?? 0) > 0 && (
            <AuditSection
              title={`表达审稿与修改建议（${article.style_review.warning_count} 项警告、${article.style_review.suggestion_count} 项建议）`}
              data-testid="humanizer-style-review"
              tone="warning"
            >
              <ul role="list" style={listStyle}>
                {article.style_review.items?.map((finding, index) => (
                  <li key={`${finding.category}-${index}`} style={{ display: "grid", gap: "var(--space-1)" }}>
                    <div>
                      <strong>{finding.category}</strong>
                      <span style={{ color: "var(--color-text-secondary)" }}>：{finding.evidence}</span>
                    </div>
                    <div style={{ color: "var(--color-text-secondary)" }}>
                      建议：{finding.suggestion}
                    </div>
                  </li>
                ))}
              </ul>
            </AuditSection>
          )}

          {article.revision && (
            <AuditSection
              title="定向修订"
              data-testid="humanizer-revision"
              tone="warning"
            >
              <p style={sectionBodyStyle}>
                {article.revision.triggered && !article.revision.skipped_reason
                  ? `已执行一次定向修订${article.revision.trigger_label ? `（触发原因：${article.revision.trigger_label}）` : ""}：解决 ${article.revision.resolved_count} 个问题，仍剩 ${article.revision.remaining_count} 个风险。`
                  : article.revision.skipped_reason
                    ? `未完成定向修订（${article.revision.skipped_reason}），正文按首稿交付。`
                    : "首稿已通过检查，未触发修订。"}
              </p>
            </AuditSection>
          )}

          {(article.evidence?.length ?? 0) > 0 && (
            <AuditSection
              title={`证据风险（${article.evidence?.length ?? 0} 项）`}
              data-testid="humanizer-evidence"
              tone="warning"
            >
              <ul role="list" style={listStyle}>
                {article.evidence?.map((item, index) => (
                  <EvidenceItemLi key={`${item.code}-${index}`} item={item} />
                ))}
              </ul>
            </AuditSection>
          )}

          {(article.confirmations?.length ?? 0) > 0 && (
            <AuditSection
              title={`待确认（${article.confirmations?.length ?? 0} 项）`}
              data-testid="humanizer-confirmations"
              tone="warning"
            >
              <ul role="list" style={listStyle}>
                {article.confirmations?.map((item, index) => (
                  <li key={`${item.code}-${index}`}>
                    <strong>{item.label}</strong>：{item.detail}
                  </li>
                ))}
              </ul>
            </AuditSection>
          )}
        </div>
      )}
    </section>
  );
}

function EvidenceItemLi({ item }: { item: ArticleEvidenceItem }) {
  if (item.kind === "change") {
    return (
      <li style={{ display: "grid", gap: "var(--space-1)" }}>
        <div>
          <span style={{ color: "var(--color-text-secondary)" }}>原文：</span>
          <span style={{ textDecoration: "line-through", color: "var(--color-status-error)" }}>{item.original_span ?? "—"}</span>
        </div>
        <div>
          <span style={{ color: "var(--color-text-secondary)" }}>改后：</span>
          {item.revised_span ?? "—"}
          {item.needs_user_confirmation && (
            <span style={{ color: "var(--color-status-warning)" }}>（需确认）</span>
          )}
        </div>
        <div style={{ color: "var(--color-text-secondary)" }}>
          {item.category}：{item.reason}
          {item.source_label ? `（来源：${item.source_label}）` : ""}
        </div>
      </li>
    );
  }
  return (
    <li style={{ display: "grid", gap: "var(--space-1)" }}>
      <div>
        <strong>{item.category}</strong>
        {item.needs_user_confirmation && (
          <span style={{ color: "var(--color-status-warning)" }}>（需确认）</span>
        )}
      </div>
      {item.original_span && (
        <div style={{ color: "var(--color-text-secondary)" }}>「{item.original_span}」</div>
      )}
      <div style={{ color: "var(--color-text-secondary)" }}>{item.reason}</div>
    </li>
  );
}

/** 头部审计摘要 pill（折叠时也可见：风险存在时突出显示）。 */
function AuditSummary({ article }: { article: NonNullable<HumanizerResultProjection["article"]> }) {
  const pills: Array<{ label: string; color: string }> = [];
  if (article.fidelity) {
    if (article.fidelity.blocking_count > 0) {
      pills.push({ label: `保真 ${article.fidelity.blocking_count} 项未通过`, color: "var(--color-status-error)" });
    } else if (article.fidelity.needs_confirmation_count > 0) {
      pills.push({ label: `保真 ${article.fidelity.needs_confirmation_count} 项待确认`, color: "var(--color-status-warning)" });
    } else if (article.fidelity.items?.length === 0) {
      pills.push({ label: "保真通过", color: "var(--color-status-success)" });
    }
  }
  if (article.style_review && article.style_review.warning_count > 0) {
    pills.push({ label: `审稿 ${article.style_review.warning_count} 项`, color: "var(--color-status-warning)" });
  }
  if (article.revision?.triggered) {
    pills.push({ label: "已定向修订", color: "var(--color-status-warning)" });
  }
  if ((article.evidence?.length ?? 0) > 0) {
    pills.push({ label: `证据风险 ${article.evidence?.length ?? 0} 项`, color: "var(--color-status-warning)" });
  }
  if ((article.confirmations?.length ?? 0) > 0) {
    pills.push({ label: `待确认 ${article.confirmations?.length ?? 0} 项`, color: "var(--color-status-warning)" });
  }
  if (pills.length === 0) {
    return null;
  }
  return (
    <span role="status" style={{ display: "inline-flex", flexWrap: "wrap", gap: "var(--space-1)", marginLeft: "var(--space-2)" }}>
      {pills.map((pill) => (
        <span
          key={pill.label}
          style={{
            padding: "0 var(--space-2)",
            borderRadius: 999,
            border: `1px solid ${pill.color}`,
            color: pill.color,
            fontSize: "var(--text-xs)",
            whiteSpace: "nowrap",
          }}
        >
          {pill.label}
        </span>
      ))}
    </span>
  );
}

function AuditSection({
  title,
  tone,
  children,
  ...props
}: {
  title: string;
  tone: "warning" | "error";
  children: React.ReactNode;
  "data-testid"?: string;
}) {
  return (
    <div
      {...props}
      style={{
        padding: "var(--space-2) var(--space-3)",
        borderTop: "1px solid var(--color-border)",
      }}
    >
      <p
        style={{
          margin: 0,
          fontSize: "var(--text-xs)",
          color: tone === "error" ? "var(--color-status-error)" : "var(--color-status-warning)",
          fontWeight: 600,
        }}
      >
        {title}
      </p>
      {children}
    </div>
  );
}

const sectionBodyStyle: React.CSSProperties = {
  margin: "var(--space-1) 0 0",
  fontSize: "var(--text-sm)",
  color: "var(--color-text-secondary)",
  overflowWrap: "break-word",
};

const listStyle: React.CSSProperties = {
  margin: "var(--space-1) 0 0",
  paddingLeft: "1.25rem",
  display: "grid",
  gap: "var(--space-2)",
  fontSize: "var(--text-sm)",
};

/** 旧版结果卡（Issue 08 AC 10：旧字段以明确 legacy 投影展示）。 */
function LegacyHumanizerResultCard({
  result,
  onRetry,
}: {
  result: HumanizerResultProjection;
  /** Issue 28：失败（停止交付）后从原任务重试（输入保留）。 */
  onRetry?: () => void;
}) {
  const [expanded, setExpanded] = useState(false);

  // Issue 07：软门未完全满足时正文照常交付——状态标签明确「已交付」，
  // 未满足项以警告列表呈现，重新生成是可选操作而非唯一出口。
  const softWarned = result.output?.quality_status === "warn";
  const statusLabel =
    result.status === "done"
      ? "人味化完成"
      : result.status === "needs_human"
        ? softWarned
          ? "已交付（含未完全满足项）"
          : "需人工确认"
        : "任务未完成";
  const statusColor =
    result.status === "done"
      ? "var(--color-status-success)"
      : result.status === "needs_human"
        ? "var(--color-status-warning)"
        : "var(--color-status-error)";

  const cardStyle: React.CSSProperties = {
    marginTop: "var(--space-2)",
    borderRadius: "var(--radius-md)",
    border: "1px solid var(--color-border)",
    backgroundColor: "var(--color-surface)",
    overflow: "hidden",
  };
  const headerStyle: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: "var(--space-2)",
    width: "100%",
    padding: "var(--space-2) var(--space-3)",
    border: "none",
    background: "transparent",
    color: "var(--color-text-primary)",
    cursor: "pointer",
    fontSize: "var(--text-sm)",
    textAlign: "left",
  };
  const sectionStyle: React.CSSProperties = {
    padding: "var(--space-2) var(--space-3)",
    borderTop: "1px solid var(--color-border)",
  };
  const labelStyle: React.CSSProperties = {
    margin: 0,
    fontSize: "var(--text-xs)",
    color: "var(--color-text-secondary)",
    fontWeight: 600,
  };

  const retryButton = result.status === "error" && onRetry && (
    <button
      type="button"
      data-testid="humanizer-result-retry"
      onClick={onRetry}
      style={{
        marginLeft: "var(--space-2)",
        padding: "var(--space-1) var(--space-3)",
        border: "1px solid var(--color-border-strong)",
        borderRadius: "var(--radius-md)",
        background: "transparent",
        color: "var(--color-text-primary)",
        cursor: "pointer",
        fontSize: "var(--text-sm)",
      }}
    >
      重试（原任务输入已保留）
    </button>
  );

  return (
    <section data-testid="humanizer-result-card" style={cardStyle}>
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          style={{ ...headerStyle, flex: 1 }}
          aria-expanded={expanded}
        >
          <span style={{ color: statusColor, display: "inline-flex" }}>
            <Icon name="humanize" size={16} aria-hidden />
          </span>
          <strong>{statusLabel}</strong>
          <span
            data-testid="humanizer-legacy-badge"
            style={{
              padding: "0 var(--space-2)",
              borderRadius: 999,
              border: "1px solid var(--color-border-strong)",
              color: "var(--color-text-secondary)",
              fontSize: "var(--text-xs)",
              whiteSpace: "nowrap",
            }}
          >
            旧版结果
          </span>
          <span style={{ flex: 1 }} />
          <span style={{ color: "var(--color-text-secondary)" }}>SKILL {result.skill_version}</span>
          <span style={{ display: "inline-flex", transition: "transform var(--motion-duration-fast) var(--motion-easing)", transform: expanded ? "rotate(180deg)" : "none" }}>
            <Icon name="chevronDown" size={16} aria-hidden />
          </span>
        </button>
        {retryButton}
      </div>

      {expanded && (
        <div>
          {/* 软门未完全满足项（Issue 07：正文照常交付并附具体警告） */}
          {(result.quality_warnings?.length ?? 0) > 0 && (
            <div style={sectionStyle}>
              <p style={labelStyle}>未完全满足项（正文已照常交付）</p>
              <ul
                role="list"
                data-testid="humanizer-quality-warnings"
                style={{
                  margin: "var(--space-1) 0 0",
                  paddingLeft: "1.25rem",
                  display: "grid",
                  gap: "var(--space-1)",
                  color: "var(--color-status-warning)",
                  fontSize: "var(--text-sm)",
                }}
              >
                {result.quality_warnings!.map((warning, index) => (
                  <li key={`${warning}-${index}`}>{warning}</li>
                ))}
              </ul>
              {result.repair_attempts != null && result.repair_attempts > 0 && (
                <p
                  style={{
                    margin: "var(--space-1) 0 0",
                    color: "var(--color-text-secondary)",
                    fontSize: "var(--text-xs)",
                  }}
                >
                  已按体裁规则定向修正 {result.repair_attempts} 次后交付（受总预算约束，至多一次）。
                </p>
              )}
            </div>
          )}

          {/* 事实锁比较摘要 */}
          {result.fact_lock_check && (
            <div style={sectionStyle}>
              <p style={labelStyle}>事实锁比较</p>
              {(result.fact_lock_check.blocking_conflicts?.length ?? 0) > 0 ? (
                <ul role="list" data-testid="humanizer-fact-lock-conflicts" style={{ margin: "var(--space-1) 0 0", paddingLeft: "1.25rem", display: "grid", gap: "var(--space-1)" }}>
                  {result.fact_lock_check.blocking_conflicts!.map((conflict, index) => (
                    <li key={`${conflict}-${index}`} style={{ color: "var(--color-status-error)" }}>{conflict}</li>
                  ))}
                </ul>
              ) : (
                <p style={{ margin: "var(--space-1) 0 0", color: "var(--color-text-secondary)", fontSize: "var(--text-sm)" }}>
                  数值、单位、对象关系、限定条件、公式、引用与结论强度前后一致
                  {(result.fact_lock_check.needs_human?.length ?? 0) > 0 ? "；部分事项标注需人工确认" : ""}。
                </p>
              )}
              {(result.fact_lock_check.needs_human?.length ?? 0) > 0 && (
                <ul role="list" data-testid="humanizer-needs-human" style={{ margin: "var(--space-1) 0 0", paddingLeft: "1.25rem", display: "grid", gap: "var(--space-1)", color: "var(--color-status-warning)" }}>
                  {result.fact_lock_check.needs_human!.map((note, index) => (
                    <li key={`${note}-${index}`}>{note}</li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {/* 逐项修改细节 + 每项理由 */}
          {result.output && (result.output.edits?.length ?? 0) > 0 && (
            <div style={sectionStyle}>
              <p style={labelStyle}>逐项修改细节（每项附理由）</p>
              <ul role="list" data-testid="humanizer-edits" style={{ margin: "var(--space-1) 0 0", padding: 0, listStyle: "none", display: "grid", gap: "var(--space-2)" }}>
                {result.output.edits!.map((edit) => (
                  <li key={edit.edit_id} style={{ fontSize: "var(--text-sm)", display: "grid", gap: "var(--space-1)" }}>
                    <div>
                      <span style={{ color: "var(--color-text-secondary)" }}>原文：</span>
                      {edit.original || "—"}
                    </div>
                    <div>
                      <span style={{ color: "var(--color-text-secondary)" }}>改后：</span>
                      {edit.revised || "—"}
                    </div>
                    <div style={{ color: "var(--color-text-secondary)" }}>
                      理由：{edit.reason}
                      {edit.genre_rule ? `（${edit.genre_rule}）` : ""}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* 事实核查结果 */}
          {result.output && (result.output.fact_check?.length ?? 0) > 0 && (
            <div style={sectionStyle}>
              <p style={labelStyle}>事实核查结果</p>
              <ul role="list" data-testid="humanizer-fact-check" style={{ margin: "var(--space-1) 0 0", padding: 0, listStyle: "none", display: "grid", gap: "var(--space-1)", fontSize: "var(--text-sm)" }}>
                {result.output.fact_check!.map((item, index) => (
                  <li key={`${item.item}-${index}`}>
                    {item.item} —— {item.result}
                    <span style={{ color: "var(--color-text-secondary)" }}>（{item.evidence}）</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* 尚未解决的问题 */}
          {result.output && (
            <div style={sectionStyle}>
              <p style={labelStyle}>尚未解决的问题</p>
              {(result.output.open_questions?.length ?? 0) > 0 ? (
                <ul role="list" data-testid="humanizer-open-questions" style={{ margin: "var(--space-1) 0 0", paddingLeft: "1.25rem", display: "grid", gap: "var(--space-1)", fontSize: "var(--text-sm)" }}>
                  {result.output.open_questions!.map((question, index) => (
                    <li key={`${question}-${index}`}>{question}</li>
                  ))}
                </ul>
              ) : (
                <p style={{ margin: "var(--space-1) 0 0", color: "var(--color-text-secondary)", fontSize: "var(--text-sm)" }}>无。</p>
              )}
            </div>
          )}

          {/* 引用保持 */}
          {(result.references?.length ?? 0) > 0 && (
            <div style={sectionStyle}>
              <p style={labelStyle}>引用与来源</p>
              <ul role="list" data-testid="humanizer-references" style={{ margin: "var(--space-1) 0 0", padding: 0, listStyle: "none", display: "grid", gap: "var(--space-1)", fontSize: "var(--text-sm)" }}>
                {result.references!.map((reference) => (
                  <li key={reference.reference_id}>
                    {reference.preserved ? "✓" : "⚠"} {reference.label}
                    <span style={{ color: "var(--color-text-secondary)" }}>
                      （{reference.source_type}｜{reference.detail}）
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* 体裁复核摘要 */}
          {(result.genre_check?.length ?? 0) > 0 && (
            <div style={sectionStyle}>
              <p style={labelStyle}>体裁规则复核</p>
              <ul role="list" data-testid="humanizer-genre-check" style={{ margin: "var(--space-1) 0 0", paddingLeft: "1.25rem", display: "grid", gap: "var(--space-1)", fontSize: "var(--text-sm)" }}>
                {result.genre_check!.map((finding, index) => (
                  <li key={`${finding}-${index}`}>{finding}</li>
                ))}
              </ul>
            </div>
          )}

          {/* 失败说明 */}
          {result.status === "error" && (
            <div style={sectionStyle} data-testid="humanizer-error-detail">
              <p style={{ margin: 0, color: "var(--color-status-error)", fontSize: "var(--text-sm)" }}>
                {result.error_message ?? "任务未完成，请重试。"}
              </p>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
