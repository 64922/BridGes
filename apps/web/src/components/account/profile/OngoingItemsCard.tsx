"use client";

import { useEffect, useState } from "react";

import {
  fetchOngoingItems,
  updateOngoingItemState,
  type OngoingEntryProjection,
  type OngoingGoalAction,
  type OngoingItemProjection,
  type OngoingItemsProjection,
} from "@/lib/api";

import styles from "./OngoingItemsCard.module.css";

const GOAL_STATE_LABELS = {
  active: "进行中",
  paused: "已暂停",
  completed: "已完成",
} as const;

const ACTION_NOTICES: Record<OngoingGoalAction, string> = {
  pause: "已暂停",
  complete: "已完成",
  resume: "已继续",
};

const COMPLETE_CONFIRM =
  "完成后这件事不再出现在进行中事项里；相关记录仍保留在「你的信息」列表中。继续吗？";

const EMPTY_COPY =
  "现在没有进行中的事项。当你在对话里明确提到正在准备或进行的目标" +
  "（例如「我在准备考研英语」），它会出现在这里；之后的进展与待续问题" +
  "会自动归到这件事上，之后在对话里再提到这件事即可接着它的进展。";

function formatUpdatedTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "long",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

/** 来源标注：只说明有几条对话来源，不复制原文；详情在「你的信息」中展开核对。 */
function sourceHint(entry: OngoingEntryProjection): string {
  const count = entry.source_message_ids?.length ?? 0;
  if (count === 0) return "还没有可核对的对话来源";
  return `有 ${count} 条对话来源，可在下方「你的信息」里展开核对`;
}

