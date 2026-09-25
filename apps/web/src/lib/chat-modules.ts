import type { IconName } from "@/components/design-system/Icon";
import type { ChatMessageProjection } from "@/lib/api";

/**
 * V2 Issue 11/13：日常聊天可显式选择的模块（论文搜索、学习资料推荐）。
 *
 * 这里只有菜单/历史标签用的中文名称与说明；模块的检索行为完全由服务端
 * 在显式派发后执行。``id`` 与后端 ``ChatModuleId`` 取值一致，
 * 随每条用户消息持久化——历史的模块标识只读消息记录，不随新选择改变。
 */
export interface ChatModuleOption {
  id: "paper" | "resources";
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
    id: "resources",
    label: "学习资料推荐",
    description: "按技术方向给出图书与哔哩哔哩视频清单，按由浅入深排列",
    icon: "learningProject",
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
 * 历史里最后一条仍等待用户回答的消息属于哪个模块（V2 Issue 11/13 等待状态恢复）。
 *
 * 服务端把澄清问题随助手消息持久化，下一条回复要在同一模块里继续；重开
 * 对话后输入区的选择会丢，所以这里据权威历史恢复「已选模块」——它是可见、
 * 可移除的标签，不是暗中派发。倒序找到的第一个等待状态即为当前等待（更晚的
 * 完成结果会取代它），因此另一个模块更早的陈旧等待不会被误恢复。
 */
export function pendingClarificationModule(
  messages: readonly ChatMessageProjection[]
): ChatModuleSelectionId | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (!message) continue;
    for (const projection of [message.paper_search, message.learning_resources]) {
      if (!projection) continue;
      if (projection.pending) {
        // 只恢复真正可选中的模块：其他取值（未接入/已退役）没有可见标签，宁可不选。
        const pendingId = projection.pending.module_id;
        const option = CHAT_MODULES.find((module) => module.id === pendingId);
        return option ? option.id : null;
      }
      if (projection.status === "success" || projection.status === "empty") {
        return null;
      }
    }
  }
  return null;
}
