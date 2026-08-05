"use client";

import { useState } from "react";

import { Icon } from "@/components/design-system/Icon";
import {
  submitAnswerFeedback,
  type CareerAssumption,
  type CareerEvidenceSource,
  type CareerFact,
  type CareerItemState,
  type CareerOption,
  type CareerPlanningOutputContract,
  type CareerPlanningProjection,
  type CareerRisk,
  type CareerStage,
  type CareerSuggestion,
} from "@/lib/api";

/** 证据类别中文标签（与后端契约一致）。 */
const EVIDENCE_KIND_CN: Record<string, string> = {
  profile_slice: "画像切片",
  learning_record: "学习记录",
  retrieval: "本地材料",
  web_search: "联网来源",
  arxiv: "arXiv 论文",
  user_statement: "用户陈述",
};

/** 条目证据状态标签与颜色。 */
const ITEM_STATE_META: Record<CareerItemState, { label: string; color: string }> = {
  verified: { label: "已核实", color: "var(--color-status-success)" },
  unverified: { label: "未核实", color: "var(--color-status-warning)" },
  conflicted: { label: "证据冲突", color: "var(--color-status-warning)" },
  outdated: { label: "来源过时", color: "var(--color-status-warning)" },
};

interface CareerItemLike {
  item_id: string;
  content: string;
  evidence_refs?: string[];
  note?: string | null;
  verified_at?: string | null;
}

interface SectionConfig {
  key: keyof CareerPlanningOutputContract;
  title: string;
  hint: string;
  testId: string;
}

const SECTIONS: readonly SectionConfig[] = [
  { key: "facts", title: "已知事实", hint: "带可定位来源与核查时间", testId: "career-facts" },
  { key: "assumptions", title: "待验证假设", hint: "附下一步核查方式", testId: "career-assumptions" },
  { key: "options", title: "可选方向", hint: "附主要依据，不暗示唯一选择", testId: "career-options" },
  { key: "risks", title: "关键风险", hint: "附触发条件，不作确定预言", testId: "career-risks" },
  { key: "path", title: "分阶段成长路径", hint: "附建议时间范围", testId: "career-path" },
  { key: "suggestions", title: "近期学习建议", hint: "附验证方式", testId: "career-suggestions" },
];

function formatDate(value?: string | null): string {
  if (!value) return "";
  return value.slice(0, 10);
}

/** 条目附加字段（假设/方向/风险/路径/建议各自的可选说明）。 */
function itemExtra(item: CareerItemLike): { label: string; value?: string | null } | null {
  const typed = item as Partial<CareerAssumption & CareerOption & CareerRisk & CareerStage & CareerSuggestion>;
  if (typed.verification_next_step) return { label: "核查方式", value: typed.verification_next_step };
  if (typed.rationale) return { label: "主要依据", value: typed.rationale };
  if (typed.trigger) return { label: "触发条件", value: typed.trigger };
  if (typed.timeline) return { label: "时间范围", value: typed.timeline };
  if (typed.verification) return { label: "验证方式", value: typed.verification };
  return null;
}

/**
 * 生涯规划结果卡（Issue 29）。
 *
 * 可展开详情展示六类分区（已知事实/待验证假设/可选方向/关键风险/分阶段
 * 成长路径/近期学习建议），每条带证据状态标签与核查时间；另附本轮证据
 * 来源清单（可打开来源）、保证边界声明与未决问题。逐项反馈把问题定位到
 * 具体条目（career_item_ref），进入既有画像治理闭环而非静默覆盖画像。
 * 失败态展示可操作说明与安全替代步骤，不输出模板化假成功。
 */
