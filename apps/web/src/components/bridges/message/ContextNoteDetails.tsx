"use client";

import type { ContextNoteProjection } from "@/lib/api";

import { ContextFeedbackForm } from "./ContextFeedbackForm";

const stateLabel: Record<string, string> = {
  ready: "已使用授权用户背景信息",
  empty: "未匹配到相关授权用户背景信息",
  off: "本轮未使用授权用户背景信息",
  error: "暂时无法整理授权用户背景信息",
  none: "本轮没有上下文披露",
};

const contextNoteHeading = "本次上下文说明（已授权用户背景使用情况）";

function stateTone(state: string): string {
  if (state === "ready") return "var(--color-status-success)";
  if (state === "empty" || state === "off" || state === "none") {
    return "var(--color-text-secondary)";
  }
  return "var(--color-status-error)";
}

interface ContextNoteDetailsProps {
  /** 披露投影；历史消息无披露为 null，流式尚未到达时可能是 undefined */
  note: ContextNoteProjection | null | undefined;
  /** 消息是否仍在生成（未到达终态披露前显示说明而非无限加载） */
  streaming: boolean;
  conversationId?: string;
  messageId: string;
}

/**
 * 公开过程展开后的上下文说明。只展示既有披露文案、材料类别与纠正入口，
 * 不展示完整画像、来源账本或内部推理。
 */
export function ContextNoteDetails({
  note,
  streaming,
  conversationId,
  messageId,
}: ContextNoteDetailsProps) {
  const pending = note === undefined || (streaming && note === null);
  const state = pending ? "pending" : note?.state ?? "none";
  const label = pending
    ? streaming
      ? "回答完成后可查看本轮上下文说明"
      : "正在同步本轮上下文说明"
    : stateLabel[state] ?? "上下文说明";

  return (
    <section
      data-testid="context-note-details"
      aria-label={contextNoteHeading}
      style={{
        marginTop: "var(--space-2)",
        paddingTop: "var(--space-2)",
        borderTop: "1px solid var(--color-border)",
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-2)",
        fontSize: "var(--text-sm)",
      }}
    >
      <p
        style={{
          margin: 0,
          color: "var(--color-text-secondary)",
          fontWeight: 600,
          fontSize: "var(--text-xs)",
        }}
      >
        {contextNoteHeading}
      </p>
      <p
        role={!pending && state === "error" ? "alert" : undefined}
        style={{ margin: 0, color: stateTone(state), fontSize: "var(--text-xs)" }}
      >
        {label}
      </p>
      {note && (
        <p style={{ margin: 0, color: "var(--color-text-secondary)" }}>{note.note}</p>
      )}
      {(note?.material_categories ?? []).length > 0 && (
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
          {(note?.material_categories ?? []).map((category) => (
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
      {note?.state === "ready" && conversationId && (
        <ContextFeedbackForm conversationId={conversationId} messageId={messageId} />
      )}
    </section>
  );
}
