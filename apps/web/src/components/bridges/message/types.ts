import type { ReactNode } from "react";

import type { ChatMessage } from "@/lib/chat-message";

/**
 * 呈现层视图模型：在历史/流式边界组装。
 * body 只承载调用方已经决定好的富正文；缺省时按 plainText 渲染。
 */
export interface ChatMessageView extends ChatMessage {
  body?: ReactNode;
}
