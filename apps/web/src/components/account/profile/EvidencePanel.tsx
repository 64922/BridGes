"use client";

import type {
  AtomicProfileEvidenceSource,
  AtomicProfileFeedbackKind,
  AtomicProfileItemEvidenceProjection,
  AtomicProfileItemProjection,
} from "@/lib/api";

import {
  FEEDBACK_LABELS,
  FEEDBACK_OPTIONS,
  SCOPE_LABELS,
  formatUpdatedTime,
  recurrenceText,
  sourceStatusText,
  validityText,
} from "./profile-format";
import styles from "./AtomicProfileCenter.module.css";

export type EvidenceEntry =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: AtomicProfileItemEvidenceProjection };

export type FeedbackNotice = {
  itemId: string;
  text: string;
  ok: boolean;
};

type Props = {
  item: AtomicProfileItemProjection;
  entry: EvidenceEntry;
  feedbackBusy: string | null;
  feedbackNotice: FeedbackNotice | null;
  onRetry: () => void;
  onFeedback: (kind: AtomicProfileFeedbackKind) => void;
  onOpenSource: (source: AtomicProfileEvidenceSource) => void;
};

/**
 * 改进工单 20：一条信息的依据面板（按需展开）。
 *
 * 真实原话、来源时间与定位、适用范围、明确期限/状态逐项如实展示：来源
 * 已删除或不可读时不展示原话，绝不伪造引语；反馈只记录问题类型，不自动
 * 删除或修改仍正确的事实。
 */
export function EvidencePanel({
  item,
  entry,
  feedbackBusy,
  feedbackNotice,
  onRetry,
  onFeedback,
  onOpenSource,
}: Props) {
  if (entry.status === "loading") {
    return (
      <p className={styles.state} data-testid="atomic-evidence-loading">
        正在加载这条信息的依据…
      </p>
    );
  }
  if (entry.status === "error") {
    return (
      <div className={styles.evidenceError} role="alert">
        <span>{entry.message}</span>
        <button type="button" onClick={onRetry}>
          重试
        </button>
      </div>
    );
  }
  const data = entry.data;
  const sources = data.sources ?? [];
  const feedback = data.feedback ?? [];
  const hasReadableSource = sources.some(
    (source) => source.status === "available"
  );
  // 合同要求删除聊天原文时同步失效派生原话副本：没有可读来源就不展示原话，
  // 即使服务端仍带着保存时的记录（防止回归时把已删内容当成现行依据）。
  const quoteAvailable =
    Boolean(data.evidence_quote) &&
    data.evidence_quote_status === "recorded" &&
    hasReadableSource;
  const quoteInvalidated =
    data.evidence_quote_status === "source_unavailable" ||
    (Boolean(data.evidence_quote) && !quoteAvailable);
  return (
    <div className={styles.evidenceBody}>
      <div className={styles.evidenceSection}>
        <p className={styles.evidenceLabel}>原话依据</p>
        {quoteAvailable ? (
          <blockquote className={styles.quote}>{data.evidence_quote}</blockquote>
        ) : quoteInvalidated ? (
          <p className={styles.evidenceNote}>
            来源消息已删除或不可读，保存的原话不再展示。
          </p>
        ) : (
          <p className={styles.evidenceNote}>
            这条信息没有保存聊天原话（旧记录，或正文由你手动修改过）。
          </p>
        )}
      </div>

      <div className={styles.evidenceSection}>
        <p className={styles.evidenceLabel}>来源</p>
        {sources.length === 0 ? (
          <p className={styles.evidenceNote}>
            没有可定位的对话来源；这条信息可能由你在聊天中直接记住。
          </p>
        ) : (
          <ul className={styles.sourceList}>
            {sources.map((source) => (
              <li key={source.message_id} className={styles.sourceItem}>
                <div className={styles.sourceRow}>
                  <span>{sourceStatusText(source)}</span>
                  {source.status === "available" && source.conversation_id ? (
                    <button type="button" onClick={() => onOpenSource(source)}>
                      查看原文
                    </button>
                  ) : null}
                </div>
                {source.quote && source.quote !== data.evidence_quote ? (
                  <blockquote
                    className={styles.sourceQuote}
                    data-testid="atomic-source-quote"
                  >
                    {source.quote}
                  </blockquote>
                ) : null}
                {source.quote_status === "source_unavailable" ? (
                  <span
                    className={styles.sourceQuoteNote}
                    data-testid="atomic-source-quote-invalidated"
                  >
                    该来源保存的片段已不可核对。
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className={styles.evidenceSection}>
        <p className={styles.evidenceLabel}>范围与时效</p>
        <p className={styles.evidenceNote}>
          适用范围：{SCOPE_LABELS[data.fact_scope]}。
        </p>
        <p className={styles.evidenceNote}>有效期：{validityText(data)}</p>
        {data.recurrence ? (
          <p
            className={styles.evidenceNote}
            data-testid="atomic-evidence-recurrence"
          >
            固定安排：原文说「{data.recurrence.phrase}」：
            {recurrenceText(data.recurrence)}
          </p>
        ) : null}
      </div>

      <div className={styles.evidenceSection}>
        <p className={styles.evidenceLabel}>反馈这条信息</p>
        <div className={styles.feedbackActions}>
          {FEEDBACK_OPTIONS.map(({ kind, label }) => (
            <button
              key={kind}
              type="button"
              disabled={feedbackBusy === `${item.profile_item_id}:${kind}`}
              onClick={() => onFeedback(kind)}
            >
              {label}
            </button>
          ))}
        </div>
        <p className={styles.evidenceNote}>
          反馈只记录问题类型；信息是否删除或修改仍由你决定，系统不会自动删除。
        </p>
        {feedbackNotice?.itemId === item.profile_item_id ? (
          <p
            className={
              feedbackNotice.ok ? styles.feedbackNotice : styles.feedbackError
            }
            role={feedbackNotice.ok ? "status" : "alert"}
            data-testid="atomic-feedback-notice"
          >
            {feedbackNotice.text}
          </p>
        ) : null}
        {feedback.length > 0 ? (
          <ul className={styles.feedbackList}>
            {feedback.map((entryFeedback) => (
              <li key={entryFeedback.feedback_id}>
                已反馈「{FEEDBACK_LABELS[entryFeedback.kind]}」 ·{" "}
                {formatUpdatedTime(entryFeedback.created_at)}：
                {entryFeedback.message}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </div>
  );
}
