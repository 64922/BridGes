import type { IconName } from "@/components/design-system/Icon";
import type { ChatMessageProjection } from "@/lib/api";

/**
 * 日常聊天可选择的三个模块：论文搜索、校园通勤、贴吧信息搜集。
 *
 * 这里只有菜单/历史标签用的中文名称与说明；模块的检索行为完全由服务端
 * 在派发后执行。``id`` 与后端 ``ActiveChatModuleId`` 取值一致，
 * 随每条用户消息持久化——历史的模块标识只读消息记录，不随新选择改变。
 */
export interface ChatModuleOption {
  id: "paper" | "commute" | "tieba";
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
  {
    id: "tieba",
    label: "贴吧信息搜集",
    description: "只看华东交通大学吧的公开帖子，拿不到回复时只给帖链",
    icon: "tiebaThread",
  },
];

export type ChatModuleSelectionId = ChatModuleOption["id"];

/** 退役模块仅用于历史结果的中文标签，不进入菜单或等待恢复。 */
const HISTORICAL_MODULE_LABELS = new Map([
  ["resources", "学习资料推荐"],
  ["career", "职业规划"],
  ["github", "GitHub 项目推荐"],
]);

export function isActiveChatModule(moduleId: string): moduleId is ChatModuleSelectionId {
  return CHAT_MODULES.some((module) => module.id === moduleId);
}

/** 模块 ID → 中文名称（历史消息标识；未接入的模块没有中文名，不臆造也不回显英文 ID）。 */
export function chatModuleLabel(moduleId: string | null | undefined): string | null {
  if (!moduleId) return null;
  return CHAT_MODULES.find((module) => module.id === moduleId)?.label
    ?? HISTORICAL_MODULE_LABELS.get(moduleId) ?? null;
}

/** 模块 ID → 图标名（未知取值用模块通用图标兜底）。 */
export function chatModuleIcon(moduleId: string | null | undefined): IconName {
  return CHAT_MODULES.find((module) => module.id === moduleId)?.icon ?? "chatBubble";
}

/**
 * 历史里仍在等待用户回答的模块（V2 Issue 11/13/14/15/16 等待状态恢复）。
 *
 * 服务端把澄清问题随助手消息持久化，下一条回复要在同一模块里继续；重开
 * 对话后输入区的选择会丢，所以这里据权威历史恢复「已选某个模块」——
 * 它是可见、可移除的标签，不是暗中派发。
 *
 * 只看**最新一条携带模块投影的消息**：它就是该模块线程的当前状态。它带着
 * 等待就是等待中（恢复），否则这一轮已有结论（成功、帖链降级、失败、停止、
 * 空结果都算），不再往更早的历史里找回一个已经被取代的等待。中间夹着的
 * 普通对话消息不带模块投影，不影响判断。通勤的等待状态形态不同，用
 * ``hasPendingCommuteClarification`` 单独判定。
 */
export function pendingClarificationModule(
  messages: readonly ChatMessageProjection[]
): ChatModuleSelectionId | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (!message) continue;
    for (const projection of [
      message.paper_search,
      message.tieba_research,
      message.learning_resources,
      message.career_plan,
      message.github_projects,
    ]) {
      if (!projection) continue;
      if (projection.pending) {
        // 只恢复真正可选中的模块：其他取值（未接入/已退役）没有可见标签，宁可不选。
        const pendingId = projection.pending.module_id;
        const option = CHAT_MODULES.find((module) => module.id === pendingId);
        return option ? option.id : null;
      }
      // 这一轮已有结论：更早的等待已被取代，不再往前找。
      return null;
    }
  }
  return null;
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
