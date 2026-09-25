import type { IconName } from "@/components/design-system/Icon";
import type { ChatMessageProjection } from "@/lib/api";

/**
 * V2 Issue 11：日常聊天可显式选择的模块（当前论文搜索、贴吧信息搜集接入了子图）。
 *
 * 这里只有菜单/历史标签用的中文名称与说明；模块的检索行为完全由服务端
 * 在显式派发后执行。``id`` 与后端 ``ChatModuleId`` 取值一致，
 * 随每条用户消息持久化——历史的模块标识只读消息记录，不随新选择改变。
 */
export interface ChatModuleOption {
  id: "paper" | "tieba";
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
    id: "tieba",
    label: "贴吧信息搜集",
    description: "只看华东交通大学吧的公开帖子，拿不到回复时只给帖链",
    icon: "tiebaThread",
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
 * 历史里仍在等待用户回答的模块（V2 Issue 11/14 等待状态恢复）。
 *
 * 服务端把澄清问题随助手消息持久化，下一条回复要在同一模块里继续；重开
 * 对话后输入区的选择会丢，所以这里据权威历史恢复「已选某个模块」——
 * 它是可见、可移除的标签，不是暗中派发。
 *
 * 只看**最新一条携带模块投影的消息**：它就是该模块线程的当前状态。它带着
 * 等待就是等待中（恢复），否则这一轮已有结论（成功、帖链降级、失败、停止
 * 都算），不再往更早的历史里找回一个已经被取代的等待。中间夹着的普通对话
 * 消息不带模块投影，不影响判断。
 */
export function pendingClarificationModule(
  messages: readonly ChatMessageProjection[]
): ChatModuleSelectionId | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    for (const search of [message?.paper_search, message?.tieba_research]) {
      if (!search) continue;
      const option = CHAT_MODULES.find((module) => module.id === search.pending?.module_id);
      return option ? option.id : null;
    }
  }
  return null;
}
