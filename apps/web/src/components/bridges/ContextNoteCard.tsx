"use client";

import { useEffect, useState } from "react";

import { Icon } from "@/components/design-system/Icon";
import {
  freezeProfileAssertion,
  modifyProfileAssertion,
  submitAnswerFeedback,
  withdrawProfileAssertion,
  type AnswerFeedback,
  type ContextNoteProjection,
  type ContextNoteProfileItem,
  type FeedbackKind,
} from "@/lib/api";

/**
 * 「本次上下文说明」可展开披露卡（Issue 27，ADR-0015）。
 *
 * 回答生成中显示 loading 态；完成后按披露状态呈现 ready（列出使用的
 * 画像类别、值摘要、用途、使用时间与来源记录链接）/ empty（无匹配记录
 * 的合法空态）/ off（发送前关闭画像）/ error（编译失败，回答已正常生成）。
 * 披露只展示类别与摘要，绝不暴露系统提示、隐藏提示或原始思维链。
 * 反馈闭环：每条画像记录可标记「画像有误」并修正/冻结/撤回；卡片尾部
 * 可标记「回答不合适」并给出偏好反馈；提交失败保留草稿可重试，不丢失
 * 用户反馈。
 */

const stateLabel: Record<string, string> = {
  loading: "正在生成上下文说明",
  ready: "已使用画像切片",
  empty: "本轮未使用画像记录",
  // AC-10 的 permission 态：发送前关闭 = 本轮画像使用未授权
  off: "画像使用未授权",
  error: "上下文说明生成失败",
  none: "本轮未生成上下文说明",
};

