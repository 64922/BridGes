import type { ChatConversationProjection } from "@/lib/api";
import {
  ADJUSTMENT_LABEL,
  DIAGNOSIS_VERDICT_LABEL,
  SUMMARY_TRIGGER_LABEL,
  pendingCheckNote,
} from "@/lib/study-presentation";

import { StudyDiagnosisPanel } from "./StudyDiagnosisPanel";
import { StudyLegacyHistory } from "./StudyLegacyHistory";
import type { StudyRestartPanelProps } from "./StudyRestartPanel";
import styles from "./chat.module.css";

function StudyControlStatus({
  studyLastControl,
  studyPause,
  studySummary,
}: {
  studyLastControl?: ChatConversationProjection["study_last_control"];
  studyPause?: ChatConversationProjection["study_pause"];
  studySummary?: ChatConversationProjection["study_summary"];
}) {
  const skipLabel = studyLastControl?.kind === "full_explanation" ? "直接讲解"
    : studyLastControl?.kind === "skip" ? "跳过" : null;
  return (
    <>
      {studyPause && (
        <p role="status" aria-label="暂停状态">
          已暂停在「{studyPause.position || studyPause.topic}」，进展已保存；
          发送「继续」即可接着学。
          {studyPause.pending_question && `（保留问题「${studyPause.pending_question}」保持未验证）`}
        </p>
      )}
      {skipLabel && studyLastControl?.question && (
        <p role="status" aria-label="跳过状态">
          已{skipLabel}：「{studyLastControl.question}」保持未验证
          {studyLastControl.released ? "，可以继续下一步" : ""}。
        </p>
      )}
      {studySummary && (
        <details>
          <summary>
            阶段小结（{SUMMARY_TRIGGER_LABEL[studySummary.trigger] ?? studySummary.trigger}，
            {studySummary.status === "complete"
              ? "已随回复完整交付"
              : "回复未完整包含原文，可重试该消息补全"}）
          </summary>
          <div className={styles.studySummaryNote} style={{ whiteSpace: "pre-wrap" }}>
            {studySummary.content}
          </div>
        </details>
      )}
    </>
  );
}

