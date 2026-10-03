"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import {
  deleteAtomicProfileItem,
  fetchAtomicProfileItemEvidence,
  listAtomicProfileItems,
  modifyAtomicProfileItem,
  submitAtomicProfileItemFeedback,
  type AtomicProfileEvidenceSource,
  type AtomicProfileFeedbackKind,
  type AtomicProfileItemEvidenceProjection,
  type AtomicProfileItemProjection,
  type AtomicProfileValidityStatus,
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

const FEEDBACK_OPTIONS: Array<{
  kind: AtomicProfileFeedbackKind;
  label: string;
}> = [
  { kind: "fact_wrong", label: "事实记错" },
  { kind: "expired", label: "信息过期" },
  { kind: "scope_inapplicable", label: "范围不适用" },
  { kind: "preference_not_followed", label: "回答没执行偏好" },
];

const FEEDBACK_LABELS = Object.fromEntries(
  FEEDBACK_OPTIONS.map(({ kind, label }) => [kind, label])
) as Record<AtomicProfileFeedbackKind, string>;

const VALIDITY_LABELS: Record<AtomicProfileValidityStatus, string> = {
  unbounded: "没有明确期限",
  scheduled: "尚未生效",
  active: "当前有效",
  expired: "已过期",
  paused: "目标已暂停",
  completed: "目标已完成",
};

const SCOPE_LABELS = {
  long_term: "长期适用",
  current: "仅提出当时适用",
} as const;

type EvidenceEntry =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: AtomicProfileItemEvidenceProjection };

function formatUpdatedTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "long",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatMoment(value: string | null | undefined): string | null {
  if (!value) return null;
  return formatUpdatedTime(value);
}

/** 来源标注：只说明有几条对话来源，可得性在展开后逐条如实展示。 */
function sourceLabel(item: AtomicProfileItemProjection): string | null {
  const count = item.source_message_ids?.length ?? 0;
  if (count === 0) return null;
  return `有 ${count} 条对话来源，展开可核对`;
}

function sourceStatusText(source: AtomicProfileEvidenceSource): string {
  const moment = formatMoment(source.created_at);
  if (source.status === "available") {
    return moment ? `来自 ${moment} 的对话` : "来源消息可查看";
  }
  if (source.status === "deleted") {
    return "这条来源消息已删除，无法再定位原文。";
  }
  return moment
    ? `这条来源发生于 ${moment}，但消息未完成或无法读取。`
    : "这条来源暂时无法读取。";
}

function validityText(evidence: AtomicProfileItemEvidenceProjection): string {
  const status = VALIDITY_LABELS[evidence.validity_status];
  if (evidence.validity_status === "unbounded") {
    // R01：未明示期限按未知处理，不承诺长期有效；据此制定计划前先确认。
    return "没有明确期限（系统不设统一过期时间；据此制定具体计划前会先与你确认）。";
  }
  const range: string[] = [];
  const from = formatMoment(evidence.valid_from);
  const until = formatMoment(evidence.valid_until);
  if (from) range.push(`从 ${from} 起`);
  if (until) range.push(`到 ${until} 止`);
  const phrase = evidence.validity_phrase
    ? `原文说「${evidence.validity_phrase}」：`
    : "";
  return `${phrase}${range.join("，")}（${status}）`;
}

/**
 * V2 Issue 08：无固定类别的原子长期信息列表。
 *
 * 每行始终可见「修改」与「删除」：修改在本行内完成，保存或取消都在原地；
 * 删除先经确认，成功后从列表移除（后台写入删除墓碑，旧消息重放不会复活）。
 * 列表不分组、不展示类别与把握度，空态说明提取边界。
 *
 * 改进工单 20：每行增加可聚焦的「查看依据」展开入口，按需拉取该条的真实
 * 原话、来源时间与定位、适用范围、明确期限/状态，并可提交四类反馈。来源
 * 已删除或不可读时如实说明，不伪造引语；反馈绝不自动删除仍正确的事实；
 * 修改/删除不随展开状态隐藏，也不增加逐条授权确认弹窗。
 */
