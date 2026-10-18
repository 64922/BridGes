"use client";

import { useState } from "react";

import { Button } from "@/components/design-system/Button";
import type { ChatConversationProjection } from "@/lib/api";
import {
  ANSWER_ASSISTANCE_LABEL,
  DIAGNOSIS_STATUS_LABEL,
  DIAGNOSIS_VERDICT_LABEL,
} from "@/lib/study-presentation";

import styles from "./chat.module.css";

// Issue 06：删除先按既有用户管理规范确认；撤回判断与旧证据的采用，
// 原聊天历史仍可查看。
const DELETE_RECORD_CONFIRM =
  "删除后这条判断与关联旧证据不再用于后续教学，迟到的核验结果也不会恢复它；"
  + "原聊天历史仍可查看。继续吗？";

export function StudyDiagnosisPanel({
  tasks,
  records,
  busy,
  onRetry,
  onCorrect,
  onDelete,
}: {
  tasks: NonNullable<ChatConversationProjection["study_diagnosis_tasks"]>;
  records: NonNullable<ChatConversationProjection["study_learning_record"]>;
  busy: boolean;
  onRetry?: (taskId: string) => Promise<void>;
  onCorrect?: (
    recordId: string,
    note: string,
    expectedVersion: number
  ) => Promise<boolean>;
  onDelete?: (recordId: string, expectedVersion: number) => Promise<void>;
}) {
  const [retrying, setRetrying] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [savingId, setSavingId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  if (tasks.length === 0 && records.length === 0) return null;
  return (
    <div className={styles.studySummary} aria-label="学习档案">
      <p className={styles.studySummaryNote}>
        后台独立核验只针对当时的实际作答；一次完成不代表长期掌握，结论会随新证据更新。
        你可以查看依据、纠正判断或删除记录。
      </p>
      {tasks.length > 0 && (
        <div className={styles.studySummarySection}>
          <h3>核验状态</h3>
          <ul>
            {tasks.map((task) => (
              <li key={task.task_id}>
                {task.question ? `「${task.question}」：` : ""}
                {DIAGNOSIS_STATUS_LABEL[task.status] ?? task.status}
                {task.failure_reason && ` · ${task.failure_reason}`}
                {task.status === "failed" && task.retryable && onRetry && (
                  <Button variant="secondary" size="sm"
                    disabled={busy || retrying === task.task_id}
                    onClick={async () => {
                      setRetrying(task.task_id);
                      try { await onRetry(task.task_id); } finally { setRetrying(null); }
                    }}>
                    重试核验
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      {records.length > 0 && (
        <div className={styles.studySummarySection}>
          <h3>已核验的知识点</h3>
          <ul>
            {records.map((record) => {
              const corrected = record.state === "corrected";
              const editing = editingId === record.record_id;
              const recordBusy =
                savingId === record.record_id || deletingId === record.record_id;
              return (
                <li key={record.record_id}>
                  <strong>{record.title}</strong>
                  {record.subject && `（${record.subject}`
                    + `${record.usage_context ? ` · ${record.usage_context}` : ""}）`}
                  ：{corrected
                    ? "用户纠正（自评，未经核验）"
                    : DIAGNOSIS_VERDICT_LABEL[record.verdict] ?? record.verdict}
                  {!corrected && record.pending_confirmation
                    && "，证据方向冲突，待进一步验证"}
                  {!corrected && record.performance
                    && <span> · 表现：{record.performance}</span>}
                  {corrected && (
                    <div>
                      <p className={styles.studySummaryNote}>
                        我的纠正（自评，未经核验）：{record.user_note}
                      </p>
                      {record.superseded_verdict && (
                        <p className={styles.studySummaryNote}>
                          原核验结论「{DIAGNOSIS_VERDICT_LABEL[record.superseded_verdict]
                            ?? record.superseded_verdict}」已被你的纠正取代，不再用于后续教学。
                          {record.superseded_performance
                            && `（原表现：${record.superseded_performance}）`}
                        </p>
                      )}
                      {record.pending_confirmation && (
                        <p className={styles.studySummaryNote}>
                          你的纠正与后续自动核验并存，待进一步验证。
                        </p>
                      )}
                    </div>
                  )}
                  {!record.source_available && (
                    <p className={styles.studySummaryNote}>
                      部分依据来源已不可读，仅保留可核对的历史记录。
                    </p>
                  )}
                  {editing ? (
                    <div className={styles.studyRecordActions}>
                      <textarea
                        aria-label="纠正内容"
                        value={draft}
                        maxLength={500}
                        rows={3}
                        onChange={(event) => setDraft(event.target.value)}
                      />
                      <Button variant="primary" size="sm"
                        disabled={busy || recordBusy || !draft.trim()}
                        onClick={async () => {
                          setSavingId(record.record_id);
                          try {
                            const saved = await onCorrect?.(
                              record.record_id, draft.trim(), record.record_version
                            );
                            if (saved) {
                              setEditingId(null);
                              setDraft("");
                            }
                          } finally {
                            setSavingId(null);
                          }
                        }}>
                        保存
                      </Button>
                      <Button variant="ghost" size="sm" disabled={busy || recordBusy}
                        onClick={() => { setEditingId(null); setDraft(""); }}>
                        取消
                      </Button>
                    </div>
                  ) : (
                    (onCorrect || onDelete) && (
                      <div className={styles.studyRecordActions}>
                        {onCorrect && (
                          <Button variant="secondary" size="sm" disabled={busy}
                            onClick={() => {
                              setEditingId(record.record_id);
                              setDraft(corrected ? record.user_note : "");
                            }}>
                            纠正
                          </Button>
                        )}
                        {onDelete && (
                          <Button variant="danger" size="sm" disabled={busy || recordBusy}
                            onClick={async () => {
                              if (!window.confirm(DELETE_RECORD_CONFIRM)) return;
                              setDeletingId(record.record_id);
                              try {
                                await onDelete(record.record_id, record.record_version);
                              } finally {
                                setDeletingId(null);
                              }
                            }}>
                            删除
                          </Button>
                        )}
                      </div>
                    )
                  )}
                  <details>
                    <summary>依据与证据（{record.evidence_count} 条）</summary>
                    {((record.supporting ?? []).length > 0
                      || (record.refuting ?? []).length > 0
                      || record.uncertainty) && (
                      <ul>
                        {(record.supporting ?? []).map((item, index) => (
                          <li key={`supporting-${index}`}>支持：{item}</li>
                        ))}
                        {(record.refuting ?? []).map((item, index) => (
                          <li key={`refuting-${index}`}>抵触：{item}</li>
                        ))}
                        {record.uncertainty && <li>不确定性：{record.uncertainty}</li>}
                      </ul>
                    )}
                    <ol>
                      {(record.evidences ?? []).map((evidence) => (
                        <li key={evidence.evidence_id}>
                          {evidence.source_available ? (
                            <>
                              「{evidence.question}」
                              {ANSWER_ASSISTANCE_LABEL[evidence.assistance]
                                && `（${ANSWER_ASSISTANCE_LABEL[evidence.assistance]}）`}
                              {evidence.verdict
                                && ` · 核验：${DIAGNOSIS_VERDICT_LABEL[evidence.verdict]
                                  ?? evidence.verdict}`}
                            </>
                          ) : (
                            <>
                              {evidence.question ? `「${evidence.question}」` : "一条依据"}
                              （{evidence.source_note || "来源已不可读"}）
                            </>
                          )}
                        </li>
                      ))}
                    </ol>
                  </details>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}
