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
