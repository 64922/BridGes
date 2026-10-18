import type {
  ChatConversationProjection,
  ChatMessageProjection,
} from "@/lib/api";

export const SUMMARY_TRIGGER_LABEL: Record<string, string> = {
  explicit_request: "按你的要求生成",
  natural_completion: "完成后自然生成",
  pause: "暂停时生成",
};

export const DIAGNOSIS_STATUS_LABEL: Record<string, string> = {
  pending: "待后台核验",
  running: "后台核验中",
  succeeded: "核验完成",
  failed: "核验失败",
  invalidated: "来源已失效，未形成诊断",
};

export const DIAGNOSIS_VERDICT_LABEL: Record<string, string> = {
  independent_completion: "独立完成",
  hinted_completion: "提示后完成",
  misconception: "存在具体误解",
  unverified: "尚未验证",
  undetermined: "当前无法判断",
};

export const ANSWER_ASSISTANCE_LABEL: Record<string, string> = {
  independent: "作答前无提示",
  after_hint: "提示后作答",
  none: "无提示",
};

export const ADJUSTMENT_LABEL: Record<string, string> = {
  prerequisite_review: "补讲前置知识点",
  independent_application: "安排独立应用",
  transfer_application: "改用迁移应用",
  recheck: "安排复核",
  hint_granularity: "改用更小粒度提示",
};

export const CONTROL_KIND_LABEL: Record<string, string> = {
  full_explanation: "直接讲解",
  skip: "跳过",
};

export const LEGACY_STAGE_LABEL: Record<string, string> = {
  recognizing: "书页识别",
  preview: "辅助预习",
  tutoring: "辅导",
  review: "复盘",
  summary: "总结",
};

export interface StudyTurnPending {
  question: string;
  assistance: string;
  sourceStatus: string;
}

export interface StudyTurnControl {
  kind: string;
  question: string;
  released: boolean;
}

export interface StudyTurnPause {
  topic: string;
  position: string;
  pendingQuestion: string;
}

export interface StudyTurnDiagnosis {
  taskId: string;
  status: string;
  question: string;
  failureReason: string;
  retryable: boolean;
}

export interface StudyTurnRecord {
  recordId: string;
  title: string;
  verdict: string;
  corrected: boolean;
  pendingConfirmation: boolean;
  sourceAvailable: boolean;
}

export interface StudyTurnSummary {
  trigger: string;
  status: string;
}

export interface StudyTurnGoal {
  statement: string;
  status: string;
}

export interface StudyTurnState {
  pending: StudyTurnPending | null;
  goal: StudyTurnGoal | null;
  controls: StudyTurnControl[];
  paused: StudyTurnPause | null;
  summary: StudyTurnSummary | null;
  diagnosis: StudyTurnDiagnosis[];
  records: StudyTurnRecord[];
  adoptionCount: number;
}

export function pendingCheckNote(pending: StudyTurnPending): string {
  return `${pending.assistance === "hinted"
    ? "（已提供提示，本次将按提示后完成记录）"
    : ""}${pending.sourceStatus === "unavailable"
    ? "（本题依据的资料已不可用或已更新，将按未验证处理，不按旧依据判定）"
    : ""}`;
}

export type StudyTurnTone =
  | "unanswered"
  | "unverified"
  | "verification-pending"
  | "verified"
  | "hinted"
  | "misconception"
  | "service-failure";

export function verdictTone(verdict: string): StudyTurnTone {
  if (verdict === "independent_completion") return "verified";
  if (verdict === "hinted_completion") return "hinted";
  if (verdict === "misconception") return "misconception";
  if (verdict === "unverified") return "unverified";
  return "unverified";
}

export function verdictLabel(verdict: string): string {
  return DIAGNOSIS_VERDICT_LABEL[verdict] ?? verdict;
}

function emptyTurn(): StudyTurnState {
  return {
    pending: null,
    goal: null,
    controls: [],
    paused: null,
    summary: null,
    diagnosis: [],
    records: [],
    adoptionCount: 0,
  };
}

