"use client";

import { useEffect, useRef } from "react";

import { MessageList } from "@/components/bridges/MessageList";
import type { ChatMessageView } from "@/lib/chat-message-view";
import type {
  ChatAttachmentProjection,
  ChatModuleId,
  CitationProjection,
  PaperSearchProjection,
} from "@/lib/api";

import styles from "./chat.module.css";

interface ChatThreadProps {
  messages: ChatMessageView[];
  onRetry?: (messageId: string) => void;
  onStop?: () => void;
  onTeachingSkip?: (messageId: string) => void;
  onTeachingBeginnerStart?: (messageId: string) => void;
  /** V2 Issue 11：点击模块建议（以该轮原文显式启动模块） */
  onUseModuleSuggestion?: (messageId: string, moduleId: ChatModuleId) => void;
  conversationId?: string;
  /** Issue 31：图片任务成功（资产落库）后刷新消息列表 */
  onRefreshMessages?: () => void;
  /** 打开该轮真实引用对应的右侧资料面板 */
  onOpenCitation?: (
    messageId: string,
    citation: CitationProjection,
    trigger: HTMLButtonElement
  ) => void;
  /** 打开该轮论文的右侧资料面板 */
  onOpenPaper?: (
    messageId: string,
    search: PaperSearchProjection,
    trigger: HTMLButtonElement
  ) => void;
  /** 打开消息附件的右侧预览面板 */
  onOpenAttachment?: (
    messageId: string,
    attachment: ChatAttachmentProjection,
    trigger: HTMLButtonElement
  ) => void;
  /** 页面级状态播报（不逐 token 朗读正文，只播报状态转换） */
  announcement?: string | null;
}

/**
 * 聊天消息流：渲染 + 自动滚动 + 屏幕阅读器状态播报。
 *
 * 流式更新只更新正文与光标，不移动焦点、不把正文放进 live region——
 * 播报仅在状态转换时触发一次（生成中/完成/停止/失败）。
 */
export function ChatThread({
  messages,
  onRetry,
  onStop,
  onTeachingSkip,
  onTeachingBeginnerStart,
  onUseModuleSuggestion,
  conversationId,
  onRefreshMessages,
  onOpenCitation,
  onOpenPaper,
  onOpenAttachment,
  announcement,
}: ChatThreadProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const pinnedToBottom = useRef(true);

  // 只在会话内容真正变化时跟随底部；打开/关闭资料面板等纯布局变化不应
  // 因为消息数组重建而把用户位置拉回底部。
  const lastMessage = messages[messages.length - 1];
  const autoScrollKey = `${messages.length}:${lastMessage?.id ?? ""}:${
    lastMessage?.plainText.length ?? 0
  }:${lastMessage?.status ?? ""}`;

  useEffect(() => {
    const el = scrollRef.current;
    if (el && pinnedToBottom.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [autoScrollKey]);

  const handleScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    pinnedToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
  };

  return (
    <div className={styles.thread} ref={scrollRef} onScroll={handleScroll} data-testid="chat-thread">
      <div className={styles.threadInner}>
        <MessageList
          messages={messages}
          onRetry={onRetry}
          onStop={onStop}
          onTeachingSkip={onTeachingSkip}
          onTeachingBeginnerStart={onTeachingBeginnerStart}
          onUseModuleSuggestion={onUseModuleSuggestion}
          conversationId={conversationId}
          onRefreshMessages={onRefreshMessages}
          onOpenCitation={onOpenCitation}
          onOpenPaper={onOpenPaper}
          onOpenAttachment={onOpenAttachment}
        />
      </div>
      <p className="sc-visually-hidden" role="status" aria-live="polite">
        {announcement}
      </p>
    </div>
  );
}
