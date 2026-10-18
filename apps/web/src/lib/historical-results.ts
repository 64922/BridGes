import type { HumanizerResultProjection } from "@/lib/api";

import type { ChatMessageView } from "./chat-message-view";

/**
 * 退役能力的历史结果只读文本。
 *
 * 这里只取当时已经保存的内容，不重新生成、不改变消息正文：
 * - 人味化结果优先使用交付门通过的最终正文；失败/未交付时返回空串，
 *   避免把候选草稿恢复成正文或复制内容。
 * - 其他消息在没有自然语言正文时，按结构化结果确定性拼出可复制的清单。
 */
export function historicalDeliverableText(message: ChatMessageView): string | null {
  if (message.humanizer) {
    return deliveredHumanizerText(message.humanizer) ?? "";
  }
  if (message.plainText.trim().length > 0) return null;
  const sections = [
    learningResourcesText(message),
    careerPlanText(message),
    githubProjectsText(message),
  ].filter((value): value is string => Boolean(value));
  return sections.length > 0 ? sections.join("\n\n") : null;
}

/** 人味化历史结果中已经通过交付门的正文；未交付返回 null。 */
export function deliveredHumanizerText(
  result: HumanizerResultProjection
): string | null {
  if (result.article) {
    if (result.article.delivery_status === "failed") return null;
    const text = result.article.final_text?.trim();
    return text ? (result.article.final_text ?? null) : null;
  }
  if (result.status === "error") return null;
  const legacyText = result.output?.final_text?.trim();
  return legacyText ? result.output?.final_text ?? null : null;
}

/** 历史消息是否没有可读正文（供界面如实说明结构化结果兜底）。 */
export function historicalBodyIsMissing(message: ChatMessageView): boolean {
  if (message.body !== undefined) return false;
  if (message.plainText.trim().length > 0) return false;
  if (message.humanizer && deliveredHumanizerText(message.humanizer) !== null) {
    return false;
  }
  return true;
}

/** 只接受 http(s) 或同源相对地址，拒绝伪协议与协议相对地址。 */
export function safeHistoricalHref(value: string | null | undefined): string | null {
  if (!value) return null;
  const trimmed = value.trim();
  if (trimmed.startsWith("/") && !trimmed.startsWith("//")) return trimmed;
  try {
    const parsed = new URL(trimmed);
    if (parsed.protocol === "http:" || parsed.protocol === "https:") return trimmed;
  } catch {
    return null;
  }
  return null;
}

export function isExternalHistoricalHref(href: string): boolean {
  return href.startsWith("http://") || href.startsWith("https://");
}

function learningResourcesText(message: ChatMessageView): string | null {
  const resources = message.learningResources;
  if (!resources) return null;
  const lines: string[] = [];
  if (resources.original_phrase) lines.push(`原始说法：${resources.original_phrase}`);
  if (resources.final_query) lines.push(`实际查询词：${resources.final_query}`);
  for (const item of resources.items ?? []) {
    lines.push(
      `${item.order}. [${item.kind === "book" ? "图书" : "视频"}] ${item.title}` +
        (item.url ? ` ${item.url}` : "")
    );
  }
  if (lines.length === 0) {
    lines.push(resources.error_message ?? "本轮没有保存可复制的资料结果。");
  }
  return `学习资料推荐（历史结果）\n${lines.join("\n")}`;
}

function careerPlanText(message: ChatMessageView): string | null {
  const plan = message.careerPlan;
  if (!plan) return null;
  const lines: string[] = [];
  if (plan.original_request) lines.push(`原始请求：${plan.original_request}`);
  if (plan.family_title || plan.job_terms?.length) {
    lines.push(
      `目标岗位：${plan.family_title ?? plan.job_terms?.join("、") ?? "未给出"}`
    );
  }
  for (const sample of plan.samples ?? []) {
    lines.push(
      [sample.title || sample.url, sample.company, sample.city, sample.salary_raw]
        .filter(Boolean)
        .join(" · ") + (sample.url ? ` ${sample.url}` : "")
    );
  }
  for (const advice of plan.advices ?? []) {
    lines.push(`${advice.title}：${advice.detail}`);
  }
  if (lines.length === 0) {
    lines.push(plan.error_message ?? plan.empty_reason ?? "本轮没有保存可复制的岗位结果。");
  }
  return `职业规划（历史结果）\n${lines.join("\n")}`;
}

function githubProjectsText(message: ChatMessageView): string | null {
  const projects = message.githubProjects;
  if (!projects) return null;
  const lines: string[] = [];
  if (projects.scenario) lines.push(`核心场景：${projects.scenario}`);
  for (const item of projects.recommendations ?? []) {
    lines.push(
      `${item.rank}. ${item.full_name}` +
        (item.html_url ? ` ${item.html_url}` : "") +
        (item.coverage_note ? `：${item.coverage_note}` : "")
    );
  }
  if (lines.length === 0) {
    lines.push(projects.error_message ?? projects.empty_reason ?? "本轮没有保存可复制的仓库结果。");
  }
  return `GitHub 项目推荐（历史结果）\n${lines.join("\n")}`;
}