function EntryList({
  label,
  entries,
  testId,
}: {
  label: string;
  entries: OngoingEntryProjection[];
  testId: string;
}) {
  if (entries.length === 0) return null;
  return (
    <div className={styles.entrySection}>
      <p className={styles.entryLabel}>{label}</p>
      <ul className={styles.entries} data-testid={testId}>
        {entries.map((entry) => (
          <li key={entry.profile_item_id} className={styles.entry}>
            <p className={styles.entryText}>{entry.text}</p>
            <p className={styles.entryMeta}>{sourceHint(entry)}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * 画像工单 13：进行中事项区。
 *
 * 列出仍在进行（或已暂停）的目标、实际进展与待续问题；暂停、完成与继续
 * 作用于精确定位的这一件事，完成后从事项区消失（记录保留）。待续问题都
 * 能回到真实交流来源；领域状态由所属领域实时解析，未接通时如实说明。
 * 加载、空态、失败重试与操作结果都有明确反馈；操作按钮均为键盘可达的
 * 原生按钮。
 */
export function OngoingItemsCard() {
  const [data, setData] = useState<OngoingItemsProjection | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = async () => {
    try {
      setData(await fetchOngoingItems());
      setError(null);
    } catch (reason) {
      setData(null);
      setError(
        reason instanceof Error ? reason.message : "进行中事项暂时无法加载，请重试。"
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

  const runAction = async (item: OngoingItemProjection, action: OngoingGoalAction) => {
    if (action === "complete" && !window.confirm(COMPLETE_CONFIRM)) return;
    const goalId = item.goal.profile_item_id;
    // 画像工单 14：关联学习等领域的目标由其领域执有状态；把页面读到的
    // 领域对象版本一并交给后端做跨会话并发守卫（未接通领域时不伪造版本）。
    const referenceVersion = (item.domain_references ?? []).find(
      (reference) => reference.resolved && reference.version != null
    )?.version;
    try {
      setBusyId(goalId);
      setError(null);
      const next = await updateOngoingItemState(goalId, {
        action,
        version: item.goal.version,
        ...(referenceVersion != null
          ? { reference_version: referenceVersion }
          : {}),
      });
      setData(next);
      setNotice(`「${item.goal.text}」${ACTION_NOTICES[action]}。`);
    } catch (reason) {
      // 版本冲突等失败：先重读真实状态，再如实报错，不假装操作成功。
      await load();
      setNotice(null);
      setError(
        reason instanceof Error ? reason.message : "操作未能保存，请重试。"
      );
    } finally {
      setBusyId(null);
    }
  };

  const listItems = data?.items ?? [];

  return (
    <section
      className={styles.card}
      aria-labelledby="ongoing-items-title"
      data-testid="ongoing-card"
    >
      <h2 id="ongoing-items-title" className={styles.title}>
        进行中事项
      </h2>
      <p className={styles.lead}>
        这些是你明确说过正在准备或进行的事：目标、实际进展和待续问题按事项
        成组。暂停或完成后，后续回答不再把它当作当前目标；记录都保留在
        「你的信息」里。
      </p>

      {data?.notice ? (
        <p className={styles.usageNotice} role="status" data-testid="ongoing-usage-notice">
          {data.notice}
        </p>
      ) : null}

      {notice ? (
        <p className={styles.actionNotice} role="status" data-testid="ongoing-action-notice">
          {notice}
        </p>
      ) : null}

      {error ? (
        <div className={styles.error} role="alert">
          <span>{error}</span>
          <button type="button" onClick={retry}>
            重试
          </button>
        </div>
      ) : null}

      {data === null && !error ? (
        <p className={styles.state} data-testid="ongoing-loading">
          正在加载进行中事项…
        </p>
      ) : null}

      {data !== null && listItems.length === 0 ? (
        <p className={styles.empty} data-testid="ongoing-empty">
          {EMPTY_COPY}
        </p>
      ) : null}

      {data !== null && listItems.length > 0 ? (
        <ul className={styles.items}>
          {listItems.map((item) => {
            const goal = item.goal;
            const busy = busyId === goal.profile_item_id;
            const paused = goal.goal_state === "paused";
            const domainReferences = item.domain_references ?? [];
            return (
              <li
                className={styles.item}
                key={goal.profile_item_id}
                data-testid="ongoing-item"
              >
                <div className={styles.head}>
                  <p className={styles.goal}>{goal.text}</p>
                  <span
                    className={styles.chip}
                    data-state={goal.goal_state}
                    data-testid="ongoing-goal-state"
                  >
                    {GOAL_STATE_LABELS[goal.goal_state]}
                  </span>
                </div>
                <p className={styles.meta}>
                  最近更新于 {formatUpdatedTime(goal.updated_at)}
                </p>

                {domainReferences.length > 0 ? (
                  <ul className={styles.domainRefs} data-testid="ongoing-domain">
                    {domainReferences.map((reference) => (
                      <li key={`${reference.domain}:${reference.reference_id}`}>
                        {reference.resolved
                          ? `所属领域进展：${reference.display}${
                              reference.state_code
                                ? `（${
                                    GOAL_STATE_LABELS[
                                      reference.state_code as keyof typeof GOAL_STATE_LABELS
                                    ] ?? reference.state_code
                                  }）`
                                : ""
                            }`
                          : "已关联所属领域的对象；该领域尚未接通，暂时无法显示状态。"}
                      </li>
                    ))}
                  </ul>
                ) : null}

                <EntryList
                  label="进展"
                  entries={item.progress ?? []}
                  testId="ongoing-progress"
                />
                <EntryList
                  label="待续问题"
                  entries={item.pending_questions ?? []}
                  testId="ongoing-pending"
                />

                <div className={styles.actions}>
                  {paused ? (
                    <button
                      type="button"
                      className={styles.primary}
                      disabled={busy}
                      onClick={() => void runAction(item, "resume")}
                    >
                      继续
                    </button>
                  ) : (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void runAction(item, "pause")}
                    >
                      暂停
                    </button>
                  )}
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void runAction(item, "complete")}
                  >
                    完成
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      ) : null}
    </section>
  );
}
