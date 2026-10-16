"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import {
  deleteAtomicProfileItem,
  fetchAtomicProfileItemEvidence,
  modifyAtomicProfileItem,
  submitAtomicProfileItemFeedback,
  type AtomicProfileEvidenceSource,
  type AtomicProfileFeedbackKind,
  type AtomicProfileItemProjection,
} from "@/lib/api";

import {
  EvidencePanel,
  type EvidenceEntry,
  type FeedbackNotice,
} from "./EvidencePanel";
import {
  DELETE_CONFIRM,
  ORIGIN_LABELS,
  formatUpdatedTime,
  parseTopics,
  sourceLabel,
  topicsToInput,
} from "./profile-format";
import styles from "./AtomicProfileCenter.module.css";

type Props = {
  item: AtomicProfileItemProjection;
  /** 概览陈述点击后由容器定位到本条：展开依据、聚焦并高亮。 */
  located: boolean;
  /** 每次定位事件的单调序号；相同条目再次被点击时也能重新定位。 */
  locateNonce: number;
  onItemUpdated: (item: AtomicProfileItemProjection) => void;
  onItemDeleted: (itemId: string) => void;
  onRefresh: () => Promise<void>;
  onError: (message: string) => void;
};

/**
 * V2 Issue 08：一条原子长期信息的行内交互。
 *
 * 修改在本行内完成，保存或取消都在原地；删除先经确认，成功后从列表移除
 * （后台写入删除墓碑，旧消息重放不会复活）。行内始终可见「修改」与「删除」；
 * 「查看依据」按需展开真实原话、来源、范围与时效，并可提交四类反馈。
 * 概览点击定位到本条时：展开依据、滚动到视口内并聚焦本行。
 */