function assistantAfterUser(
  messages: ChatMessageProjection[],
  userId: string | null | undefined,
): string | null {
  if (!userId) return null;
  const index = messages.findIndex((message) => message.message_id === userId);
  if (index < 0) return null;
  let assistantId: string | null = null;
  for (let cursor = index + 1; cursor < messages.length; cursor += 1) {
    const message = messages[cursor];
    if (message.role === "user") break;
    assistantId = message.message_id;
  }
  return assistantId;
}

/**
 * 把会话级学习投影按真实消息标识归到具体助手回合（工单 12）。
 *
 * 只使用服务端已提交的归属字段：待答问题与小结带助手消息标识，控制与
 * 采用记录由服务端补充助手消息标识；诊断与暂停以用户消息为锚点，取该
 * 轮最后一条助手消息作为反馈。找不到真实消息时不猜测、不展示。
 */
export function buildStudyTurns(
  conversation: ChatConversationProjection | null | undefined,
): Map<string, StudyTurnState> {
  const turns = new Map<string, StudyTurnState>();
  if (!conversation || conversation.mode !== "study") return turns;
  const messages = conversation.messages ?? [];
  const known = new Set(messages.map((message) => message.message_id));
  const attach = (
    messageId: string | null | undefined,
    update: (turn: StudyTurnState) => void,
  ) => {
    if (!messageId || !known.has(messageId)) return;
    const turn = turns.get(messageId) ?? emptyTurn();
    update(turn);
    turns.set(messageId, turn);
  };

  const pending = conversation.study_pending_check;
  if (pending) {
    attach(pending.source_message_id, (turn) => {
      turn.pending = {
        question: pending.question,
        assistance: pending.assistance,
        sourceStatus: pending.source_status,
      };
    });
  }

  const goal = conversation.study_goal;
  if (goal) {
    attach(assistantAfterUser(messages, goal.source_message_id), (turn) => {
      turn.goal = { statement: goal.statement, status: goal.status };
    });
  }

  const control = conversation.study_last_control;
  if (control && (control.kind === "skip" || control.kind === "full_explanation")) {
    attach(control.assistant_message_id, (turn) => {
      turn.controls.push({
        kind: control.kind,
        question: control.question,
        released: control.released,
      });
    });
  }

  const pause = conversation.study_pause;
  if (pause) {
    attach(assistantAfterUser(messages, pause.source_message_id), (turn) => {
      turn.paused = {
        topic: pause.topic,
        position: pause.position,
        pendingQuestion: pause.pending_question,
      };
    });
  }

  const summary = conversation.study_summary;
  if (summary) {
    attach(summary.assistant_message_id, (turn) => {
      turn.summary = { trigger: summary.trigger, status: summary.status };
    });
  }

  for (const task of conversation.study_diagnosis_tasks ?? []) {
    if (task.status === "succeeded") continue;
    attach(assistantAfterUser(messages, task.evidence_message_id), (turn) => {
      turn.diagnosis.push({
        taskId: task.task_id,
        status: task.status,
        question: task.question,
        failureReason: task.failure_reason,
        retryable: task.retryable,
      });
    });
  }

  for (const record of conversation.study_learning_record ?? []) {
    const corrected = record.state === "corrected";
    for (const evidence of record.evidences ?? []) {
      attach(evidence.feedback_message_id, (turn) => {
        if (turn.records.some((item) => item.recordId === record.record_id)) return;
        turn.records.push({
          recordId: record.record_id,
          title: record.title,
          verdict: evidence.verdict ?? record.verdict,
          corrected,
          pendingConfirmation: record.pending_confirmation,
          sourceAvailable: evidence.source_available,
        });
      });
    }
  }

  for (const adoption of conversation.study_record_adoptions ?? []) {
    if (adoption.status !== "applied") continue;
    const adoptedCount = (adoption.entries ?? []).length;
    if (adoptedCount === 0) continue;
    attach(adoption.assistant_message_id, (turn) => {
      turn.adoptionCount += adoptedCount;
    });
  }

  return turns;
}
