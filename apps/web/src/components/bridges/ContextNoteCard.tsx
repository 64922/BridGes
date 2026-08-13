"use client";

import { useEffect, useState } from "react";

import { Icon } from "@/components/design-system/Icon";
import {
  submitAnswerFeedback,
  type AnswerFeedback,
  type ContextNoteProjection,
  type FeedbackKind,
} from "@/lib/api";

const stateLabel: Record<string, string> = {
  loading: "正在整理授权用户背景信息",
  ready: "已使用授权用户背景信息",
  empty: "未匹配到相关授权用户背景信息",
  off: "本轮未使用授权用户背景信息",
  error: "暂时无法整理授权用户背景信息",
  none: "本轮未生成用户背景说明",
};

const contextNoteHeading = "本次上下文说明（已授权用户背景使用情况）";

function stateTone(state: string): string {
  if (state === "ready") return "var(--color-status-success)";
  if (state === "empty" || state === "off") return "var(--color-text-secondary)";
  if (state === "loading") return "var(--color-accent-primary)";
  return "var(--color-status-error)";
}

interface ContextNoteCardProps {
  /** 披露投影；streaming 中尚未到达时为 null */
  note: ContextNoteProjection | null;
  /** 消息是否仍在生成（未到达终态披露前显示 loading） */
  streaming: boolean;
  conversationId: string;
  messageId: string;
}

/** 只展示用户背景摘要，不把来源、版本或撤回账本暴露给普通聊天。 */
export function ContextNoteCard({
  note,
  streaming,
  conversationId,
  messageId,
}: ContextNoteCardProps) {
  const [open, setOpen] = useState(false);
  const loading = streaming && note === null;
  const state = loading ? "loading" : note?.state ?? "none";

  return (
    <section
      data-testid="context-note-card"
      aria-label={contextNoteHeading}
      style={{
        marginBottom: "var(--space-3)",
        border: "1px solid var(--color-border)",
        borderRadius: "var(--radius-md)",
        padding: "var(--space-2) var(--space-3)",
        backgroundColor: "var(--color-bg-secondary)",
      }}
    >
      <div
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onClick={() => {
          if (!loading) setOpen((value) => !value);
        }}
        onKeyDown={(event) => {
          if ((event.key === "Enter" || event.key === " ") && !loading) {
            event.preventDefault();
            setOpen((value) => !value);
          }
        }}
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-2)",
          minHeight: "var(--target-size)",
          padding: "0 var(--space-1)",
          cursor: loading ? "default" : "pointer",
          fontSize: "var(--text-sm)",
        }}
      >
        <Icon name="profile" size={16} aria-hidden />
        <span style={{ color: "var(--color-text-secondary)", fontWeight: 600 }}>
          {contextNoteHeading}
        </span>
        <span
          role={state === "error" ? "alert" : "status"}
          style={{ color: stateTone(state), fontSize: "var(--text-xs)" }}
        >
          {stateLabel[state] ?? "上下文说明"}
        </span>
        {!loading && (
          <span
            aria-hidden="true"
            style={{
              marginLeft: "auto",
              display: "inline-flex",
              transform: open ? "rotate(180deg)" : "none",
              color: "var(--color-text-tertiary)",
            }}
          >
            <Icon name="chevronDown" size={16} />
          </span>
        )}
      </div>

      {open && note && (
        <div
          style={{
            marginTop: "var(--space-2)",
            display: "flex",
            flexDirection: "column",
            gap: "var(--space-3)",
          }}
        >
          <p style={{ margin: 0, fontSize: "var(--text-sm)", color: "var(--color-text-secondary)" }}>
            {note.note}
          </p>
          {(note.material_categories ?? []).length > 0 && (
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-2)",
                flexWrap: "wrap",
                fontSize: "var(--text-xs)",
              }}
            >
              <span style={{ color: "var(--color-text-tertiary)" }}>本轮其他来源类别：</span>
              {(note.material_categories ?? []).map((category) => (
                <span
                  key={category}
                  style={{
                    padding: "var(--space-1) var(--space-2)",
                    borderRadius: "var(--radius-full)",
                    backgroundColor: "var(--color-surface)",
                    border: "1px solid var(--color-border)",
                    color: "var(--color-text-secondary)",
                  }}
                >
                  {category}
                </span>
              ))}
            </div>
          )}
          {state === "ready" && (
            <AnswerFeedbackForm conversationId={conversationId} messageId={messageId} />
          )}
        </div>
      )}
    </section>
  );
}

