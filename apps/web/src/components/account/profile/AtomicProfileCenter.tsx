"use client";

import { useEffect, useState } from "react";

import {
  deleteAtomicProfileItem,
  listAtomicProfileItems,
  modifyAtomicProfileItem,
  type AtomicProfileItemProjection,
  type AtomicProfileWriteOrigin,
} from "@/lib/api";

import styles from "./AtomicProfileCenter.module.css";

const ORIGIN_LABELS: Record<AtomicProfileWriteOrigin, string> = {
  automatic: "从对话里整理",
  user: "你手动记住",
  migration: "从旧列表迁移",
};

const EMPTY_COPY =
  "这里还没有长期信息。系统只从你明确说过、能引用原话的消息里整理稳定事实，" +
  "不会推断人格或心理状态；聊到相关内容的下一轮，也会按任务需要少量取用。";

const DELETE_CONFIRM =
  "删除后这条信息会从后续对话的上下文里移除，旧消息重放也不会让它回来。继续吗？";

function formatUpdatedTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "long",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

/**
 * V2 Issue 08：无固定类别的原子长期信息列表。
 *
 * 每行始终可见「修改」与「删除」：修改在本行内完成，保存或取消都在原地；
 * 删除先经确认，成功后从列表移除（后台写入删除墓碑，旧消息重放不会复活）。
 * 列表不分组、不展示类别与把握度，空态说明提取边界。
 */
export function AtomicProfileCenter() {
  const [items, setItems] = useState<AtomicProfileItemProjection[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = async () => {
    try {
      setItems(await listAtomicProfileItems());
      setError(null);
    } catch (reason) {
      setItems(null);
      setError(
        reason instanceof Error ? reason.message : "长期信息暂时无法加载，请重试。"
      );
    }
  };

  const retry = () => {
    setError(null);
    void load();
  };

  useEffect(() => {
    void load();
  }, []);

  const startEdit = (item: AtomicProfileItemProjection) => {
    setError(null);
    setEditingId(item.profile_item_id);
    setDraft(item.text);
  };

  const cancelEdit = () => {
    setEditingId(null);
    setDraft("");
  };

  const saveEdit = async () => {
    if (editingId === null) return;
    const text = draft.trim();
    if (!text) return;
    const current = items?.find((item) => item.profile_item_id === editingId);
    if (current === undefined) return;
    try {
      setBusyId(editingId);
      const updated = await modifyAtomicProfileItem(editingId, {
        text,
        version: current.version,
      });
      setItems(
        (previous) =>
          previous?.map((item) =>
            item.profile_item_id === updated.profile_item_id ? updated : item
          ) ?? previous
      );
      cancelEdit();
    } catch (reason) {
      // 版本冲突说明页面读到的是旧快照：先重新拉取，再如实报错，让用户在新
      // 版本上重试（重新加载成功不掩盖这条提示）。
      await load();
      setError(
        reason instanceof Error ? reason.message : "长期信息修改失败，请重试。"
      );
    } finally {
      setBusyId(null);
    }
  };

  const remove = async (item: AtomicProfileItemProjection) => {
    if (!window.confirm(DELETE_CONFIRM)) return;
    try {
      setBusyId(item.profile_item_id);
      await deleteAtomicProfileItem(item.profile_item_id, item.version);
      setItems(
        (previous) =>
          previous?.filter(
            (candidate) => candidate.profile_item_id !== item.profile_item_id
          ) ?? previous
      );
      if (editingId === item.profile_item_id) cancelEdit();
    } catch (reason) {
      await load();
      setError(
        reason instanceof Error ? reason.message : "长期信息删除失败，请重试。"
      );
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section className={styles.page} aria-labelledby="atomic-profile-title">
      <header className={styles.header}>
        <p className={styles.eyebrow}>关于你的信息</p>
        <h1 id="atomic-profile-title">你的信息</h1>
        <p className={styles.lead}>
          这些是系统从对话里整理出的长期信息，按条列出。你可以逐条修改或删除，
          你的修改优先于自动整理。
        </p>
      </header>

      {error ? (
        <div className={styles.error} role="alert">
          <span>{error}</span>
          <button type="button" onClick={retry}>
            重试
          </button>
        </div>
      ) : null}

      {items === null && !error ? (
        <p className={styles.state} data-testid="atomic-loading">
          正在加载信息…
        </p>
      ) : null}

      {items !== null && items.length === 0 ? (
        <p className={styles.empty} data-testid="atomic-empty">
          {EMPTY_COPY}
        </p>
      ) : null}

      {items !== null && items.length > 0 ? (
        <ul className={styles.items}>
          {items.map((item) => {
            const editing = editingId === item.profile_item_id;
            return (
              <li
                className={styles.item}
                key={item.profile_item_id}
                data-testid="atomic-item"
              >
                {editing ? (
                  <>
                    <textarea
                      className={styles.editor}
                      value={draft}
                      maxLength={1000}
                      rows={3}
                      aria-label="长期信息内容"
                      onChange={(event) => setDraft(event.target.value)}
                    />
                    <div className={styles.actions}>
                      <button
                        type="button"
                        onClick={cancelEdit}
                        disabled={busyId === item.profile_item_id}
                      >
                        取消
                      </button>
                      <button
                        type="button"
                        className={styles.primary}
                        disabled={!draft.trim() || busyId === item.profile_item_id}
                        onClick={() => void saveEdit()}
                      >
                        保存
                      </button>
                    </div>
                  </>
                ) : (
                  <>
                    <p className={styles.text}>{item.text}</p>
                    <p className={styles.meta}>
                      {ORIGIN_LABELS[item.write_origin]}
                      {item.user_edited_at ? " · 已由你修改" : ""} · 最近更新于{" "}
                      {formatUpdatedTime(item.updated_at)}
                    </p>
                    <div className={styles.actions}>
                      <button type="button" onClick={() => startEdit(item)}>
                        修改
                      </button>
                      <button
                        type="button"
                        className={styles.delete}
                        disabled={busyId === item.profile_item_id}
                        onClick={() => void remove(item)}
                      >
                        删除
                      </button>
                    </div>
                  </>
                )}
              </li>
            );
          })}
        </ul>
      ) : null}
    </section>
  );
}
