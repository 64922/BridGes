"use client";

import { useEffect, useMemo, useState } from "react";

import {
  deleteFourDimensionProfileRecord,
  getFourDimensionProfileStatus,
  listFourDimensionProfileRecords,
  modifyFourDimensionProfileRecord,
  withdrawFourDimensionProfileRecord,
  type FourDimension,
  type FourDimensionProfileRecord,
  type ProfileStatusProjection,
} from "@/lib/api";

import styles from "./FourDimensionProfileCenter.module.css";

const DIMENSIONS: { value: FourDimension; label: string }[] = [
  { value: "academic_status", label: "学业情况" },
  { value: "knowledge_interest", label: "感兴趣的知识" },
  { value: "hobby", label: "兴趣爱好" },
  { value: "stage_goal", label: "阶段目标" },
];

function formatStableTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "long",
    day: "numeric",
  }).format(new Date(value));
}

function formatUpdatedTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "long",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

const CONFIDENCE_LABELS: Record<FourDimensionProfileRecord["confidence"], string> = {
  low: "低",
  medium: "中",
  high: "高",
};

const EMPTY_COPY: Record<FourDimension, string> = {
  academic_status: "聊过学习经历后，会在这里整理与你有关的内容。",
  knowledge_interest: "聊过感兴趣的知识后，会在这里整理相关内容。",
  hobby: "聊过兴趣爱好后，会在这里整理相关内容。",
  stage_goal: "聊过近期计划后，会在这里整理相关内容。",
};

/** Issue 06：展示四类记录的证据、把握度与最近变化。 */
const PROFILE_STATUS_COPY: Record<ProfileStatusProjection["status"], string> = {
  ready: "画像已更新。",
  empty: "目前还没有可整理的相关信息。",
  pending: "正在整理，等待重试。",
  failed: "画像整理暂时不可用，聊天仍可继续。",
};

