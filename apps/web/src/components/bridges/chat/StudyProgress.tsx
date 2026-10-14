import { useState } from "react";

import { Button } from "@/components/design-system/Button";
import type { ChatConversationProjection } from "@/lib/api";

import { StudyRestartPanel, type StudyRestartPanelProps } from "./StudyRestartPanel";
import styles from "./chat.module.css";

const STAGES = [
  { id: "recognizing", label: "书页识别" },
  { id: "preview", label: "辅助预习" },
  { id: "tutoring", label: "辅导" },
  { id: "review", label: "复盘" },
  { id: "summary", label: "总结" },
] as const;

const JUDGEMENT_LABEL: Record<string, string> = {
  correct: "正确",
  incomplete: "不完整",
  incorrect: "错误",
};

// Issue 04：用户学习控制的自然语言状态；未验证如实标注，不当作错答或掌握。
const SUMMARY_TRIGGER_LABEL: Record<string, string> = {
  explicit_request: "按你的要求生成",
  natural_completion: "完成后自然生成",
  pause: "暂停时生成",
};

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

// 四段兜底文案与消息内总结一致：空段只陈述实际判定，不额外宣称掌握；
// 未作答/尚未判定单独成段，不与答对或漏洞混排（工单 36）。
const SUMMARY_SECTIONS = [
  { kind: "learned", label: "学到了什么", empty: "本节没有可依据书页归纳的内容。" },
  { kind: "mastered", label: "复盘已掌握", empty: "本次复盘没有判定为正确的题目。" },
  { kind: "gap", label: "还需补的点", empty: "本次没有判定不完整、错误或依据需重新确认的题目。" },
  { kind: "unanswered", label: "未作答或尚未判定", empty: "本次复盘没有未作答或尚未判定的题目。" },
] as const;

function evidenceLabels(
  state: NonNullable<ChatConversationProjection["study"]>,
) {
  const labels = new Map<string, string>();
  const materialStatus = new Map(
    (state.materials ?? []).map((item) => [item.object_id, item.status]),
  );
  (state.review?.questions ?? []).forEach((item, index) => {
    labels.set(
      item.question_id,
      `第${index + 1}题「${item.question}」`
        + (item.judgement
          ? `判定为${JUDGEMENT_LABEL[item.judgement]}`
          : item.answer != null ? "已作答（尚未判定）" : "未作答"),
    );
  });
  for (const page of state.pages ?? []) {
    for (const fragment of page.fragments) {
      const status = materialStatus.get(page.object_id);
      const source = status === "deleted" || status === "pending_cleanup"
        ? "已删除材料的历史记录："
        : status === "unknown" ? "材料状态未知：" : "";
      labels.set(
        fragment.fragment_id,
        `${source}上传第${page.ordinal}页`
          + (page.page_number != null ? `（书上第${page.page_number}页）` : "")
          + ` · ${fragment.position}`
          + (fragment.source === "user" ? " · 用户补录" : ""),
      );
    }
  }
  return labels;
}

// Issue 05：后台核验状态与学习档案文案。诊断结论是证据语义（完成方式、
// 具体误解与不确定性），不渲染百分比、人格或整体能力标签；一次完成不
// 代表长期掌握，来源失效如实标注而不冒充结论。
const DIAGNOSIS_STATUS_LABEL: Record<string, string> = {
  pending: "待后台核验",
  running: "后台核验中",
  failed: "核验失败",
  invalidated: "来源已失效，未形成诊断",
};

const DIAGNOSIS_VERDICT_LABEL: Record<string, string> = {
  independent_completion: "独立完成",
  hinted_completion: "提示后完成",
  misconception: "存在具体误解",
  unverified: "尚未验证",
  undetermined: "当前无法判断",
};

const ANSWER_ASSISTANCE_LABEL: Record<string, string> = {
  independent: "作答前无提示",
  after_hint: "提示后作答",
  none: "无提示",
};

// Issue 09：采用学情的调整方向；呈现真实生效的教学调整，不是能力等级。
const ADJUSTMENT_LABEL: Record<string, string> = {
  prerequisite_review: "补讲前置知识点",
  independent_application: "安排独立应用",
  transfer_application: "改用迁移应用",
  recheck: "安排复核",
  hint_granularity: "改用更小粒度提示",
};

// Issue 09：学习详情按需查看本轮采用依据——采用了哪些已核验学情、按什么
// 方向调整；只展示真实生效的采用记录，不含模型提示词或内部推理。
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

// Issue 06：删除先按既有用户管理规范确认；撤回判断与旧证据的采用，
// 原聊天历史仍可查看。
const DELETE_RECORD_CONFIRM =
  "删除后这条判断与关联旧证据不再用于后续教学，迟到的核验结果也不会恢复它；"
  + "原聊天历史仍可查看。继续吗？";

