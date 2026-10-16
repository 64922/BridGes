import type {
  AtomicProfileEvidenceSource,
  AtomicProfileFeedbackKind,
  AtomicProfileItemProjection,
  AtomicProfileRecurrence,
  AtomicProfileValidityStatus,
  AtomicProfileWriteOrigin,
} from "@/lib/api";

/**
 * 画像页共享的展示文案与格式化（从 AtomicProfileCenter 拆出，控制单文件规模）。
 * 只做展示，不改变任何事实语义。
 */

export const ORIGIN_LABELS: Record<AtomicProfileWriteOrigin, string> = {
  automatic: "从对话里整理",
  user: "你手动记住",
  migration: "从旧列表迁移",
};

export const DELETE_CONFIRM =
  "删除后这条信息会从后续对话的上下文里移除，旧消息重放也不会让它回来。继续吗？";

export const FEEDBACK_OPTIONS: Array<{
  kind: AtomicProfileFeedbackKind;
  label: string;
}> = [
  { kind: "fact_wrong", label: "事实记错" },
  { kind: "expired", label: "信息过期" },
  { kind: "scope_inapplicable", label: "范围不适用" },
  { kind: "preference_not_followed", label: "回答没执行偏好" },
];

export const FEEDBACK_LABELS = Object.fromEntries(
  FEEDBACK_OPTIONS.map(({ kind, label }) => [kind, label])
) as Record<AtomicProfileFeedbackKind, string>;

export const VALIDITY_LABELS: Record<AtomicProfileValidityStatus, string> = {
  unbounded: "没有明确期限",
  scheduled: "尚未生效",
  active: "当前有效",
  expired: "已过期",
  paused: "目标已暂停",
  completed: "目标已完成",
};

export const SCOPE_LABELS = {
  long_term: "长期适用",
  current: "仅提出当时适用",
} as const;

export const RECURRENCE_WEEKDAY_LABELS = ["一", "二", "三", "四", "五", "六", "日"];

/** 主题输入按逗号/顿号分隔；空主题不构成保存门槛，多主题不复制条目。 */
export function parseTopics(value: string): string[] {
  const seen = new Set<string>();
  const topics: string[] = [];
  for (const raw of value.split(/[,，、;；]/)) {
    const topic = raw.trim();
    if (!topic || seen.has(topic)) continue;
    seen.add(topic);
    topics.push(topic);
  }
  return topics;
}

export function topicsToInput(topics: string[] | undefined): string {
  return (topics ?? []).join("，");
}

export function formatUpdatedTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "long",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export function formatMoment(value: string | null | undefined): string | null {
  if (!value) return null;
  return formatUpdatedTime(value);
}

/** 来源标注：只说明有几条对话来源，可得性在展开后逐条如实展示。 */
export function sourceLabel(item: AtomicProfileItemProjection): string | null {
  const count = item.source_message_ids?.length ?? 0;
  if (count === 0) return null;
  return `有 ${count} 条对话来源，展开可核对`;
}

export function sourceStatusText(source: AtomicProfileEvidenceSource): string {
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

export function validityText(evidence: {
  validity_status: AtomicProfileValidityStatus;
  valid_from?: string | null;
  valid_until?: string | null;
  validity_phrase?: string | null;
}): string {
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
 * 画像工单 07：周期安排是长期固定的节奏（每周三晚上…），本身没有期限，
 * 不按统一 TTL 过期；页面只展示原文写明的周期与时段，不做日历推算。
 */
export function recurrenceText(recurrence: AtomicProfileRecurrence): string {
  const days = recurrence.weekdays
    .map((index) => RECURRENCE_WEEKDAY_LABELS[index] ?? `第 ${index + 1} 天`)
    .join("、");
  const when =
    recurrence.kind === "daily"
      ? `每天${recurrence.time_of_day ?? ""}`
      : `每周${days}${recurrence.time_of_day ?? ""}`;
  return `${when}固定进行（长期有效，不设统一过期时间）。`;
}