export function CareerPlanningResultCard({
  result,
  conversationId,
  messageId,
  onRetry,
}: {
  result: CareerPlanningProjection;
  conversationId: string;
  messageId: string;
  onRetry?: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  // 逐项反馈表单状态：item_id → {open, text, submitting, submitted, error}
  const [feedback, setFeedback] = useState<Record<string, { open: boolean; text: string; submitting: boolean; submitted: boolean; error: string }>>({});

  const statusLabel = result.status === "done" ? "生涯规划完成" : "规划未完成";
  const statusColor =
    result.status === "done"
      ? "var(--color-status-success)"
      : "var(--color-status-error)";
  const output = result.output;

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

  const openFeedback = (itemId: string) => {
    setFeedback((current) => ({
      ...current,
      [itemId]: { open: true, text: "", submitting: false, submitted: false, error: "" },
    }));
  };
  const closeFeedback = (itemId: string) => {
    setFeedback((current) => {
      const next = { ...current };
      delete next[itemId];
      return next;
    });
  };
  const submitFeedback = async (itemId: string) => {
    const entry = feedback[itemId];
    if (!entry || entry.submitting || !entry.text.trim()) return;
    setFeedback((current) => ({
      ...current,
      [itemId]: { ...current[itemId], submitting: true, error: "" },
    }));
    try {
      await submitAnswerFeedback(conversationId, messageId, {
        kind: "answer_inappropriate",
        feedback_text: entry.text.trim(),
        career_item_ref: itemId,
      });
      setFeedback((current) => ({
        ...current,
        [itemId]: { ...current[itemId], submitting: false, submitted: true },
      }));
    } catch (error) {
      setFeedback((current) => ({
        ...current,
        [itemId]: {
          ...current[itemId],
          submitting: false,
          error: error instanceof Error ? error.message : "反馈提交失败，请重试。",
        },
      }));
    }
  };

  const renderItem = (item: CareerItemLike) => {
    const state: CareerItemState =
      (result.review?.reviews ?? []).find((review) => review.item_id === item.item_id)?.state ??
      "verified";
    const meta = ITEM_STATE_META[state] ?? ITEM_STATE_META.verified;
    const extra = itemExtra(item);
    const entry = feedback[item.item_id];
    const evidenceTitles = (item.evidence_refs ?? [])
      .map((ref) => (result.evidence_sources ?? []).find((source) => source.evidence_id === ref)?.title)
      .filter(Boolean);

    return (
      <li key={item.item_id} style={{ display: "grid", gap: "var(--space-1)", fontSize: "var(--text-sm)" }}>
        <div style={{ display: "flex", alignItems: "flex-start", gap: "var(--space-2)" }}>
          <span style={{ color: meta.color, whiteSpace: "nowrap" }}>{meta.label}</span>
          <span style={{ flex: 1, overflowWrap: "break-word" }}>{item.content}</span>
          <button
            type="button"
            data-testid={`career-item-feedback-${item.item_id}`}
            aria-label={`反馈这条：${item.content.slice(0, 24)}`}
            onClick={() => (entry?.open ? closeFeedback(item.item_id) : openFeedback(item.item_id))}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "var(--space-1)",
              padding: "var(--space-1) var(--space-2)",
              border: "1px solid var(--color-border)",
              borderRadius: "var(--radius-md)",
              background: "transparent",
              color: "var(--color-text-secondary)",
              cursor: "pointer",
              fontSize: "var(--text-xs)",
              whiteSpace: "nowrap",
            }}
          >
            <Icon name="feedbackBad" size={14} aria-hidden />
            {entry?.open ? "收起" : "反馈"}
          </button>
        </div>
        {formatDate(item.verified_at) && (
          <div style={{ color: "var(--color-text-tertiary)", fontSize: "var(--text-xs)" }}>
            核查时间：{formatDate(item.verified_at)}
          </div>
        )}
        {extra && (
          <div style={{ color: "var(--color-text-secondary)" }}>
            {extra.label}：{extra.value}
          </div>
        )}
        {evidenceTitles.length > 0 && (
          <div style={{ color: "var(--color-text-tertiary)", fontSize: "var(--text-xs)" }}>
            依据：{evidenceTitles.join("、")}
          </div>
        )}
        {item.note && (
          <div style={{ color: "var(--color-text-secondary)", fontSize: "var(--text-xs)" }}>{item.note}</div>
        )}
        {entry?.open && (
          <div style={{ display: "grid", gap: "var(--space-1)", paddingTop: "var(--space-1)" }}>
            <label
              htmlFor={`career-feedback-${item.item_id}`}
              style={{ fontSize: "var(--text-xs)", color: "var(--color-text-secondary)" }}
            >
              这条内容哪里有问题？（会进入画像治理闭环，不会静默覆盖画像）
            </label>
            <textarea
              id={`career-feedback-${item.item_id}`}
              data-testid={`career-feedback-text-${item.item_id}`}
              value={entry.text}
              autoFocus
              onChange={(event) =>
                setFeedback((current) => ({
                  ...current,
                  [item.item_id]: { ...current[item.item_id], text: event.target.value },
                }))
              }
              rows={2}
              maxLength={500}
              style={{
                width: "100%",
                padding: "var(--space-1) var(--space-2)",
                border: "1px solid var(--color-border)",
                borderRadius: "var(--radius-md)",
                background: "var(--color-surface)",
                color: "var(--color-text)",
                fontSize: "var(--text-sm)",
                fontFamily: "inherit",
                boxSizing: "border-box",
                resize: "vertical",
              }}
            />
            {entry.error && (
              <p role="alert" style={{ margin: 0, fontSize: "var(--text-xs)", color: "var(--color-status-error)" }}>
                {entry.error}
              </p>
            )}
            <div style={{ display: "flex", justifyContent: "flex-end", gap: "var(--space-2)" }}>
              {entry.submitted ? (
                <span role="status" data-testid="career-feedback-submitted" style={{ fontSize: "var(--text-xs)", color: "var(--color-status-success)" }}>
                  已提交，谢谢反馈
                </span>
              ) : (
                <button
                  type="button"
                  data-testid={`career-feedback-submit-${item.item_id}`}
                  onClick={() => void submitFeedback(item.item_id)}
                  disabled={!entry.text.trim() || entry.submitting}
                  style={{
                    padding: "var(--space-1) var(--space-2)",
                    border: "1px solid var(--color-border-strong)",
                    borderRadius: "var(--radius-md)",
                    background: "transparent",
                    color: "var(--color-text-primary)",
                    cursor: "pointer",
                    fontSize: "var(--text-xs)",
                  }}
                >
                  {entry.submitting ? "提交中…" : "提交反馈"}
                </button>
              )}
            </div>
          </div>
        )}
      </li>
    );
  };

  const renderEvidence = (source: CareerEvidenceSource) => {
    const kind = EVIDENCE_KIND_CN[source.kind] ?? source.kind;
    const accessed = formatDate(source.accessed_at);
    return (
      <li key={source.evidence_id} style={{ fontSize: "var(--text-sm)", display: "grid", gap: "var(--space-1)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-1)", flexWrap: "wrap" }}>
          <span style={{ color: "var(--color-text-secondary)" }}>{kind}</span>
          <strong>{source.title}</strong>
          {source.stale && (
            <span style={{ color: "var(--color-status-warning)", fontSize: "var(--text-xs)" }}>可能过时</span>
          )}
          <span style={{ color: "var(--color-text-tertiary)", fontSize: "var(--text-xs)" }}>
            核查于 {accessed || "—"}
          </span>
          {source.url && (
            <a
              href={source.url}
              target="_blank"
              rel="noreferrer"
              data-testid={`career-evidence-link-${source.evidence_id}`}
              style={{ color: "var(--color-accent-primary)", fontSize: "var(--text-xs)" }}
            >
              打开来源 ↗
            </a>
          )}
        </div>
        {source.locator && source.locator !== source.url && (
          <div style={{ color: "var(--color-text-tertiary)", fontSize: "var(--text-xs)" }}>{source.locator}</div>
        )}
        {source.summary && (
          <div style={{ color: "var(--color-text-tertiary)", fontSize: "var(--text-xs)" }}>{source.summary}</div>
        )}
      </li>
    );
  };

  // 只在可重试失败（recovery 态，如模型限流/临时故障）时提供重试入口；
  // 权限/边界违反等不可重试错误只展示安全替代说明，不提供必败的重试。
  const retryButton =
    result.status === "error" &&
    result.process_state === "recovery" &&
    onRetry && (
      <button
        type="button"
        data-testid="career-result-retry"
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
        重试（原问题已保留）
      </button>
    );

  return (
    <section data-testid="career-result-card" style={cardStyle}>
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          style={{ ...headerStyle, flex: 1 }}
          aria-expanded={expanded}
          aria-controls="career-result-details"
        >
          <span style={{ color: statusColor, display: "inline-flex" }}>
            <Icon name="career" size={16} aria-hidden />
          </span>
          <strong>{statusLabel}</strong>
          {result.profile_enabled && result.profile_used && (
            <span style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>已使用最小画像切片</span>
          )}
          <span style={{ flex: 1 }} />
          {result.status === "done" && (
            <span style={{ color: "var(--color-text-tertiary)" }}>核查于 {formatDate(result.verified_at)}</span>
          )}
          <span style={{ display: "inline-flex", transition: "transform var(--motion-duration-fast) var(--motion-easing)", transform: expanded ? "rotate(180deg)" : "none" }}>
            <Icon name="chevronDown" size={16} aria-hidden />
          </span>
        </button>
        {retryButton}
      </div>

      {expanded && (
        <div id="career-result-details">
          {result.status === "error" ? (
            <div style={sectionStyle} data-testid="career-error-detail">
              <p style={{ margin: 0, color: "var(--color-status-error)", fontSize: "var(--text-sm)" }}>
                {result.error_message ?? "规划未完成，请重试。"}
              </p>
              {(result.process_steps?.length ?? 0) > 0 && (
                <p style={{ margin: "var(--space-1) 0 0", color: "var(--color-text-secondary)", fontSize: "var(--text-xs)" }}>
                  已完成的步骤：{result.process_steps!.join(" → ")}
                </p>
              )}
            </div>
          ) : (
            output && (
              <>
                {SECTIONS.map((section) => {
                  const items = (output[section.key] as CareerItemLike[] | undefined) ?? [];
                  return (
                    <div key={section.key} style={sectionStyle} data-testid={section.testId}>
                      <p style={labelStyle}>
                        {section.title}
                        <span style={{ fontWeight: 400, marginLeft: "var(--space-1)" }}>（{section.hint}）</span>
                      </p>
                      {items.length > 0 ? (
                        <ul role="list" style={{ margin: "var(--space-1) 0 0", padding: 0, listStyle: "none", display: "grid", gap: "var(--space-2)" }}>
                          {items.map(renderItem)}
                        </ul>
                      ) : (
                        <p style={{ margin: "var(--space-1) 0 0", color: "var(--color-text-secondary)", fontSize: "var(--text-sm)" }}>
                          本轮没有该类别内容。
                        </p>
                      )}
                    </div>
                  );
                })}

                {(result.evidence_sources?.length ?? 0) > 0 && (
                  <div style={sectionStyle} data-testid="career-evidence-sources">
                    <p style={labelStyle}>本轮证据（含核查时间）</p>
                    <ul role="list" style={{ margin: "var(--space-1) 0 0", padding: 0, listStyle: "none", display: "grid", gap: "var(--space-1)" }}>
                      {(result.evidence_sources ?? []).map(renderEvidence)}
                    </ul>
                  </div>
                )}

                {output.boundary_statement && (
                  <div style={sectionStyle} data-testid="career-boundary">
                    <p style={labelStyle}>保证边界声明</p>
                    <p style={{ margin: "var(--space-1) 0 0", color: "var(--color-text-secondary)", fontSize: "var(--text-sm)" }}>
                      {output.boundary_statement}
                    </p>
                  </div>
                )}

                <div style={sectionStyle} data-testid="career-open-questions">
                  <p style={labelStyle}>证据缺口与未决问题</p>
                  {(output.open_questions?.length ?? 0) > 0 ? (
                    <ul role="list" style={{ margin: "var(--space-1) 0 0", paddingLeft: "1.25rem", display: "grid", gap: "var(--space-1)", fontSize: "var(--text-sm)" }}>
                      {output.open_questions!.map((question, index) => (
                        <li key={`${question}-${index}`}>{question}</li>
                      ))}
                    </ul>
                  ) : (
                    <p style={{ margin: "var(--space-1) 0 0", color: "var(--color-text-secondary)", fontSize: "var(--text-sm)" }}>无。</p>
                  )}
                </div>
              </>
            )
          )}
        </div>
      )}
    </section>
  );
}