export function AtomicProfileCenter() {
  const router = useRouter();
  const [items, setItems] = useState<AtomicProfileItemProjection[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [evidence, setEvidence] = useState<Record<string, EvidenceEntry>>({});
  const evidenceRequests = useRef<Record<string, number>>({});
  const [feedbackBusy, setFeedbackBusy] = useState<string | null>(null);
  const [feedbackNotice, setFeedbackNotice] = useState<{
    itemId: string;
    text: string;
    ok: boolean;
  } | null>(null);

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

  const clearEvidence = (itemId: string) => {
    evidenceRequests.current[itemId] = (evidenceRequests.current[itemId] ?? 0) + 1;
    setEvidence((previous) => {
      if (!(itemId in previous)) return previous;
      const next = { ...previous };
      delete next[itemId];
      return next;
    });
  };

  const loadEvidence = async (itemId: string) => {
    const request = (evidenceRequests.current[itemId] ?? 0) + 1;
    evidenceRequests.current[itemId] = request;
    setEvidence((previous) => ({ ...previous, [itemId]: { status: "loading" } }));
    try {
      const data = await fetchAtomicProfileItemEvidence(itemId);
      if (evidenceRequests.current[itemId] !== request) return;
      setEvidence((previous) => ({
        ...previous,
        [itemId]: { status: "ready", data },
      }));
    } catch (reason) {
      if (evidenceRequests.current[itemId] !== request) return;
      setEvidence((previous) => ({
        ...previous,
        [itemId]: {
          status: "error",
          message:
            reason instanceof Error
              ? reason.message
              : "这条信息的依据暂时无法加载，请重试。",
        },
      }));
    }
  };

  const toggleEvidence = async (item: AtomicProfileItemProjection) => {
    const itemId = item.profile_item_id;
    if (expandedId === itemId) {
      clearEvidence(itemId);
      setExpandedId(null);
      return;
    }
    setExpandedId(itemId);
    setFeedbackNotice(null);
    // 每次展开都重新读取：来源可能在两次展开之间被删除或变得不可读。
    await loadEvidence(itemId);
  };

  const retryEvidence = (itemId: string) => {
    void loadEvidence(itemId);
  };

  const startEdit = (item: AtomicProfileItemProjection) => {
    setError(null);
    setEditingId(item.profile_item_id);
    setDraft(item.text);
    if (expandedId === item.profile_item_id) setExpandedId(null);
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
      clearEvidence(updated.profile_item_id);
      cancelEdit();
    } catch (reason) {
      // 版本冲突说明页面读到的是旧快照：先重新拉取，再如实报错，让用户在新
      // 版本上重试（重新加载成功不掩盖这条提示）。
      await load();
      clearEvidence(editingId);
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
      clearEvidence(item.profile_item_id);
      if (editingId === item.profile_item_id) cancelEdit();
      if (expandedId === item.profile_item_id) setExpandedId(null);
    } catch (reason) {
      await load();
      setError(
        reason instanceof Error ? reason.message : "长期信息删除失败，请重试。"
      );
    } finally {
      setBusyId(null);
    }
  };

  const submitFeedback = async (
    itemId: string,
    kind: AtomicProfileFeedbackKind
  ) => {
    setFeedbackBusy(`${itemId}:${kind}`);
    setFeedbackNotice(null);
    try {
      const result = await submitAtomicProfileItemFeedback(itemId, { kind });
      // 反馈落库后重读，避免重展开的旧快照丢掉已提交反馈。
      await loadEvidence(itemId);
      setEvidence((previous) => {
        const entry = previous[itemId];
        if (entry?.status !== "ready") return previous;
        const remaining = (entry.data.feedback ?? []).filter(
          (existing) => existing.kind !== result.kind
        );
        return {
          ...previous,
          [itemId]: {
            status: "ready",
            data: { ...entry.data, feedback: [result, ...remaining] },
          },
        };
      });
      setFeedbackNotice({ itemId, text: result.message, ok: true });
    } catch (reason) {
      setFeedbackNotice({
        itemId,
        text:
          reason instanceof Error
            ? reason.message
            : "反馈提交失败，请重试。",
        ok: false,
      });
    } finally {
      setFeedbackBusy(null);
    }
  };

  const renderEvidence = (item: AtomicProfileItemProjection, entry: EvidenceEntry) => {
    if (entry.status === "loading") {
      return (
        <p className={styles.state} data-testid="atomic-evidence-loading">
          正在加载这条信息的依据…
        </p>
      );
    }
    if (entry.status === "error") {
      return (
        <div className={styles.evidenceError} role="alert">
          <span>{entry.message}</span>
          <button type="button" onClick={() => retryEvidence(item.profile_item_id)}>
            重试
          </button>
        </div>
      );
    }
    const data = entry.data;
    const sources = data.sources ?? [];
    const feedback = data.feedback ?? [];
    const hasReadableSource = sources.some(
      (source) => source.status === "available"
    );
    // 合同要求删除聊天原文时同步失效派生原话副本：没有可读来源就不展示原话，
    // 即使服务端仍带着保存时的记录（防止回归时把已删内容当成现行依据）。
    const quoteAvailable =
      Boolean(data.evidence_quote) &&
      data.evidence_quote_status === "recorded" &&
      hasReadableSource;
    const quoteInvalidated =
      data.evidence_quote_status === "source_unavailable" ||
      (Boolean(data.evidence_quote) && !quoteAvailable);
    return (
      <div className={styles.evidenceBody}>
        <div className={styles.evidenceSection}>
          <p className={styles.evidenceLabel}>原话依据</p>
          {quoteAvailable ? (
            <blockquote className={styles.quote}>
              {data.evidence_quote}
            </blockquote>
          ) : quoteInvalidated ? (
            <p className={styles.evidenceNote}>
              来源消息已删除或不可读，保存的原话不再展示。
            </p>
          ) : (
            <p className={styles.evidenceNote}>
              这条信息没有保存聊天原话（旧记录，或正文由你手动修改过）。
            </p>
          )}
        </div>

        <div className={styles.evidenceSection}>
          <p className={styles.evidenceLabel}>来源</p>
          {sources.length === 0 ? (
            <p className={styles.evidenceNote}>
              没有可定位的对话来源；这条信息可能由你在聊天中直接记住。
            </p>
          ) : (
            <ul className={styles.sourceList}>
              {sources.map((source) => (
                <li key={source.message_id} className={styles.sourceItem}>
                  <span>{sourceStatusText(source)}</span>
                  {source.status === "available" && source.conversation_id ? (
                    <button
                      type="button"
                      onClick={() =>
                        router.push(
                          `/chat/${encodeURIComponent(
                            source.conversation_id as string
                          )}?message=${encodeURIComponent(source.message_id)}`
                        )
                      }
                    >
                      查看原文
                    </button>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className={styles.evidenceSection}>
          <p className={styles.evidenceLabel}>范围与时效</p>
          <p className={styles.evidenceNote}>
            适用范围：{SCOPE_LABELS[data.fact_scope]}。
          </p>
          <p className={styles.evidenceNote}>
            有效期：{validityText(data)}
          </p>
        </div>

        <div className={styles.evidenceSection}>
          <p className={styles.evidenceLabel}>反馈这条信息</p>
          <div className={styles.feedbackActions}>
            {FEEDBACK_OPTIONS.map(({ kind, label }) => (
              <button
                key={kind}
                type="button"
                disabled={feedbackBusy === `${item.profile_item_id}:${kind}`}
                onClick={() => void submitFeedback(item.profile_item_id, kind)}
              >
                {label}
              </button>
            ))}
          </div>
          <p className={styles.evidenceNote}>
            反馈只记录问题类型；信息是否删除或修改仍由你决定，系统不会自动删除。
          </p>
          {feedbackNotice?.itemId === item.profile_item_id ? (
            <p
              className={
                feedbackNotice.ok ? styles.feedbackNotice : styles.feedbackError
              }
              role={feedbackNotice.ok ? "status" : "alert"}
              data-testid="atomic-feedback-notice"
            >
              {feedbackNotice.text}
            </p>
          ) : null}
          {feedback.length > 0 ? (
            <ul className={styles.feedbackList}>
              {feedback.map((entryFeedback) => (
                <li key={entryFeedback.feedback_id}>
                  已反馈「{FEEDBACK_LABELS[entryFeedback.kind]}」 ·{" "}
                  {formatUpdatedTime(entryFeedback.created_at)}：
                  {entryFeedback.message}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      </div>
    );
  };

  return (
    <section className={styles.page} aria-labelledby="atomic-profile-title">
      <header className={styles.header}>
        <p className={styles.eyebrow}>关于你的信息</p>
        <h1 id="atomic-profile-title">你的信息</h1>
        <p className={styles.lead}>
          这些是系统从对话里整理出的长期信息，按条列出。你可以逐条修改或删除，
          你的修改优先于自动整理；展开某条可以查看它的原话依据、来源与有效期。
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
            const expanded = expandedId === item.profile_item_id;
            const source = sourceLabel(item);
            const entry = evidence[item.profile_item_id];
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
                      {source ? ` · ${source}` : ""}
                      {item.user_edited_at ? " · 已由你修改" : ""} · 最近更新于{" "}
                      {formatUpdatedTime(item.updated_at)}
                    </p>
                    <div className={styles.actions}>
                      <button
                        type="button"
                        aria-expanded={expanded}
                        aria-controls={`atomic-evidence-${item.profile_item_id}`}
                        onClick={() => void toggleEvidence(item)}
                      >
                        {expanded ? "收起依据" : "查看依据"}
                      </button>
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
                    {expanded ? (
                      <div
                        className={styles.evidence}
                        id={`atomic-evidence-${item.profile_item_id}`}
                        role="region"
                        aria-label="这条信息的依据"
                        data-testid="atomic-evidence"
                      >
                        {entry
                          ? renderEvidence(item, entry)
                          : renderEvidence(item, { status: "loading" })}
                      </div>
                    ) : null}
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
