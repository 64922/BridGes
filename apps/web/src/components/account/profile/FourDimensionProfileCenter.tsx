"use client";

import { useEffect, useMemo, useState } from "react";

import {
  listFourDimensionProfileRecords,
  modifyFourDimensionProfileRecord,
  withdrawFourDimensionProfileRecord,
  type FourDimension,
  type FourDimensionProfileRecord,
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

/** Issue 14：只显示四类画像内容和首次稳定记录时间。 */
export function FourDimensionProfileCenter() {
  const [records, setRecords] = useState<FourDimensionProfileRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<FourDimensionProfileRecord | null>(null);
  const [draft, setDraft] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = async () => {
    try {
      setError(null);
      setRecords(await listFourDimensionProfileRecords());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "画像暂时无法加载，请重试。");
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
      setError(reason instanceof Error ? reason.message : "画像修改失败，请刷新后重试。");
    } finally {
      setBusyId(null);
    }
  };

  const withdraw = async (record: FourDimensionProfileRecord) => {
    if (!window.confirm("撤回后这条画像将停止使用，但会保留内部记录。继续吗？")) return;
    try {
      setBusyId(record.record_id);
      await withdrawFourDimensionProfileRecord(record.record_id, record.version);
      setRecords((current) =>
        current?.filter((candidate) => candidate.record_id !== record.record_id) ?? current
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "画像撤回失败，请刷新后重试。");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <main className={styles.page} aria-labelledby="four-dimension-profile-title">
      <header className={styles.header}>
        <p className={styles.eyebrow}>用户画像</p>
        <h1 id="four-dimension-profile-title">四维画像</h1>
        <p className={styles.intro}>
          这里只展示你确认过的四类信息。首次记录时间不会因修改而改变；撤回后记录将停止使用。
        </p>
      </header>

      {error ? (
        <div className={styles.error} role="alert">
          <span>{error}</span>
          <button type="button" onClick={() => void load()}>
            重试
          </button>
        </div>
      ) : null}

      {records === null && !error ? <p className={styles.state}>正在加载画像…</p> : null}

      <div className={styles.groups}>
        {DIMENSIONS.map((dimension) => {
          const dimensionRecords = grouped.get(dimension.value) ?? [];
          return (
            <section className={styles.group} key={dimension.value}>
              <div className={styles.groupHeader}>
                <h2>{dimension.label}</h2>
                <span>{dimensionRecords.length} 条</span>
              </div>
              {dimensionRecords.length === 0 ? (
                <p className={styles.empty}>暂时没有已确认的记录</p>
              ) : (
                <div className={styles.records}>
                  {dimensionRecords.map((record) => (
                    <article className={styles.record} key={record.record_id}>
                      <p className={styles.content}>{record.content}</p>
                      <p className={styles.time}>
                        首次记录于 {formatStableTime(record.first_stable_recorded_at)}
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
              aria-label="画像内容"
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