export function FourDimensionProfileCenter() {
  const [records, setRecords] = useState<FourDimensionProfileRecord[] | null>(null);
  const [profileStatus, setProfileStatus] = useState<ProfileStatusProjection | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<FourDimensionProfileRecord | null>(null);
  const [draft, setDraft] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = async () => {
    setError(null);
    const [recordsResult, statusResult] = await Promise.allSettled([
      listFourDimensionProfileRecords(),
      getFourDimensionProfileStatus(),
    ]);
    if (recordsResult.status === "fulfilled") {
      setRecords(recordsResult.value);
    }
    if (statusResult.status === "fulfilled") {
      setProfileStatus(statusResult.value);
    }
    if (recordsResult.status === "rejected") {
      const reason = recordsResult.reason;
      setError(reason instanceof Error ? reason.message : "个人信息暂时无法加载，请重试。");
    } else if (statusResult.status === "rejected") {
      setError("画像状态暂时无法加载，已有信息仍可用。请稍后重试。");
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const grouped = useMemo(() => {
    const groups = new Map<FourDimension, FourDimensionProfileRecord[]>();
    for (const dimension of DIMENSIONS) groups.set(dimension.value, []);
    for (const record of records ?? []) groups.get(record.dimension)?.push(record);
    return groups;
  }, [records]);

  const startEdit = (record: FourDimensionProfileRecord) => {
    setEditing(record);
    setDraft(record.content);
  };

  const saveEdit = async () => {
    if (!editing || !draft.trim()) return;
    try {
      setBusyId(editing.record_id);
      const updated = await modifyFourDimensionProfileRecord(editing.record_id, {
        content: draft.trim(),
        version: editing.version,
      });
      setRecords((current) =>
        current?.map((record) =>
          record.record_id === updated.record_id ? updated : record
        ) ?? current
      );
      setEditing(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "个人信息修改失败，请刷新后重试。");
    } finally {
      setBusyId(null);
    }
  };

  const withdraw = async (record: FourDimensionProfileRecord) => {
    if (!window.confirm("撤回后这条信息将停止使用，但会保留内部记录。继续吗？")) return;
    try {
      setBusyId(record.record_id);
      await withdrawFourDimensionProfileRecord(record.record_id, record.version);
      setRecords((current) =>
        current?.filter((candidate) => candidate.record_id !== record.record_id) ?? current
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "个人信息撤回失败，请刷新后重试。");
    } finally {
      setBusyId(null);
    }
  };

  const remove = async (record: FourDimensionProfileRecord) => {
    if (!window.confirm("删除后这条记录和相关观察会被永久移除，无法恢复。继续吗？")) {
      return;
    }
    try {
      setBusyId(record.record_id);
      await deleteFourDimensionProfileRecord(record.record_id, record.version);
      setRecords((current) =>
        current?.filter((candidate) => candidate.record_id !== record.record_id) ?? current
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "个人信息删除失败，请刷新后重试。");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <main className={styles.page} aria-labelledby="four-dimension-profile-title">
      <header className={styles.header}>
        <p className={styles.eyebrow}>关于你的信息</p>
        <h1 id="four-dimension-profile-title">你的信息</h1>
      </header>

      {error ? (
        <div className={styles.error} role="alert">
          <span>{error}</span>
          <button type="button" onClick={() => void load()}>
            重试
          </button>
        </div>
      ) : null}

      {profileStatus &&
      (profileStatus.status === "pending" || profileStatus.status === "failed") ? (
        <div className={styles.state} role="status" data-testid="profile-status">
          {PROFILE_STATUS_COPY[profileStatus.status]}
          {profileStatus.can_retry ? "稍后可以重试。" : ""}
        </div>
      ) : null}

      {records === null && !error ? <p className={styles.state}>正在加载信息…</p> : null}

      <div className={styles.groups}>
        {DIMENSIONS.map((dimension) => {
          const dimensionRecords = grouped.get(dimension.value) ?? [];
          return (
            <section className={styles.group} key={dimension.value}>
              <div className={styles.groupHeader}>
                <h2>{dimension.label}</h2>
              </div>
              {dimensionRecords.length === 0 ? (
                <p className={styles.empty}>{EMPTY_COPY[dimension.value]}</p>
              ) : (
                <div className={styles.records}>
                  {dimensionRecords.map((record) => (
                    <article className={styles.record} key={record.record_id}>
                      <p className={styles.content}>{record.content}</p>
                      <div className={styles.evidenceMeta}>
                        <span className={styles.confidence}>
                          可靠程度：{CONFIDENCE_LABELS[record.confidence]}
                        </span>
                        <span className={styles.source}>来自某次对话</span>
                      </div>
                      {record.evidence_quote ? (
                        <blockquote className={styles.evidence}>
                          “{record.evidence_quote}”
                        </blockquote>
                      ) : (
                        <p className={styles.evidenceEmpty}>暂未保存证据原话</p>
                      )}
                      <p className={styles.time}>
                        首次记录于 {formatStableTime(record.first_stable_recorded_at)}
                      </p>
                      <p className={styles.change}>
                        {record.change_note ?? "最近一次整理后暂无补充说明"} · 最近更新于 {formatUpdatedTime(record.updated_at)}
                      </p>
                      <div className={styles.actions}>
                        <button type="button" onClick={() => startEdit(record)}>
                          修改
                        </button>
                        <button
                          type="button"
                          className={styles.withdraw}
                          disabled={busyId === record.record_id}
                          onClick={() => void withdraw(record)}
                        >
                          撤回
                        </button>
                        <button
                          type="button"
                          className={styles.delete}
                          disabled={busyId === record.record_id}
                          onClick={() => void remove(record)}
                        >
                          删除
                        </button>
                      </div>
                    </article>
                  ))}
                </div>
              )}
            </section>
          );
        })}
      </div>

      {editing ? (
        <div className={styles.dialogBackdrop} role="presentation">
          <section
            className={styles.dialog}
            role="dialog"
            aria-modal="true"
            aria-labelledby="four-dimension-edit-title"
          >
            <h2 id="four-dimension-edit-title">修改{editing.label}</h2>
            <textarea
              value={draft}
              maxLength={1000}
              onChange={(event) => setDraft(event.target.value)}
              aria-label="信息内容"
              rows={5}
            />
            <div className={styles.dialogActions}>
              <button type="button" onClick={() => setEditing(null)}>
                取消
              </button>
              <button
                type="button"
                className={styles.primary}
                disabled={!draft.trim() || busyId === editing.record_id}
                onClick={() => void saveEdit()}
              >
                保存修改
              </button>
            </div>
          </section>
        </div>
      ) : null}
    </main>
  );
}
