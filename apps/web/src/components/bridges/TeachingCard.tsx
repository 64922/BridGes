"use client";

import { Icon } from "@/components/design-system/Icon";
import type { TeachingTurnProjection } from "@/lib/api";

interface TeachingCardProps {
  teaching: TeachingTurnProjection;
  onSkip?: () => void;
  onRetry?: () => void;
}

const statusLabel: Record<string, string> = {
  loading: "正在检查证据",
  recovery: "正在恢复检查",
  ready: "证据检查完成",
  empty: "暂未找到足够证据",
  error: "证据检查失败",
  permission: "证据来源无权限",
};

const evidenceLabel: Record<string, string> = {
  sufficient: "证据充足",
  insufficient: "证据不足",
  conflict: "本地材料存在冲突",
  unavailable: "证据暂不可用",
};

const sourceLabel: Record<string, string> = {
  attachment: "当前对话附件",
  project: "学习项目文件",
  knowledge_base: "已授权知识库",
  local: "本地材料",
  duckduckgo: "DuckDuckGo",
  arxiv: "arXiv",
};

const searchSourceLabel: Record<string, string> = {
  none: "无需联网",
  duckduckgo: "DuckDuckGo",
  arxiv: "arXiv",
  both: "DuckDuckGo + arXiv",
};

const evaluationLabel: Record<string, string> = {
  correct: "覆盖充分",
  partial: "部分覆盖",
  incorrect: "尚未覆盖",
  needs_review: "待复核",
};

function statusTone(status: string): string {
  if (status === "ready") return "var(--color-status-success)";
  if (status === "loading" || status === "recovery") return "var(--color-accent-primary)";
  return "var(--color-status-error)";
}

