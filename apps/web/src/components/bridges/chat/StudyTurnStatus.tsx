"use client";

import { useState } from "react";

import {
  CONTROL_KIND_LABEL,
  DIAGNOSIS_STATUS_LABEL,
  SUMMARY_TRIGGER_LABEL,
  pendingCheckNote,
  verdictLabel,
  verdictTone,
  type StudyTurnState,
} from "@/lib/study-presentation";

import styles from "./StudyTurnStatus.module.css";

export function StudyTurnStatus({
  state,
  busy,
  onRetryDiagnosis,
  onOpenDetails,
}: {
  state: StudyTurnState;
  busy: boolean;
  onRetryDiagnosis?: (taskId: string) => Promise<void>;
  onOpenDetails: (trigger: HTMLButtonElement) => void;
}) {
  const [retrying, setRetrying] = useState<string | null>(null);
  const hasContent =
    state.pending !== null
    || state.goal !== null
    || state.controls.length > 0
    || state.paused !== null
    || state.summary !== null
    || state.diagnosis.length > 0
    || state.records.length > 0
    || state.adoptionCount > 0;
  if (!hasContent) return null;

  return (
    <div
      className={styles.turnStatus}
      role="group"
      aria-label="本轮学习状态"
      data-testid="study-turn-status"
    >
      {state.pending && (
        <div className={styles.item} data-state="unanswered">
          <span>
            <span className={styles.label}>待你作答</span>
            ：{state.pending.question}
            {pendingCheckNote(state.pending)}
          </span>
        </div>
      )}

      {state.goal && (
        <div className={styles.item} data-state="recorded">
          <span>
            <span className={styles.label}>本轮形成学习目标</span>
            ：{state.goal.statement}
          </span>
        </div>
      )}

      {state.controls.map((control) => (
        <div
          key={`${control.kind}-${control.question}`}
          className={styles.item}
          data-state="unverified"
        >
          <span>
            <span className={styles.label}>未验证</span>
            ：你选择了{CONTROL_KIND_LABEL[control.kind] ?? control.kind}
            {control.question ? `「${control.question}」` : "本题"}
            并保持未验证，不记为掌握
            {control.released ? "；可以继续下一步" : ""}。
          </span>
        </div>
      ))}

      {state.paused && (
        <div className={styles.item} data-state="unverified">
          <span>
            <span className={styles.label}>已暂停</span>
            ：停在「{state.paused.position || state.paused.topic}」，进展已保存；
            发送「继续」即可接着学。
            {state.paused.pendingQuestion
              && `（保留问题「${state.paused.pendingQuestion}」保持未验证）`}
          </span>
        </div>
      )}

      {state.summary && (
        <div
          className={styles.item}
          data-state={state.summary.status === "complete" ? "recorded" : "service-failure"}
        >
          <span>
            <span className={styles.label}>阶段小结</span>
            （{SUMMARY_TRIGGER_LABEL[state.summary.trigger] ?? state.summary.trigger}）：
            {state.summary.status === "complete"
              ? "已随本条回复完整交付。"
              : "回复未完整包含原文，可重试该消息补全。"}
          </span>
          <div className={styles.actions}>
            <button
              type="button"
              className={styles.action}
              onClick={(event) => onOpenDetails(event.currentTarget)}
            >
              查看阶段小结
            </button>
          </div>
        </div>
      )}

      {state.diagnosis.map((task) => (
        <div
          key={task.taskId}
          className={styles.item}
          data-state={
            task.status === "failed"
              ? "service-failure"
              : task.status === "invalidated"
                ? "unverified"
                : "verification-pending"
          }
          role={task.status === "failed" ? "alert" : undefined}
        >
          <span>
            <span className={styles.label}>
              {task.status === "failed"
                ? "后台核验失败"
                : task.status === "invalidated"
                  ? "未形成诊断"
                  : task.status === "pending"
                    ? "待后台核验"
                    : task.status === "running"
                      ? "后台核验中"
                      : DIAGNOSIS_STATUS_LABEL[task.status] ?? "后台核验"}
            </span>
            {task.failureReason
              ? `：${task.failureReason}`
              : task.status === "invalidated"
                ? "：来源已失效，本轮未形成可用诊断"
                : ""}
            {task.status === "failed" || task.status === "invalidated"
              ? "。"
              : "；已生成的解释保留，完成后会更新学情。"}
          </span>
          {task.status === "failed" && task.retryable && onRetryDiagnosis && (
            <div className={styles.actions}>
              <button
                type="button"
                className={styles.action}
                disabled={busy || retrying === task.taskId}
                onClick={async () => {
                  setRetrying(task.taskId);
                  try {
                    await onRetryDiagnosis(task.taskId);
                  } finally {
                    setRetrying(null);
                  }
                }}
              >
                重试核验
              </button>
            </div>
          )}
        </div>
      ))}

      {state.records.map((record) => (
        <div
          key={record.recordId}
          className={styles.item}
          data-state={record.corrected ? "unverified" : verdictTone(record.verdict)}
        >
          <span>
            <span className={styles.label}>
              {record.corrected ? "已纠正（自评，未经核验）" : "本次作答已记录"}
            </span>
            ：「{record.title}」
            {record.corrected
              ? "的原核验结论不再用于后续教学。"
              : ` · ${verdictLabel(record.verdict)}`}
            {!record.corrected && record.pendingConfirmation
              && "，证据方向冲突，待进一步验证"}
            {!record.sourceAvailable && "；部分依据来源已不可读，仅保留可核对的历史记录"}
          </span>
          <div className={styles.actions}>
            <button
              type="button"
              className={styles.action}
              onClick={(event) => onOpenDetails(event.currentTarget)}
            >
              查看学习档案
            </button>
          </div>
        </div>
      ))}

      {state.adoptionCount > 0 && (
        <div className={styles.item} data-state="recorded">
          <span>
            <span className={styles.label}>已采用学情</span>
            ：本轮采用 {state.adoptionCount} 条学情依据，用于调整讲法、例子或检查。
          </span>
          <div className={styles.actions}>
            <button
              type="button"
              className={styles.action}
              onClick={(event) => onOpenDetails(event.currentTarget)}
            >
              查看依据
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
