"use client";

import type { ChatConversationProjection } from "@/lib/api";
import { LEGACY_STAGE_LABEL } from "@/lib/study-presentation";

import styles from "./StudyStatusBar.module.css";

function legacyStageLabel(
  study: ChatConversationProjection["study"],
): string {
  if (!study) return "学习阶段";
  const stage = study.stage === "awaiting_pages" ? "recognizing" : study.stage;
  return `学习阶段：${LEGACY_STAGE_LABEL[stage ?? ""] ?? "历史记录"}`;
}

export function StudyStatusBar({
  study,
  studyLocalProgress,
  studyGoal,
  studyPendingCheck,
  studyPause,
  historyStatus,
  open,
  onOpen,
}: {
  study?: ChatConversationProjection["study"];
  studyLocalProgress?: ChatConversationProjection["study_local_progress"];
  studyGoal?: ChatConversationProjection["study_goal"];
  studyPendingCheck?: ChatConversationProjection["study_pending_check"];
  studyPause?: ChatConversationProjection["study_pause"];
  historyStatus?: ChatConversationProjection["study_history_status"];
  open: boolean;
  onOpen: (trigger: HTMLButtonElement) => void;
}) {
  const route = studyGoal?.route;
  const currentNode = route?.nodes?.find(
    (node) => node.node_id === route.current_node_id,
  );
  const label = studyGoal
    ? `目标：${studyGoal.statement}`
    : studyLocalProgress
      ? `正在学习：${studyLocalProgress.topic}`
      : historyStatus || study
        ? "旧学习记录（只读）"
        : "学习";
  const meta = studyGoal
    ? currentNode
      ? `当前知识点：${currentNode.title}`
      : "学习路线尚未推进到具体知识点"
    : studyLocalProgress?.current_focus
      ? `当前关注：${studyLocalProgress.current_focus}`
      : study
        ? legacyStageLabel(study)
        : historyStatus?.message ?? "当前学习状态尚未形成，可继续对话";

  return (
    <div
      className={styles.bar}
      role="region"
      aria-label="学习状态"
      data-testid="study-status-bar"
    >
      <div className={styles.summary}>
        <span className={styles.label} title={label}>{label}</span>
        <span className={styles.meta} title={meta}>{meta}</span>
      </div>
      {studyPause && <span className={styles.state}>已暂停</span>}
      {!studyPause && studyPendingCheck && (
        <span className={styles.state} data-state="unanswered">待你作答</span>
      )}
      <button
        type="button"
        className={styles.button}
        aria-expanded={open}
        aria-controls="study-details-panel"
        onClick={(event) => onOpen(event.currentTarget)}
        data-testid="study-details-open"
      >
        学习详情
      </button>
    </div>
  );
}
