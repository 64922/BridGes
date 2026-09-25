import type { IconName } from "@/components/design-system/Icon";
import type { ChatMessageProjection } from "@/lib/api";

/**
 * V2 Issue 11/12：日常聊天可显式选择的模块（论文搜索与校园通勤已接入子图）。
 *
 * 这里只有菜单/历史标签用的中文名称与说明；模块的检索行为完全由服务端
 * 在显式派发后执行。``id`` 与后端 ``ChatModuleId`` 取值一致，
 * 随每条用户消息持久化——历史的模块标识只读消息记录，不随新选择改变。
 */
export interface ChatModuleOption {
  id: "paper" | "commute";
  label: string;
  description: string;
  icon: IconName;
}

export const CHAT_MODULES: readonly ChatModuleOption[] = [
  {
    id: "paper",
    label: "论文搜索",
    description: "按主题检索 arXiv 论文，给出阅读顺序与真实链接",
    icon: "paperSearch",
  },
  {
    id: "commute",
    label: "校园通勤",
    description: "按步行／自行车／电动车查校内路线，只画高德返回的路径",
    icon: "route",
  },
];

export type ChatModuleSelectionId = ChatModuleOption["id"];

/** 模块 ID → 中文名称（历史消息标识；未接入的模块没有中文名，不臆造也不回显英文 ID）。 */
export function chatModuleLabel(moduleId: string | null | undefined): string | null {
  if (!moduleId) return null;
  return CHAT_MODULES.find((module) => module.id === moduleId)?.label ?? null;
}

/** 模块 ID → 图标名（未知取值用模块通用图标兜底）。 */
export function chatModuleIcon(moduleId: string | null | undefined): IconName {
  return CHAT_MODULES.find((module) => module.id === moduleId)?.icon ?? "chatBubble";
}

/**
 * 历史里最后一条论文消息是否仍在等待用户回答（V2 Issue 11 等待状态恢复）。
 *
 * 服务端把澄清问题随助手消息持久化，下一条回复要在同一模块里继续；重开
 * 对话后输入区的选择会丢，所以这里据权威历史恢复「已选论文搜索」——
 * 它是可见、可移除的标签，不是暗中派发。
 */
export function hasPendingPaperClarification(
  messages: readonly ChatMessageProjection[]
): boolean {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const search = messages[index]?.paper_search;
    if (!search) continue;
    if (search.pending?.module_id === "paper") return true;
    if (search.status === "success" || search.status === "empty") return false;
  }
  return false;
}

/**
 * 历史里最后一条通勤消息是否仍在等待用户回答（V2 Issue 12 等待状态恢复）。
 *
 * 通勤的等待只有一种原因：缺少起终点或方式、地点无匹配、候选冲突。最后一条
 * 带通勤投影的消息即权威结论——它在等就恢复「已选校园通勤」，否则不恢复。
 */
export function hasPendingCommuteClarification(
  messages: readonly ChatMessageProjection[]
): boolean {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const route = messages[index]?.commute_route;
    if (!route) continue;
    return route.pending?.module_id === "commute";
  }
  return false;
}
