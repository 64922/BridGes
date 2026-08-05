"use client";

import { useState } from "react";

import { Icon } from "@/components/design-system/Icon";
import type { HumanizerResultProjection } from "@/lib/api";

/**
 * 文章人味化结果卡（Issue 28）。
 *
 * 可展开详情展示输出合同五要素：最终文本（即消息正文）、逐项修改细节、
 * 每项理由、事实核查结果与尚未解决的问题；另附事实锁比较摘要、引用
 * 保持清单与体裁规则复核结果。状态：已完成 / 需人工确认（明确标注）/
 * 失败（停止交付，说明冲突）。
 */
export function HumanizerResultCard({
  result,
  onRetry,
}: {
  result: HumanizerResultProjection;
  /** Issue 28：失败（停止交付）后从原任务重试（输入保留）。 */
  onRetry?: () => void;
}) {
  const [expanded, setExpanded] = useState(false);

  const statusLabel =
    result.status === "done"
      ? "人味化完成"
      : result.status === "needs_human"
        ? "需人工确认"
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
