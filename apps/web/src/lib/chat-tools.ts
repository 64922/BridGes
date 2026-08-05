import type { IconName } from "@/components/design-system/Icon";

/**
 * 聊天工具入口的结构化意图（Issue 13）：「+」菜单与空白态建议卡共享。
 *
 * 论文搜索已经接入真实 arXiv MCP 流程；其它意图目前只负责预填前缀，
 * 不在前端伪造任何工具结果。
 */
export interface ChatToolIntent {
  label: string;
  icon: IconName;
  /** 预填到输入区的结构化意图前缀 */
  prefix: string;
}

export const CHAT_TOOL_INTENTS: readonly ChatToolIntent[] = [
  { label: "论文搜索", icon: "paperSearch", prefix: "论文搜索：" },
  { label: "文章人味化", icon: "humanize", prefix: "文章人味化：" },
  { label: "生涯规划助手", icon: "career", prefix: "生涯规划助手：" },
] as const;

/** Issue 28：「文章人味化」入口已接入真实任务对话框（不再预填前缀）。 */
export const HUMANIZER_TOOL_LABEL = "文章人味化" as const;

/** Issue 29：「生涯规划助手」入口已接入真实任务对话框（不再预填前缀）。 */
export const CAREER_TOOL_LABEL = "生涯规划助手" as const;

/**
 * Issue 29：生涯规划意图的显式前缀（与后端 src/bridges/career/intent.py
 * 的 _PREFIX_MARKERS 保持一致；前端用于流式期间判定规划过程卡）。
 */
export const CAREER_INTENT_PREFIXES: readonly string[] = [
  "生涯规划助手：",
  "生涯规划：",
  "职业规划：",
];

/**
 * Issue 29：生涯规划强触发关键词（与后端 intent.py 的 _STRONG_KEYWORDS
 * 保持一致）。前端只用于流式过程卡的初始态判定，最终意图裁决始终以
 * 后端确定性检测为准。
 */
export const CAREER_INTENT_KEYWORDS: readonly string[] = [
  "生涯规划",
  "职业规划",
  "职业发展",
  "职业路径",
  "职业方向",
  "生涯方向",
  "就业方向",
  "求职规划",
  "职业选择",
  "晋升路径",
  "职业目标",
  "未来规划",
  "人生规划",
];