export function TeachingCard({ teaching, onSkip, onRetry }: TeachingCardProps) {
  const gate = teaching.evidence_gate;
  const sources = [...(gate.local_sources ?? []), ...(gate.external_sources ?? [])];
  const isBusy = teaching.status === "loading" || teaching.status === "recovery";

  return (
    <section
      data-testid="teaching-card"
      aria-label="本轮教学"
      style={{
        marginBottom: "var(--space-3)",
        padding: "var(--space-4)",
        border: "1px solid var(--color-border-strong)",
        borderRadius: "var(--radius-lg)",
        backgroundColor: "var(--color-bg-secondary)",
        color: "var(--color-text-primary)",
      }}
    >
      <div style={{ display: "flex", gap: "var(--space-2)", alignItems: "flex-start" }}>
        <Icon name="learningProject" size={20} aria-hidden />
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ display: "flex", gap: "var(--space-2)", alignItems: "baseline", flexWrap: "wrap" }}>
            <h3 style={{ margin: 0, fontSize: "var(--text-base)" }}>
              本轮教学
            </h3>
            <span role={isBusy ? "status" : teaching.status === "error" || teaching.status === "permission" ? "alert" : "status"} style={{ color: statusTone(teaching.status), fontSize: "var(--text-sm)" }}>
              {statusLabel[teaching.status] ?? teaching.status}
            </span>
          </div>
          <p style={{ margin: "var(--space-2) 0 0", color: "var(--color-text-secondary)" }}>
            {teaching.goal}
          </p>
        </div>
      </div>

      <dl style={{ display: "grid", gap: "var(--space-2)", margin: "var(--space-4) 0 0", fontSize: "var(--text-sm)" }}>
        <div>
          <dt style={{ fontWeight: 600 }}>当前水平假设</dt>
          <dd style={{ margin: 0, color: "var(--color-text-secondary)" }}>{teaching.level_assumption}</dd>
        </div>
        <div>
          <dt style={{ fontWeight: 600 }}>本轮步骤</dt>
          <dd style={{ margin: 0 }}>
            <ol style={{ margin: 0, paddingLeft: "var(--space-5)", color: "var(--color-text-secondary)" }}>
              {teaching.steps.map((step, index) => <li key={`${step}-${index}`}>{step}</li>)}
            </ol>
          </dd>
        </div>
        <div>
          <dt style={{ fontWeight: 600 }}>理解检查</dt>
          <dd style={{ margin: 0, color: "var(--color-text-secondary)" }}>{teaching.check_method}</dd>
        </div>
      </dl>

      <div
        style={{
          marginTop: "var(--space-4)",
          padding: "var(--space-3)",
          border: "1px solid var(--color-border)",
          borderRadius: "var(--radius-md)",
          backgroundColor: "var(--color-surface)",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", gap: "var(--space-2)", flexWrap: "wrap" }}>
          <strong>证据门：{evidenceLabel[gate.status] ?? gate.status}</strong>
          <span style={{ color: "var(--color-text-secondary)", fontSize: "var(--text-sm)" }}>
            补充来源：{searchSourceLabel[gate.required_search] ?? "待确定"}
          </span>
        </div>
        <p style={{ margin: "var(--space-2) 0 0", color: "var(--color-text-secondary)", fontSize: "var(--text-sm)" }}>
          {gate.reason}
        </p>
        {sources.length > 0 && (
          <ul aria-label="本轮证据来源" style={{ margin: "var(--space-2) 0 0", paddingLeft: "var(--space-5)", fontSize: "var(--text-sm)" }}>
            {sources.map((source) => (
              <li key={`${source.source_id}-${source.source_type}`}>
                <span>{sourceLabel[source.source_type] ?? source.source_type}：{source.title}</span>
                {source.locator && <span style={{ color: "var(--color-text-tertiary)" }}>（{source.locator}）</span>}
                {source.url && (
                  <a
                    href={source.url}
                    target="_blank"
                    rel="noreferrer"
                    style={{ marginLeft: "var(--space-2)" }}
                  >
                    打开来源
                  </a>
                )}
                {source.alternate_url && (
                  <a
                    href={source.alternate_url}
                    target="_blank"
                    rel="noreferrer"
                    style={{ marginLeft: "var(--space-2)" }}
                  >
                    打开 PDF
                  </a>
                )}
              </li>
            ))}
          </ul>
        )}
        {gate.gap && (
          <p role="alert" style={{ margin: "var(--space-2) 0 0", color: "var(--color-status-error)", fontSize: "var(--text-sm)" }}>
            {gate.gap}
          </p>
        )}
      </div>

      {teaching.quiz && teaching.can_answer_reliably && (
        <div style={{ marginTop: "var(--space-4)" }}>
          <p style={{ margin: 0, fontWeight: 600 }}>理解检查题</p>
          <p style={{ margin: "var(--space-2) 0 0" }}>{teaching.quiz.question}</p>
          <div style={{ display: "flex", gap: "var(--space-2)", flexWrap: "wrap", marginTop: "var(--space-3)" }}>
            {teaching.quiz.can_skip && onSkip && (
              <button type="button" onClick={onSkip} style={buttonStyle}>
                跳过这题
              </button>
            )}
            <span style={{ alignSelf: "center", color: "var(--color-text-tertiary)", fontSize: "var(--text-sm)" }}>
              也可以直接追问或切换回日常陪伴
            </span>
          </div>
        </div>
      )}

      {(teaching.evidence ?? []).length > 0 && (
        <div style={{ marginTop: "var(--space-4)", fontSize: "var(--text-sm)" }}>
          <strong>回答评价记录</strong>
          <ul style={{ margin: "var(--space-2) 0 0", paddingLeft: "var(--space-5)" }}>
            {(teaching.evidence ?? []).map((item) => (
              <li key={item.answer_id}>
                {evaluationLabel[item.evaluated_state] ?? "待复核"}：{item.evaluation_basis}。知识状态为待确认候选；不会直接标记为“已掌握”。
              </li>
            ))}
          </ul>
        </div>
      )}

      <div style={{ display: "flex", gap: "var(--space-2)", alignItems: "center", flexWrap: "wrap", marginTop: "var(--space-4)" }}>
        <span style={{ color: "var(--color-text-secondary)", fontSize: "var(--text-sm)" }}>{teaching.next_prompt}</span>
        {teaching.can_retry && onRetry && (
          <button type="button" onClick={onRetry} style={buttonStyle}>
            重试证据检查
          </button>
        )}
      </div>
    </section>
  );
}

const buttonStyle: React.CSSProperties = {
  minHeight: "var(--target-size)",
  padding: "0 var(--space-3)",
  border: "1px solid var(--color-border-strong)",
  borderRadius: "var(--radius-md)",
  backgroundColor: "var(--color-surface)",
  color: "var(--color-text-primary)",
  cursor: "pointer",
};