function stateTone(state: string): string {
  if (state === "ready") return "var(--color-status-success)";
  if (state === "empty" || state === "off") return "var(--color-text-secondary)";
  if (state === "loading") return "var(--color-accent-primary)";
  return "var(--color-status-error)";
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const now = new Date();
  const minutes = Math.floor((now.getTime() - date.getTime()) / 60000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  return date.toLocaleDateString("zh-CN");
}

interface ContextNoteCardProps {
  /** 披露投影；streaming 中尚未到达时为 null */
  note: ContextNoteProjection | null;
  /** 消息是否仍在生成（未到达终态披露前显示 loading） */
  streaming: boolean;
  conversationId: string;
  messageId: string;
}

export function ContextNoteCard({
  note,
  streaming,
  conversationId,
  messageId,
}: ContextNoteCardProps) {
  const [open, setOpen] = useState(false);
  // 终态且无披露（历史消息/退化环境）显示 none 态，不误报 loading
  const loading = streaming && note === null;
  const state = loading ? "loading" : note?.state ?? "none";

  return (
    <section
      data-testid="context-note-card"
      aria-label="本次上下文说明"
      style={{
        marginBottom: "var(--space-3)",
        border: "1px solid var(--color-border)",
        borderRadius: "var(--radius-md)",
        padding: "var(--space-2) var(--space-3)",
        backgroundColor: "var(--color-bg-secondary)",
        transition: "border-color 150ms ease",
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
        <span
          style={{ color: "var(--color-text-secondary)", fontWeight: 600 }}
        >
          本次上下文说明
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
              transition: "transform 150ms ease",
              color: "var(--color-text-tertiary)",
            }}
          >
            <Icon name="chevronDown" size={16} />
          </span>
        )}
      </div>

      {open && note && (
        <div style={{ marginTop: "var(--space-2)", display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
          <p style={{ margin: 0, fontSize: "var(--text-sm)", color: "var(--color-text-secondary)" }}>
            {note.note}
          </p>

          {(note.profile_items ?? []).length > 0 && (
            <ul
              role="list"
              aria-label="使用的画像记录"
              style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: "var(--space-2)" }}
            >
              {(note.profile_items ?? []).map((item) => (
                <ContextNoteItem
                  key={item.assertion_id}
                  item={item}
                  conversationId={conversationId}
                  messageId={messageId}
                />
              ))}
            </ul>
          )}

          {(note.material_categories ?? []).length > 0 && (
            <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", flexWrap: "wrap", fontSize: "var(--text-xs)" }}>
              <span style={{ color: "var(--color-text-tertiary)" }}>本轮材料类别：</span>
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

          {note.excluded_count > 0 && (
            <p style={{ margin: 0, fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
              另有 {note.excluded_count} 条画像记录因范围、敏感度或状态未进入本轮。
            </p>
          )}

          {state === "ready" && (
            <AnswerFeedbackForm
              conversationId={conversationId}
              messageId={messageId}
            />
          )}
        </div>
      )}
    </section>
  );
}

function ContextNoteItem({
  item,
  conversationId,
  messageId,
}: {
  item: ContextNoteProfileItem;
  conversationId: string;
  messageId: string;
}) {
  const [correctionOpen, setCorrectionOpen] = useState(false);
  const [corrected, setCorrected] = useState<string | null>(null);
  const [failed, setFailed] = useState("");

  return (
    <li
      data-testid="context-note-item"
      style={{
        padding: "var(--space-3)",
        border: "1px solid var(--color-border)",
        borderRadius: "var(--radius-md)",
        backgroundColor: "var(--color-surface)",
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-2)",
      }}
    >
      <div style={{ display: "flex", alignItems: "flex-start", gap: "var(--space-2)", flexWrap: "wrap" }}>
        <span
          style={{
            padding: "var(--space-1) var(--space-2)",
            borderRadius: "var(--radius-full)",
            backgroundColor: "var(--color-accent-primary-soft)",
            color: "var(--color-text-primary)",
            fontSize: "var(--text-xs)",
            fontWeight: 600,
            flexShrink: 0,
          }}
        >
          {item.dimension_label}
        </span>
        <span style={{ flex: 1, minWidth: 0, fontSize: "var(--text-sm)", overflowWrap: "break-word" }}>
          {item.value_summary}
        </span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", flexWrap: "wrap", fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
        <span>用途：{item.inclusion_reason}</span>
        <span>使用于：{formatTime(item.used_at)}</span>
        <span>版本 {item.version}</span>
        <a
          href={`/account/profile?assertion=${encodeURIComponent(item.assertion_id)}`}
          style={{ color: "var(--color-accent-secondary)" }}
          title="在画像中心查看这条记录"
        >
          查看记录
        </a>
      </div>
      {corrected ? (
        <p
          role="status"
          style={{ margin: 0, fontSize: "var(--text-xs)", color: "var(--color-status-success)" }}
        >
          {corrected}
        </p>
      ) : (
        <>
          {!correctionOpen && (
            <button
              type="button"
              onClick={() => setCorrectionOpen(true)}
              style={linkButtonStyle}
            >
              <Icon name="edit" size={14} aria-hidden />
              画像有误，修正它
            </button>
          )}
          {correctionOpen && (
            <ProfileCorrectionForm
              item={item}
              conversationId={conversationId}
              messageId={messageId}
              onCancel={() => setCorrectionOpen(false)}
              onCorrected={(message) => {
                setCorrectionOpen(false);
                setCorrected(message);
              }}
              failed={failed}
              onFailed={setFailed}
            />
          )}
        </>
      )}
    </li>
  );
}

function ProfileCorrectionForm({
  item,
  conversationId,
  messageId,
  onCancel,
  onCorrected,
  failed,
  onFailed,
}: {
  item: ContextNoteProfileItem;
  conversationId: string;
  messageId: string;
  onCancel: () => void;
  onCorrected: (message: string) => void;
  failed: string;
  onFailed: (message: string) => void;
}) {
  const [action, setAction] = useState<"modify" | "freeze" | "withdraw">("modify");
  const [newValue, setNewValue] = useState("");
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);

  // 失败不丢失反馈：用户输入保留在组件内，可直接重试
  useEffect(() => {
    if (failed) onFailed("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [action]);

  const submit = async () => {
    if (action === "modify" && !newValue.trim()) {
      onFailed("请填写修正后的内容。");
      return;
    }
    if (!reason.trim()) {
      onFailed("请说明修改原因。");
      return;
    }
    setSubmitting(true);
    try {
      // 1) 先记录「画像有误」反馈（幂等，失败可重试不重复）
      await submitAnswerFeedback(conversationId, messageId, {
        kind: "profile_incorrect" as FeedbackKind,
        feedback_text: `${item.dimension_label}记录有误：${reason.trim()}`,
        assertion_id: item.assertion_id,
      });
      // 2) 执行修正动作（画像治理 API）；修正时回传当时的适用场景快照，
      //    授权范围不因一次修正而漂移
      if (action === "modify") {
        await modifyProfileAssertion(item.assertion_id, {
          value_or_rule: newValue.trim(),
          applicable_scenes: item.applicable_scenes ?? [],
          reason: reason.trim(),
        });
      } else if (action === "freeze") {
        await freezeProfileAssertion(item.assertion_id, reason.trim());
      } else {
        await withdrawProfileAssertion(item.assertion_id, reason.trim());
      }
      onCorrected(
        action === "modify"
          ? "已更新画像记录，下一轮回答将使用新版本。"
          : action === "freeze"
            ? "已冻结这条记录，之后不再自动用于回答。"
            : "已撤回这条记录，之后不再用于回答。"
      );
    } catch (error) {
      onFailed(error instanceof Error ? error.message : "提交失败，请重试。");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      style={{
        marginTop: "var(--space-1)",
        padding: "var(--space-3)",
        border: "1px solid var(--color-border-strong)",
        borderRadius: "var(--radius-md)",
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-2)",
        fontSize: "var(--text-sm)",
      }}
    >
      <div role="radiogroup" aria-label="修正动作" style={{ display: "flex", gap: "var(--space-2)", flexWrap: "wrap" }}>
        {(
          [
            ["modify", "修正内容"],
            ["freeze", "冻结记录"],
            ["withdraw", "撤回记录"],
          ] as const
        ).map(([value, label]) => (
          <label
            key={value}
            style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-1)", cursor: "pointer" }}
          >
            <input
              type="radio"
              name={`correction-${item.assertion_id}`}
              checked={action === value}
              onChange={() => setAction(value)}
            />
            {label}
          </label>
        ))}
      </div>
      {action === "modify" && (
        <label style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
          修正后的内容
          <textarea
            value={newValue}
            onChange={(event) => setNewValue(event.target.value)}
            rows={2}
            maxLength={1000}
            placeholder="例如：我其实更喜欢详细、带例子的回答"
            style={inputStyle}
          />
        </label>
      )}
      <label style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
        原因
        <textarea
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          rows={2}
          maxLength={500}
          placeholder="为什么这条记录不准？（会随反馈一起记录）"
          style={inputStyle}
        />
      </label>
      {failed && (
        <p role="alert" style={{ margin: 0, fontSize: "var(--text-xs)", color: "var(--color-status-error)" }}>
          {failed}（输入已保留，可直接重试）
        </p>
      )}
      <div style={{ display: "flex", gap: "var(--space-2)", flexWrap: "wrap" }}>
        <button type="button" onClick={submit} disabled={submitting} style={{ ...primaryButtonStyle, opacity: submitting ? 0.6 : 1 }}>
          {submitting ? "提交中…" : "提交修正"}
        </button>
        <button type="button" onClick={onCancel} disabled={submitting} style={linkButtonStyle}>
          取消
        </button>
      </div>
    </div>
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

  // 草稿保留：切换对话或刷新后仍可恢复（不丢失用户反馈）
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
      // 草稿损坏时忽略，不阻塞表单
    }
  }, [conversationId, messageId]);

  const saveDraft = (nextProblem: string, nextPreference: string) => {
    try {
      window.sessionStorage.setItem(
        `bridges-feedback-${conversationId}-${messageId}`,
        JSON.stringify({ problem: nextProblem, preference: nextPreference })
      );
    } catch {
      // 存储不可用时忽略
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
        // 忽略清理失败
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
      <p role="status" data-testid="answer-feedback-submitted" style={{ margin: 0, fontSize: "var(--text-xs)", color: "var(--color-status-success)" }}>
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
        <div
          style={{
            padding: "var(--space-3)",
            border: "1px solid var(--color-border-strong)",
            borderRadius: "var(--radius-md)",
            display: "flex",
            flexDirection: "column",
            gap: "var(--space-2)",
            fontSize: "var(--text-sm)",
          }}
        >
          <label style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
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
          <label style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
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
            <button type="button" onClick={submit} disabled={submitting} style={{ ...primaryButtonStyle, opacity: submitting ? 0.6 : 1 }}>
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
