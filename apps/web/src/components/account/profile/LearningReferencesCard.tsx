"use client";

import { useEffect, useState } from "react";

import {
  fetchLearningReferences,
  type LearningEvidenceReference,
  type LearningGoalReference,
  type LearningReferencesProjection,
} from "@/lib/api";

import styles from "./LearningReferencesCard.module.css";

const GOAL_STATUS_LABELS: Record<LearningGoalReference["status"], string> = {
  active: "进行中",
  paused: "已暂停",
  completed: "已完成",
};

const VERDICT_LABELS: Record<LearningEvidenceReference["verdict"], string> = {
  independent_completion: "独立完成",
  hinted_completion: "在提示下完成",
  misconception: "存在误解",
  unverified: "未验证",
  undetermined: "未能判定",
};

function formatUpdatedTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "long",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function EvidenceItem({ evidence }: { evidence: LearningEvidenceReference }) {
  const corrected = evidence.state === "corrected";
  return (
    <li className={styles.entry} data-testid="learning-evidence">
      <div className={styles.entryHead}>
        <p className={styles.entryTitle}>
          {evidence.title || "未命名知识点"}
          {evidence.subject ? ` · ${evidence.subject}` : ""}
        </p>
        {evidence.verified ? (
          <span
            className={styles.chip}
            data-state="verified"
            data-testid="learning-evidence-badge"
          >
            已核验
          </span>
        ) : (
          <span
            className={styles.chip}
            data-state="unverified"
            data-testid="learning-evidence-badge"
          >
            {corrected ? "已按你的说明纠正" : "未核验"}
          </span>
        )}
      </div>
      <p className={styles.entryMeta}>
        依据实际作答的核验结论：{VERDICT_LABELS[evidence.verdict] ?? evidence.verdict}
        {evidence.pending_confirmation ? "；待进一步验证" : ""}
        {evidence.verified && !evidence.pending_confirmation ? "；仍按已核验采用" : ""}
      </p>
      {!evidence.source_available ? (
        <p className={styles.entryWarning}>
          作答来源已不可读，这条判断不再视为已核验。
        </p>
      ) : null}
      {evidence.user_note ? (
        <p className={styles.entryNote}>你的说明：{evidence.user_note}</p>
      ) : null}
      {evidence.uncertainty ? (
        <p className={styles.entryMeta}>未能确认：{evidence.uncertainty}</p>
      ) : null}
      <p className={styles.entryMeta}>
        最近更新于 {formatUpdatedTime(evidence.updated_at)}
      </p>
    </li>
  );
}

function GoalItem({ goal }: { goal: LearningGoalReference }) {
  return (
    <li className={styles.entry} data-testid="learning-goal">
      <div className={styles.entryHead}>
        <p className={styles.entryTitle}>{goal.statement}</p>
        <span className={styles.chip} data-state={goal.status}>
          {GOAL_STATUS_LABELS[goal.status] ?? goal.status}
        </span>
      </div>
      <p className={styles.entryMeta}>
        {goal.current_node_title
          ? `当前知识点：${goal.current_node_title}`
          : "学习模块还没有形成路线"}
      </p>
    </li>
  );
}

/**
 * 画像工单 14：记忆页学习引用区。
 *
 * 只读引用学习模块自己的目标与学情判断：目标是学习模块在执行的学习事项，
 * 判断来自实际作答的独立核验；用户自述（「我学过…」「我懂了」）只属于
 * 个人记忆条目，不在这里冒充核验结论。读取实时反映学习模块的纠正与删除，
 * 本卡片不提供改学习状态的操作（那是学习模块的事）。
 * 加载、空态、未接通与失败重试都有明确反馈。
 */
export function LearningReferencesCard() {
  const [data, setData] = useState<LearningReferencesProjection | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState(false);

  const load = async () => {
    try {
      setData(await fetchLearningReferences());
      setError(null);
      setUnavailable(false);
    } catch (reason) {
      setData(null);
      if ((reason as { status?: number } | null)?.status === 503) {
        setUnavailable(true);
        setError(null);
        return;
      }
      setUnavailable(false);
      setError(
        reason instanceof Error ? reason.message : "学习引用暂时无法加载，请重试。"
      );
    }
  };

  const retry = () => {
    setError(null);
    setUnavailable(false);
    void load();
  };

  useEffect(() => {
    void load();
  }, []);

  const goals = data?.goals ?? [];
  const evidences = data?.evidences ?? [];
  const isEmpty = data !== null && goals.length === 0 && evidences.length === 0;

  return (
    <section
      className={styles.card}
      aria-labelledby="learning-references-title"
      data-testid="learning-references-card"
    >
      <h2 id="learning-references-title" className={styles.title}>
        学习背景与学情判断
      </h2>
      <p className={styles.lead}>
        这里引用学习模块自己的目标和判断：目标是学习模块在执行的学习事项，
        判断来自实际作答的独立核验。你在对话里说的「学过」「懂了」属于
        上方「你的信息」里的自述条目，不在这里冒充核验结论；学习模块纠正或
        删除后，这里会立即反映。
      </p>

      {unavailable ? (
        <p className={styles.state} data-testid="learning-references-unavailable">
          学习模块尚未接通，暂时无法显示学习引用。
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

      {data === null && !error && !unavailable ? (
        <p className={styles.state} data-testid="learning-references-loading">
          正在加载学习引用…
        </p>
      ) : null}

      {isEmpty ? (
        <p className={styles.state} data-testid="learning-references-empty">
          学习模块目前还没有目标或核验记录。当你在学习对话里形成目标或
          依据实际作答得出判断后，会出现在这里。
        </p>
      ) : null}

      {data !== null && goals.length > 0 ? (
        <div className={styles.section}>
          <p className={styles.sectionLabel}>学习目标</p>
          <ul className={styles.entries} data-testid="learning-goals">
            {goals.map((goal) => (
              <GoalItem key={goal.goal_id} goal={goal} />
            ))}
          </ul>
        </div>
      ) : null}

      {data !== null && evidences.length > 0 ? (
        <div className={styles.section}>
          <p className={styles.sectionLabel}>学情判断</p>
          <ul className={styles.entries} data-testid="learning-evidences">
            {evidences.map((evidence) => (
              <EvidenceItem key={evidence.record_id} evidence={evidence} />
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
