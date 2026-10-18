"use client";

import { useEffect, useRef } from "react";

import { Icon } from "@/components/design-system/Icon";
import type { ChatConversationProjection } from "@/lib/api";

import { StudyDetailsContent } from "./StudyDetailsContent";
import type { StudyRestartPanelProps } from "./StudyRestartPanel";
import styles from "./StudyDetailsPanel.module.css";

export interface StudyDetailsPanelProps {
  study?: ChatConversationProjection["study"];
  studyLocalProgress?: ChatConversationProjection["study_local_progress"];
  studyGoal?: ChatConversationProjection["study_goal"];
  studyGoalNotice?: ChatConversationProjection["study_goal_notice"];
  studyPendingCheck?: ChatConversationProjection["study_pending_check"];
  studyLastControl?: ChatConversationProjection["study_last_control"];
  studyPause?: ChatConversationProjection["study_pause"];
  studySummary?: ChatConversationProjection["study_summary"];
  historyStatus?: ChatConversationProjection["study_history_status"];
  studyDiagnosisTasks?: ChatConversationProjection["study_diagnosis_tasks"];
  studyLearningRecord?: ChatConversationProjection["study_learning_record"];
  studyRecordAdoptions?: ChatConversationProjection["study_record_adoptions"];
  restart?: StudyRestartPanelProps;
  busy?: boolean;
  onRetryDiagnosis?: (taskId: string) => Promise<void>;
  onCorrectRecord?: (
    recordId: string,
    note: string,
    expectedVersion: number
  ) => Promise<boolean>;
  onDeleteRecord?: (recordId: string, expectedVersion: number) => Promise<void>;
}

/**
 * 学习详情右侧面板：默认关闭；打开后承载目标、路线、证据、档案与旧历史。
 * 正文里的讲解、问题与反馈不依赖本面板，关闭后仍可连续阅读。
 */
export function StudyDetailsPanel({
  onClose,
  ...content
}: StudyDetailsPanelProps & { onClose: () => void }) {
  const panelRef = useRef<HTMLElement>(null);

  useEffect(() => {
    panelRef.current?.focus();
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.isComposing) return;
      event.preventDefault();
      onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <aside
      ref={panelRef}
      id="study-details-panel"
      role="dialog"
      aria-modal="false"
      tabIndex={-1}
      aria-labelledby="study-details-title"
      className={styles.panel}
      data-testid="study-details-panel"
    >
      <header className={styles.header}>
        <div className={styles.titleGroup}>
          <span className={styles.kicker}>学习</span>
          <h2 id="study-details-title" className={styles.title}>
            学习详情
          </h2>
        </div>
        <button
          type="button"
          aria-label="关闭学习详情面板"
          className={styles.closeButton}
          data-testid="study-details-close"
          onClick={onClose}
        >
          <Icon name="close" size={18} aria-hidden />
        </button>
      </header>

      <p className={styles.meta}>
        目标、路线、学习档案与旧会话历史均在需要时展开；当前对话正文不受影响。
      </p>

      <div className={styles.body}>
        <StudyDetailsContent {...content} />
      </div>
    </aside>
  );
}
