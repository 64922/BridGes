import type { ReactNode } from "react";

import type { ChatMessage } from "./chat-message";
import type { StudyTurnState } from "./study-presentation";

/**
 * 呈现层视图模型：在历史/流式边界组装。
 * body 只承载调用方已经决定好的富正文；缺省时按 plainText 渲染。
 */
export interface ChatMessageView extends ChatMessage {
  body?: ReactNode;
  /** 工单 12：该助手回合真实对应的学习状态；没有归属时不渲染。 */
  studyTurn?: StudyTurnState;
}