function StudyDiagnosisPanel({
  tasks,
  records,
  busy,
  onRetry,
  onCorrect,
  onDelete,
}: {
  tasks: NonNullable<ChatConversationProjection["study_diagnosis_tasks"]>;
  records: NonNullable<ChatConversationProjection["study_learning_record"]>;
  busy: boolean;
  onRetry?: (taskId: string) => Promise<void>;
  onCorrect?: (
    recordId: string,
    note: string,
    expectedVersion: number
  ) => Promise<boolean>;
  onDelete?: (recordId: string, expectedVersion: number) => Promise<void>;
}) {
  const [retrying, setRetrying] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [savingId, setSavingId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  if (tasks.length === 0 && records.length === 0) return null;
  return (
    <div className={styles.studySummary} aria-label="学习档案">
      <p className={styles.studySummaryNote}>
        后台独立核验只针对当时的实际作答；一次完成不代表长期掌握，结论会随新证据更新。
        你可以查看依据、纠正判断或删除记录。
      </p>
      {tasks.length > 0 && (
        <div className={styles.studySummarySection}>
          <h3>核验状态</h3>
          <ul>
            {tasks.map((task) => (
              <li key={task.task_id}>
                {task.question ? `「${task.question}」：` : ""}
                {DIAGNOSIS_STATUS_LABEL[task.status] ?? task.status}
                {task.failure_reason && ` · ${task.failure_reason}`}
                {task.status === "failed" && task.retryable && onRetry && (
                  <Button variant="secondary" size="sm"
                    disabled={busy || retrying === task.task_id}
                    onClick={async () => {
                      setRetrying(task.task_id);
                      try { await onRetry(task.task_id); } finally { setRetrying(null); }
                    }}>
                    重试核验
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      {records.length > 0 && (
        <div className={styles.studySummarySection}>
          <h3>已核验的知识点</h3>
          <ul>
            {records.map((record) => {
              const corrected = record.state === "corrected";
              const editing = editingId === record.record_id;
              const recordBusy =
                savingId === record.record_id || deletingId === record.record_id;
              return (
                <li key={record.record_id}>
                  <strong>{record.title}</strong>
                  {record.subject && `（${record.subject}`
                    + `${record.usage_context ? ` · ${record.usage_context}` : ""}）`}
                  ：{corrected
                    ? "用户纠正（自评，未经核验）"
                    : DIAGNOSIS_VERDICT_LABEL[record.verdict] ?? record.verdict}
                  {!corrected && record.pending_confirmation
                    && "，证据方向冲突，待进一步验证"}
                  {!corrected && record.performance
                    && <span> · 表现：{record.performance}</span>}
                  {corrected && (
                    <div>
                      <p className={styles.studySummaryNote}>
                        我的纠正（自评，未经核验）：{record.user_note}
                      </p>
                      {record.superseded_verdict && (
                        <p className={styles.studySummaryNote}>
                          原核验结论「{DIAGNOSIS_VERDICT_LABEL[record.superseded_verdict]
                            ?? record.superseded_verdict}」已被你的纠正取代，不再用于后续教学。
                          {record.superseded_performance
                            && `（原表现：${record.superseded_performance}）`}
                        </p>
                      )}
                      {record.pending_confirmation && (
                        <p className={styles.studySummaryNote}>
                          你的纠正与后续自动核验并存，待进一步验证。
                        </p>
                      )}
                    </div>
                  )}
                  {!record.source_available && (
                    <p className={styles.studySummaryNote}>
                      部分依据来源已不可读，仅保留可核对的历史记录。
                    </p>
                  )}
                  {editing ? (
                    <div className={styles.studyRecordActions}>
                      <textarea
                        aria-label="纠正内容"
                        value={draft}
                        maxLength={500}
                        rows={3}
                        onChange={(event) => setDraft(event.target.value)}
                      />
                      <Button variant="primary" size="sm"
                        disabled={busy || recordBusy || !draft.trim()}
                        onClick={async () => {
                          setSavingId(record.record_id);
                          try {
                            const saved = await onCorrect?.(
                              record.record_id, draft.trim(), record.record_version
                            );
                            if (saved) {
                              setEditingId(null);
                              setDraft("");
                            }
                          } finally {
                            setSavingId(null);
                          }
                        }}>
                        保存
                      </Button>
                      <Button variant="ghost" size="sm" disabled={busy || recordBusy}
                        onClick={() => { setEditingId(null); setDraft(""); }}>
                        取消
                      </Button>
                    </div>
                  ) : (
                    (onCorrect || onDelete) && (
                      <div className={styles.studyRecordActions}>
                        {onCorrect && (
                          <Button variant="secondary" size="sm" disabled={busy}
                            onClick={() => {
                              setEditingId(record.record_id);
                              setDraft(corrected ? record.user_note : "");
                            }}>
                            纠正
                          </Button>
                        )}
                        {onDelete && (
                          <Button variant="danger" size="sm" disabled={busy || recordBusy}
                            onClick={async () => {
                              if (!window.confirm(DELETE_RECORD_CONFIRM)) return;
                              setDeletingId(record.record_id);
                              try {
                                await onDelete(record.record_id, record.record_version);
                              } finally {
                                setDeletingId(null);
                              }
                            }}>
                            删除
                          </Button>
                        )}
                      </div>
                    )
                  )}
                  <details>
                    <summary>依据与证据（{record.evidence_count} 条）</summary>
                    {((record.supporting ?? []).length > 0
                      || (record.refuting ?? []).length > 0
                      || record.uncertainty) && (
                      <ul>
                        {(record.supporting ?? []).map((item, index) => (
                          <li key={`supporting-${index}`}>支持：{item}</li>
                        ))}
                        {(record.refuting ?? []).map((item, index) => (
                          <li key={`refuting-${index}`}>抵触：{item}</li>
                        ))}
                        {record.uncertainty && <li>不确定性：{record.uncertainty}</li>}
                      </ul>
                    )}
                    <ol>
                      {(record.evidences ?? []).map((evidence) => (
                        <li key={evidence.evidence_id}>
                          {evidence.source_available ? (
                            <>
                              「{evidence.question}」
                              {ANSWER_ASSISTANCE_LABEL[evidence.assistance]
                                && `（${ANSWER_ASSISTANCE_LABEL[evidence.assistance]}）`}
                              {evidence.verdict
                                && ` · 核验：${DIAGNOSIS_VERDICT_LABEL[evidence.verdict]
                                  ?? evidence.verdict}`}
                            </>
                          ) : (
                            <>
                              {evidence.question ? `「${evidence.question}」` : "一条依据"}
                              （{evidence.source_note || "来源已不可读"}）
                            </>
                          )}
                        </li>
                      ))}
                    </ol>
                  </details>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
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

export function StudyProgress({ study: state, studyLocalProgress, studyGoal, studyGoalNotice, studyPendingCheck, studyLastControl, studyPause, studySummary, studyDiagnosisTasks, studyLearningRecord, studyRecordAdoptions, historyStatus, restart, busy = false, onRetryDiagnosis, onCorrectRecord, onDeleteRecord }: {
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
  /** 工单 12：旧学习会话的主动续学面板（由页面接线 API 与导航）。 */
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
  if (studyGoal) {
    // Issue 07：顶部固定显示长期目标与当前知识点，路线按需展开；
    // 只呈现已提交的真实状态——无路线不虚报，跳节点不标掌握。
    // Issue 04：控制/暂停/小结状态在同一区域如实呈现。
    const route = studyGoal.route;
    const nodes = route?.nodes ?? [];
    const currentNode = nodes.find((node) => node.node_id === route?.current_node_id);
    return (
      <section className={styles.studyProgress} aria-label="学习目标">
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
        {studyPendingCheck && (
          <p role="status" aria-label="当前待答问题">
            待答：{studyPendingCheck.question}
            {studyPendingCheck.assistance === "hinted" && "（已提供提示，本次将按提示后完成记录）"}
            {studyPendingCheck.source_status === "unavailable"
              && "（本题依据的资料已不可用或已更新，将按未验证处理，不按旧依据判定）"}
          </p>
        )}
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
  if (studyLocalProgress) {
    return (
      <section className={styles.studyProgress} aria-label="局部学习进展">
        <StudyGoalNotice notice={studyGoalNotice} />
        <p className={styles.studySummaryNote}>正在学习：{studyLocalProgress.topic}</p>
        {studyLocalProgress.current_focus && (
          <p role="status">当前关注：{studyLocalProgress.current_focus}</p>
        )}
        {studyPendingCheck && (
          <p role="status" aria-label="当前待答问题">
            待答：{studyPendingCheck.question}
            {studyPendingCheck.assistance === "hinted" && "（已提供提示，本次将按提示后完成记录）"}
            {studyPendingCheck.source_status === "unavailable"
              && "（本题依据的资料已不可用或已更新，将按未验证处理，不按旧依据判定）"}
          </p>
        )}
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
  if (!state && historyStatus) {
    return (
      <section className={styles.studyProgress} aria-label="学习历史">
        <p className={styles.studySummaryNote} role="status">{historyStatus.message}</p>
        {restart && <StudyRestartPanel {...restart} />}
      </section>
    );
  }
  if (!state) return null;
  const current = state?.stage === "awaiting_pages" ? "recognizing" : state?.stage ?? "recognizing";
  const summary = state?.summary;
  const labels = summary && state ? evidenceLabels(state) : null;
  return (
    <section className={styles.studyProgress} aria-label="学习阶段">
      {historyStatus && (
        <p className={styles.studySummaryNote} role="status">{historyStatus.message}</p>
      )}
      <p className={styles.studySummaryNote} role="note">
        本节是按原始记录只读展示的历史；旧流程不会再执行。
      </p>
      <ol className={styles.studyStages}>
        {STAGES.map((stage) => (
          <li
            key={stage.id}
            aria-current={stage.id === current ? "step" : undefined}
            className={stage.id === current ? styles.studyStageCurrent : undefined}
          >
            {stage.label}
          </li>
        ))}
      </ol>
      {summary && labels && (
        <div className={styles.studySummary}>
          {SUMMARY_SECTIONS.map((section) => {
            const points = summary.points.filter((point) => point.kind === section.kind);
            return (
              <div key={section.kind} className={styles.studySummarySection}>
                <h3>{section.label}</h3>
                {points.length === 0 ? (
                  <p>{section.empty}</p>
                ) : (
                  <ul>
                    {points.map((point, index) => {
                      const refs = [
                        ...(point.question_ids ?? []),
                        ...(point.fragment_ids ?? []),
                      ].map((ref) => labels.get(ref)).filter((label): label is string => !!label);
                      return (
                        <li key={index}>
                          {point.text}
                          {refs.length > 0 && (
                            <span className={styles.studySummaryEvidence}>
                              依据：{refs.join("；")}
                            </span>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            );
          })}
          <p className={styles.studySummaryNote}>
            总结依据本节书页与已判定题；本节为只读历史记录，继续学习请使用下方「主动续学」。
          </p>
        </div>
      )}
      {state?.wait_reason && <p role="status">{state.wait_reason === "page_order"
        ? "书上页码与上传顺序不一致，请查看证据并按下方消息调整页序。"
        : "书页有待补拍的位置，请查看下方消息。"}</p>}
      {state?.page_update && (
        <details open>
          <summary>追加书页待确认（尚未更新本节范围）</summary>
          <p role="status">{state.page_update.wait_reason === "page_order"
            ? "请按下方消息调整追加后的页序。"
            : state.page_update.wait_reason === "recognition_failed"
              ? "追加书页识别未完成，请重试原失败消息或补拍。"
              : "请查看下方消息，补充不清晰内容或确认同节归属。"}</p>
          <ul>
            {state.page_update.pages.map((page) => (
              <li key={page.object_id}>
                上传第{page.ordinal}页
              {state.materials?.find((item) => item.object_id === page.object_id)?.status === "deleted"
                && " · 原材料已删除，保留的识别内容仍是历史记录"}
              {state.materials?.find((item) => item.object_id === page.object_id)?.status === "pending_cleanup"
                && " · 原材料正在删除，保留的识别内容仍是历史记录"}
              {state.materials?.find((item) => item.object_id === page.object_id)?.status === "unknown"
                && " · 暂时无法核验原材料状态"}
                {page.page_number != null && `（书上第${page.page_number}页）`}
                {!page.same_section && " · 同节归属待确认"}
                {page.unclear?.map((issue) => ` · ${issue.position}：${issue.reason}`).join("")}
              </li>
            ))}
          </ul>
        </details>
      )}
      {state?.pages && state.pages.length > 0 && (
        <details>
          <summary>已识别书页与证据（{state.pages.length} 页）</summary>
          <ol className={styles.studyPages}>
            {state.pages.map((page) => (
              <li key={page.object_id}>
                <strong>第{page.ordinal}页</strong>
                {state.materials?.find((item) => item.object_id === page.object_id)?.status === "deleted"
                  && <span> · 原材料已删除，以下内容仅是保留的历史识别记录</span>}
                {state.materials?.find((item) => item.object_id === page.object_id)?.status === "pending_cleanup"
                  && <span> · 原材料正在删除，以下内容仅是保留的历史识别记录</span>}
                {state.materials?.find((item) => item.object_id === page.object_id)?.status === "unknown"
                  && <span> · 暂时无法核验原材料状态</span>}
                {page.page_number != null && `（书上第${page.page_number}页）`}
                {page.replaced_object_ids && page.replaced_object_ids.length > 0 && "（已补拍更新）"}
                <ul>
                  {page.fragments.map((fragment, index) => (
                    <li key={index}>
                      {fragment.position}：{fragment.text}
                      {fragment.source === "user" && "（用户补录）"}
                    </li>
                  ))}
                  {page.unclear?.map((issue, index) => (
                    <li key={index}>待补拍 {issue.position}：{issue.reason}</li>
                  ))}
                </ul>
              </li>
            ))}
          </ol>
        </details>
      )}
      {restart && <StudyRestartPanel {...restart} />}
    </section>
  );
}