function StudyAdoptionBasis({
  adoptions,
}: {
  adoptions: NonNullable<ChatConversationProjection["study_record_adoptions"]>;
}) {
  if (adoptions.length === 0) return null;
  return (
    <details className={styles.studySummary}>
      <summary>教学采用的学情依据（{adoptions.length} 次）</summary>
      <ul>
        {adoptions.map((adoption) => {
          const entries = adoption.entries ?? [];
          return (
            <li key={adoption.adoption_id}>
              {entries.length > 0 ? (
                <ul>
                  {entries.map((entry) => {
                    const adjustments = entry.adjustments ?? [];
                    return (
                      <li key={entry.record_id}>
                        「{entry.title}」
                        ：{DIAGNOSIS_VERDICT_LABEL[entry.verdict] ?? entry.verdict}
                        {adjustments.length > 0 && (
                          <span>
                            {" · 调整："}
                            {adjustments
                              .map((item) => ADJUSTMENT_LABEL[item] ?? item)
                              .join("、")}
                          </span>
                        )}
                        {entry.reason && (
                          <span className={styles.studySummaryNote}>
                            （{entry.reason}）
                          </span>
                        )}
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <p className={styles.studySummaryNote}>
                  本轮未采用具体学情，仅保留待进一步验证的说明。
                </p>
              )}
              {adoption.uncertainty_note && (
                <p className={styles.studySummaryNote}>{adoption.uncertainty_note}</p>
              )}
            </li>
          );
        })}
      </ul>
      <p className={styles.studySummaryNote}>
        学情依据只影响讲法、例子与检查；一次完成不代表长期掌握，纠正或删除记录后不再用于后续教学。
      </p>
    </details>
  );
}

function StudyGoalNotice({
  notice,
}: {
  notice?: ChatConversationProjection["study_goal_notice"];
}) {
  // Issue 08：目标恢复/切换/冲突/待确认由权威事件派生，客户端不自行改名字。
  if (!notice) return null;
  return (
    <p role="status" aria-label="目标通知" className={styles.studySummaryNote}>
      {notice.message}
    </p>
  );
}

function StudyGoalPausedStatus({
  studyGoal,
}: {
  studyGoal: NonNullable<ChatConversationProjection["study_goal"]>;
}) {
  // 目标被其他会话改学暂停：如实呈现，明确恢复方式（不虚构已恢复）。
  if (studyGoal.status !== "paused") return null;
  return (
    <p role="status" aria-label="目标暂停状态" className={styles.studySummaryNote}>
      该目标已在其他会话被暂停；明确说「继续学」即可在此恢复，原进展保持已保存。
    </p>
  );
}

function StudyPendingCheck({
  check,
}: {
  check: NonNullable<ChatConversationProjection["study_pending_check"]>;
}) {
  return (
    <p role="status" aria-label="当前待答问题">
      待答：{check.question}
      {pendingCheckNote({
        question: check.question,
        assistance: check.assistance,
        sourceStatus: check.source_status,
      })}
    </p>
  );
}

function ActiveStudyDetails({
  studyLocalProgress,
  studyGoal,
  studyGoalNotice,
  studyPendingCheck,
  studyLastControl,
  studyPause,
  studySummary,
  studyDiagnosisTasks,
  studyLearningRecord,
  studyRecordAdoptions,
  busy,
  onRetryDiagnosis,
  onCorrectRecord,
  onDeleteRecord,
}: {
  studyLocalProgress?: ChatConversationProjection["study_local_progress"];
  studyGoal: NonNullable<ChatConversationProjection["study_goal"]>;
  studyGoalNotice?: ChatConversationProjection["study_goal_notice"];
  studyPendingCheck?: ChatConversationProjection["study_pending_check"];
  studyLastControl?: ChatConversationProjection["study_last_control"];
  studyPause?: ChatConversationProjection["study_pause"];
  studySummary?: ChatConversationProjection["study_summary"];
  studyDiagnosisTasks?: ChatConversationProjection["study_diagnosis_tasks"];
  studyLearningRecord?: ChatConversationProjection["study_learning_record"];
  studyRecordAdoptions?: ChatConversationProjection["study_record_adoptions"];
  busy: boolean;
  onRetryDiagnosis?: (taskId: string) => Promise<void>;
  onCorrectRecord?: (
    recordId: string,
    note: string,
    expectedVersion: number
  ) => Promise<boolean>;
  onDeleteRecord?: (recordId: string, expectedVersion: number) => Promise<void>;
}) {
  const route = studyGoal.route;
  const nodes = route?.nodes ?? [];
  const currentNode = nodes.find((node) => node.node_id === route?.current_node_id);
  return (
    <section className={styles.studySummary} aria-label="学习目标">
      <StudyGoalNotice notice={studyGoalNotice} />
      <StudyGoalPausedStatus studyGoal={studyGoal} />
      <p className={styles.studyGoalStatement}>目标：{studyGoal.statement}</p>
      {currentNode ? (
        <p role="status">当前知识点：{currentNode.title}</p>
      ) : nodes.length > 0 ? (
        <p role="status">学习路线尚未推进到具体知识点。</p>
      ) : (
        <p role="status">学习路线尚未形成，导师会依据你的目标继续教学。</p>
      )}
      {studyLocalProgress?.current_focus && (
        <p role="status">当前关注：{studyLocalProgress.current_focus}</p>
      )}
      {studyPendingCheck && <StudyPendingCheck check={studyPendingCheck} />}
      <StudyControlStatus
        studyLastControl={studyLastControl}
        studyPause={studyPause}
        studySummary={studySummary}
      />
      <StudyAdoptionBasis adoptions={studyRecordAdoptions ?? []} />
      {route && nodes.length > 0 && (
        <details>
          <summary>
            学习路线（版本 {route.version}，{nodes.length} 个知识点）
          </summary>
          <ol className={styles.studyRoute}>
            {nodes.map((node) => (
              <li
                key={node.node_id}
                aria-current={node.node_id === route.current_node_id ? "step" : undefined}
                className={node.node_id === route.current_node_id
                  ? styles.studyStageCurrent : undefined}
              >
                {node.title}
                {node.depth && ` · ${node.depth}`}
                {(node.prerequisites?.length ?? 0) > 0 && (
                  <span className={styles.studyRoutePrereq}>
                    前置：{(node.prerequisites ?? [])
                      .map((id) => nodes.find((item) => item.node_id === id)?.title ?? id)
                      .join("、")}
                  </span>
                )}
              </li>
            ))}
          </ol>
          {route.revision_reason && (
            <p className={styles.studySummaryNote}>修订依据：{route.revision_reason}</p>
          )}
          <p className={styles.studySummaryNote}>
            路线是可调整的导师方案；跳到某节点不代表前面的内容已完成。
          </p>
        </details>
      )}
      <StudyDiagnosisPanel
        tasks={studyDiagnosisTasks ?? []}
        records={studyLearningRecord ?? []}
        busy={busy}
        onRetry={onRetryDiagnosis}
        onCorrect={onCorrectRecord}
        onDelete={onDeleteRecord}
      />
    </section>
  );
}

function LocalStudyDetails({
  state,
  studyGoalNotice,
  studyPendingCheck,
  studyLastControl,
  studyPause,
  studySummary,
  studyDiagnosisTasks,
  studyLearningRecord,
  studyRecordAdoptions,
  busy,
  onRetryDiagnosis,
  onCorrectRecord,
  onDeleteRecord,
}: {
  state: NonNullable<ChatConversationProjection["study_local_progress"]>;
  studyGoalNotice?: ChatConversationProjection["study_goal_notice"];
  studyPendingCheck?: ChatConversationProjection["study_pending_check"];
  studyLastControl?: ChatConversationProjection["study_last_control"];
  studyPause?: ChatConversationProjection["study_pause"];
  studySummary?: ChatConversationProjection["study_summary"];
  studyDiagnosisTasks?: ChatConversationProjection["study_diagnosis_tasks"];
  studyLearningRecord?: ChatConversationProjection["study_learning_record"];
  studyRecordAdoptions?: ChatConversationProjection["study_record_adoptions"];
  busy: boolean;
  onRetryDiagnosis?: (taskId: string) => Promise<void>;
  onCorrectRecord?: (
    recordId: string,
    note: string,
    expectedVersion: number
  ) => Promise<boolean>;
  onDeleteRecord?: (recordId: string, expectedVersion: number) => Promise<void>;
}) {
  return (
    <section className={styles.studySummary} aria-label="局部学习进展">
      <StudyGoalNotice notice={studyGoalNotice} />
      <p className={styles.studySummaryNote}>正在学习：{state.topic}</p>
      {state.current_focus && (
        <p role="status">当前关注：{state.current_focus}</p>
      )}
      {studyPendingCheck && <StudyPendingCheck check={studyPendingCheck} />}
      <StudyControlStatus
        studyLastControl={studyLastControl}
        studyPause={studyPause}
        studySummary={studySummary}
      />
      <StudyAdoptionBasis adoptions={studyRecordAdoptions ?? []} />
      <StudyDiagnosisPanel
        tasks={studyDiagnosisTasks ?? []}
        records={studyLearningRecord ?? []}
        busy={busy}
        onRetry={onRetryDiagnosis}
        onCorrect={onCorrectRecord}
        onDelete={onDeleteRecord}
      />
    </section>
  );
}

export function StudyDetailsContent({
  study,
  studyLocalProgress,
  studyGoal,
  studyGoalNotice,
  studyPendingCheck,
  studyLastControl,
  studyPause,
  studySummary,
  studyDiagnosisTasks,
  studyLearningRecord,
  studyRecordAdoptions,
  historyStatus,
  restart,
  busy = false,
  onRetryDiagnosis,
  onCorrectRecord,
  onDeleteRecord,
}: {
  study?: ChatConversationProjection["study"];
  studyLocalProgress?: ChatConversationProjection["study_local_progress"];
  studyGoal?: ChatConversationProjection["study_goal"];
  studyGoalNotice?: ChatConversationProjection["study_goal_notice"];
  studyPendingCheck?: ChatConversationProjection["study_pending_check"];
  studyLastControl?: ChatConversationProjection["study_last_control"];
  studyPause?: ChatConversationProjection["study_pause"];
  studySummary?: ChatConversationProjection["study_summary"];
  studyDiagnosisTasks?: ChatConversationProjection["study_diagnosis_tasks"];
  studyLearningRecord?: ChatConversationProjection["study_learning_record"];
  studyRecordAdoptions?: ChatConversationProjection["study_record_adoptions"];
  historyStatus?: ChatConversationProjection["study_history_status"];
  restart?: StudyRestartPanelProps;
  busy?: boolean;
  onRetryDiagnosis?: (taskId: string) => Promise<void>;
  onCorrectRecord?: (
    recordId: string,
    note: string,
    expectedVersion: number
  ) => Promise<boolean>;
  onDeleteRecord?: (recordId: string, expectedVersion: number) => Promise<void>;
}) {
  const hasActiveGoal = Boolean(studyGoal);
  const hasLocalProgress = Boolean(studyLocalProgress);
  if (!hasActiveGoal && !hasLocalProgress && !study && !historyStatus) {
    return (
      <p className={styles.studySummaryNote} role="status">
        当前没有可查看的学习状态。
      </p>
    );
  }
  return (
    <>
      {studyGoal && (
        <ActiveStudyDetails
          studyLocalProgress={studyLocalProgress}
          studyGoal={studyGoal}
          studyGoalNotice={studyGoalNotice}
          studyPendingCheck={studyPendingCheck}
          studyLastControl={studyLastControl}
          studyPause={studyPause}
          studySummary={studySummary}
          studyDiagnosisTasks={studyDiagnosisTasks}
          studyLearningRecord={studyLearningRecord}
          studyRecordAdoptions={studyRecordAdoptions}
          busy={busy}
          onRetryDiagnosis={onRetryDiagnosis}
          onCorrectRecord={onCorrectRecord}
          onDeleteRecord={onDeleteRecord}
        />
      )}
      {!studyGoal && studyLocalProgress && (
        <LocalStudyDetails
          state={studyLocalProgress}
          studyGoalNotice={studyGoalNotice}
          studyPendingCheck={studyPendingCheck}
          studyLastControl={studyLastControl}
          studyPause={studyPause}
          studySummary={studySummary}
          studyDiagnosisTasks={studyDiagnosisTasks}
          studyLearningRecord={studyLearningRecord}
          studyRecordAdoptions={studyRecordAdoptions}
          busy={busy}
          onRetryDiagnosis={onRetryDiagnosis}
          onCorrectRecord={onCorrectRecord}
          onDeleteRecord={onDeleteRecord}
        />
      )}
      {!studyGoal && !studyLocalProgress && (
        <StudyLegacyHistory
          study={study}
          historyStatus={historyStatus}
          restart={restart}
        />
      )}
    </>
  );
}