function AnswerFeedbackForm({
  conversationId,
  messageId,
}: {
  conversationId: string;
  messageId: string;
}) {
  const [open, setOpen] = useState(false);
  const [problem, setProblem] = useState("");
  const [preference, setPreference] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState<AnswerFeedback | null>(null);
  const [failed, setFailed] = useState("");

  useEffect(() => {
    const key = `bridges-feedback-${conversationId}-${messageId}`;
    try {
      const draft = window.sessionStorage.getItem(key);
      if (draft) {
        const parsed = JSON.parse(draft) as { problem?: string; preference?: string };
        setProblem(parsed.problem ?? "");
        setPreference(parsed.preference ?? "");
      }
    } catch {
      // 草稿损坏时忽略，不阻塞反馈。
    }
  }, [conversationId, messageId]);

  const saveDraft = (nextProblem: string, nextPreference: string) => {
    try {
      window.sessionStorage.setItem(
        `bridges-feedback-${conversationId}-${messageId}`,
        JSON.stringify({ problem: nextProblem, preference: nextPreference })
      );
    } catch {
      // 存储不可用时忽略。
    }
  };

  const submit = async () => {
    if (!problem.trim()) {
      setFailed("请先说明这次回答哪里不合适。");
      return;
    }
    setSubmitting(true);
    setFailed("");
    try {
      const feedback = await submitAnswerFeedback(conversationId, messageId, {
        kind: "answer_inappropriate" as FeedbackKind,
        feedback_text: problem.trim(),
        preference: preference.trim() || null,
      });
      setSubmitted(feedback);
      try {
        window.sessionStorage.removeItem(`bridges-feedback-${conversationId}-${messageId}`);
      } catch {
        // 忽略清理失败。
      }
    } catch (error) {
      setFailed(error instanceof Error ? error.message : "提交失败，请重试。");
      saveDraft(problem, preference);
    } finally {
      setSubmitting(false);
    }
  };

  if (submitted) {
    return (
      <p
        role="status"
        data-testid="answer-feedback-submitted"
        style={{ margin: 0, fontSize: "var(--text-xs)", color: "var(--color-status-success)" }}
      >
        已记录你的反馈：我们会据此调整后续回答。
      </p>
    );
  }

  return (
    <div style={{ marginTop: "var(--space-1)" }}>
      {!open && (
        <button type="button" onClick={() => setOpen(true)} style={linkButtonStyle}>
          <Icon name="feedbackBad" size={14} aria-hidden />
          这次回答不合适
        </button>
      )}
      {open && (
        <div style={formStyle}>
          <label style={labelStyle}>
            哪里不合适？
            <textarea
              value={problem}
              onChange={(event) => {
                setProblem(event.target.value);
                saveDraft(event.target.value, preference);
              }}
              rows={2}
              maxLength={500}
              placeholder="例如：太长了，我只需要结论"
              style={inputStyle}
            />
          </label>
          <label style={labelStyle}>
            希望怎么回答？（可选）
            <textarea
              value={preference}
              onChange={(event) => {
                setPreference(event.target.value);
                saveDraft(problem, event.target.value);
              }}
              rows={2}
              maxLength={500}
              placeholder="例如：先给结论，再给一句理由"
              style={inputStyle}
            />
          </label>
          {failed && (
            <p role="alert" style={{ margin: 0, fontSize: "var(--text-xs)", color: "var(--color-status-error)" }}>
              {failed}（草稿已保留，可直接重试）
            </p>
          )}
          <div style={{ display: "flex", gap: "var(--space-2)", flexWrap: "wrap" }}>
            <button
              type="button"
              onClick={submit}
              disabled={submitting}
              style={{ ...primaryButtonStyle, opacity: submitting ? 0.6 : 1 }}
            >
              {submitting ? "提交中…" : "提交反馈"}
            </button>
            <button type="button" onClick={() => setOpen(false)} disabled={submitting} style={linkButtonStyle}>
              取消
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

const formStyle: React.CSSProperties = {
  padding: "var(--space-3)",
  border: "1px solid var(--color-border-strong)",
  borderRadius: "var(--radius-md)",
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-2)",
  fontSize: "var(--text-sm)",
};

const labelStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-1)",
};

const inputStyle: React.CSSProperties = {
  padding: "var(--space-2)",
  border: "1px solid var(--color-border-strong)",
  borderRadius: "var(--radius-md)",
  backgroundColor: "var(--color-surface)",
  color: "var(--color-text-primary)",
  fontFamily: "inherit",
  fontSize: "var(--text-sm)",
  resize: "vertical",
};

const linkButtonStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: "var(--space-1)",
  minHeight: "var(--target-size)",
  padding: "0 var(--space-2)",
  border: "none",
  backgroundColor: "transparent",
  color: "var(--color-accent-secondary)",
  fontSize: "var(--text-xs)",
  fontWeight: 600,
  cursor: "pointer",
  alignSelf: "flex-start",
};

const primaryButtonStyle: React.CSSProperties = {
  minHeight: "var(--target-size)",
  padding: "0 var(--space-3)",
  border: "none",
  borderRadius: "var(--radius-md)",
  backgroundColor: "var(--color-accent-primary)",
  color: "var(--color-text-on-accent)",
  fontSize: "var(--text-sm)",
  fontWeight: 600,
  cursor: "pointer",
};