export function AtomicProfileItemRow({
  item,
  located,
  locateNonce,
  onItemUpdated,
  onItemDeleted,
  onRefresh,
  onError,
}: Props) {
  const router = useRouter();
  const rowRef = useRef<HTMLLIElement | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [draftTopics, setDraftTopics] = useState("");
  const [draftPurpose, setDraftPurpose] = useState("");
  const [busy, setBusy] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [evidence, setEvidence] = useState<EvidenceEntry | null>(null);
  const evidenceRequest = useRef(0);
  const [feedbackBusy, setFeedbackBusy] = useState<string | null>(null);
  const [feedbackNotice, setFeedbackNotice] = useState<FeedbackNotice | null>(
    null
  );

  const loadEvidence = async () => {
    const request = evidenceRequest.current + 1;
    evidenceRequest.current = request;
    setEvidence({ status: "loading" });
    try {
      const data = await fetchAtomicProfileItemEvidence(item.profile_item_id);
      if (evidenceRequest.current !== request) return;
      setEvidence({ status: "ready", data });
    } catch (reason) {
      if (evidenceRequest.current !== request) return;
      setEvidence({
        status: "error",
        message:
          reason instanceof Error
            ? reason.message
            : "这条信息的依据暂时无法加载，请重试。",
      });
    }
  };

  useEffect(() => {
    if (!located) return;
    setEditing(false);
    setExpanded(true);
    setFeedbackNotice(null);
    // 每次定位都重新读取：来源可能在两次定位之间被删除或变得不可读。
    void loadEvidence();
    const node = rowRef.current;
    if (node) {
      // 概览在页面上方，条目在下方：滚动到视口中央并聚焦本行，
      // 键盘与辅助技术用户随后的操作目标就是这条信息。
      node.scrollIntoView?.({ block: "center" });
      node.focus?.();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [locateNonce, located]);

  const startEdit = () => {
    setEditing(true);
    setDraft(item.text);
    setDraftTopics(topicsToInput(item.topics));
    setDraftPurpose(item.usage_purpose ?? "");
    if (expanded) {
      evidenceRequest.current += 1;
      setExpanded(false);
      setEvidence(null);
    }
  };

  const cancelEdit = () => {
    setEditing(false);
    setDraft("");
    setDraftTopics("");
    setDraftPurpose("");
  };

  const saveEdit = async () => {
    const text = draft.trim();
    if (!text || busy) return;
    try {
      setBusy(true);
      // 用途：空输入即清除；清除后敏感自述不再进入回答上下文。
      const updated = await modifyAtomicProfileItem(item.profile_item_id, {
        text,
        version: item.version,
        topics: parseTopics(draftTopics),
        usage_purpose: draftPurpose.trim(),
      });
      onItemUpdated(updated);
      evidenceRequest.current += 1;
      setEvidence(null);
      setExpanded(false);
      cancelEdit();
    } catch (reason) {
      // 版本冲突说明页面读到的是旧快照：先重新拉取，再如实报错，让用户在新
      // 版本上重试（重新加载成功不掩盖这条提示）。
      await onRefresh();
      onError(
        reason instanceof Error ? reason.message : "长期信息修改失败，请重试。"
      );
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!window.confirm(DELETE_CONFIRM)) return;
    try {
      setBusy(true);
      await deleteAtomicProfileItem(item.profile_item_id, item.version);
      onItemDeleted(item.profile_item_id);
    } catch (reason) {
      await onRefresh();
      onError(
        reason instanceof Error ? reason.message : "长期信息删除失败，请重试。"
      );
    } finally {
      setBusy(false);
    }
  };

  const toggleEvidence = async () => {
    if (expanded) {
      evidenceRequest.current += 1;
      setExpanded(false);
      setEvidence(null);
      return;
    }
    setExpanded(true);
    setFeedbackNotice(null);
    // 每次展开都重新读取：来源可能在两次展开之间被删除或变得不可读。
    await loadEvidence();
  };

  const submitFeedback = async (kind: AtomicProfileFeedbackKind) => {
    setFeedbackBusy(`${item.profile_item_id}:${kind}`);
    setFeedbackNotice(null);
    try {
      const result = await submitAtomicProfileItemFeedback(
        item.profile_item_id,
        { kind }
      );
      // 反馈落库后重读，避免重展开的旧快照丢掉已提交反馈。
      await loadEvidence();
      setEvidence((previous) => {
        if (previous?.status !== "ready") return previous;
        const remaining = (previous.data.feedback ?? []).filter(
          (existing) => existing.kind !== result.kind
        );
        return {
          status: "ready",
          data: { ...previous.data, feedback: [result, ...remaining] },
        };
      });
      setFeedbackNotice({
        itemId: item.profile_item_id,
        text: result.message,
        ok: true,
      });
    } catch (reason) {
      setFeedbackNotice({
        itemId: item.profile_item_id,
        text:
          reason instanceof Error ? reason.message : "反馈提交失败，请重试。",
        ok: false,
      });
    } finally {
      setFeedbackBusy(null);
    }
  };

  const openSource = (source: AtomicProfileEvidenceSource) => {
    if (!source.conversation_id) return;
    router.push(
      `/chat/${encodeURIComponent(source.conversation_id)}?message=${encodeURIComponent(
        source.message_id
      )}`
    );
  };

  const source = sourceLabel(item);

  return (
    <li
      ref={rowRef}
      id={`atomic-item-${item.profile_item_id}`}
      className={styles.item}
      tabIndex={-1}
      data-testid="atomic-item"
      data-located={located ? "true" : undefined}
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
          <input
            className={styles.topicsInput}
            value={draftTopics}
            maxLength={200}
            aria-label="长期信息主题"
            placeholder="主题（可选，用逗号分隔；清空表示无主题）"
            onChange={(event) => setDraftTopics(event.target.value)}
          />
          <input
            className={styles.topicsInput}
            value={draftPurpose}
            maxLength={60}
            aria-label="长期信息用途"
            placeholder="用途（可选；清空表示不设用途，敏感信息将不再用于回答）"
            onChange={(event) => setDraftPurpose(event.target.value)}
          />
          <div className={styles.actions}>
            <button type="button" onClick={cancelEdit} disabled={busy}>
              取消
            </button>
            <button
              type="button"
              className={styles.primary}
              disabled={!draft.trim() || busy}
              onClick={() => void saveEdit()}
            >
              保存
            </button>
          </div>
        </>
      ) : (
        <>
          <p className={styles.text}>{item.text}</p>
          {item.conflict_state === "unresolved" ? (
            // 冲突标记：与同属性的另一条说法不一致，确认前不作确定
            // 信息使用；用户直接修改本条或删除其一即可消除。
            <p
              className={styles.conflict}
              data-testid="atomic-conflict-badge"
              role="status"
            >
              待确认：还有一条说法与此不一致。确认之前，这部分不会作为确定信息使用；
              你可以直接修改成正确的说法，或删除不需要的那条。
            </p>
          ) : null}
          <p className={styles.meta}>
            {ORIGIN_LABELS[item.write_origin]}
            {source ? ` · ${source}` : ""}
            {item.user_edited_at ? " · 已由你修改" : ""} · 最近更新于{" "}
            {formatUpdatedTime(item.updated_at)}
          </p>
          {(item.topics ?? []).length > 0 ? (
            <ul className={styles.topics} data-testid="atomic-topics">
              {(item.topics ?? []).map((topic) => (
                <li key={topic} className={styles.topic}>
                  {topic}
                </li>
              ))}
            </ul>
          ) : null}
          {item.usage_purpose ? (
            // 工单 09：用途是敏感自述的采用范围；清空后不再进入回答。
            <p className={styles.purpose} data-testid="atomic-usage-purpose">
              用途：{item.usage_purpose}
            </p>
          ) : null}
          <div className={styles.actions}>
            <button
              type="button"
              aria-expanded={expanded}
              aria-controls={`atomic-evidence-${item.profile_item_id}`}
              onClick={() => void toggleEvidence()}
            >
              {expanded ? "收起依据" : "查看依据"}
            </button>
            <button type="button" onClick={startEdit}>
              修改
            </button>
            <button
              type="button"
              className={styles.delete}
              disabled={busy}
              onClick={() => void remove()}
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
              <EvidencePanel
                item={item}
                entry={evidence ?? { status: "loading" }}
                feedbackBusy={feedbackBusy}
                feedbackNotice={feedbackNotice}
                onRetry={() => void loadEvidence()}
                onFeedback={(kind) => void submitFeedback(kind)}
                onOpenSource={openSource}
              />
            </div>
          ) : null}
        </>
      )}
    </li>
  );
}
