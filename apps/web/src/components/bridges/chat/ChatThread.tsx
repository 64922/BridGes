"use client";

import { useEffect, useRef } from "react";

import {
  MessageList,
  type ChatMessage,
  type ThreadModeEvent,
} from "@/components/bridges/MessageList";
import type { ChatAttachmentProjection } from "@/lib/api";

import styles from "./chat.module.css";

interface ChatThreadProps {
  messages: (ChatMessage | ThreadModeEvent)[];
  onRetry?: (messageId: string) => void;
  onDownloadAttachment?: (attachment: ChatAttachmentProjection) => void;
  onDeleteAttachment?: (messageId: string, attachment: ChatAttachmentProjection) => void;
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
  onDownloadAttachment,
  onDeleteAttachment,
  announcement,
}: ChatThreadProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const pinnedToBottom = useRef(true);

  useEffect(() => {
    const el = scrollRef.current;
    if (el && pinnedToBottom.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages]);

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
          onDownloadAttachment={onDownloadAttachment}
          onDeleteAttachment={onDeleteAttachment}
        />
      </div>
      <p className="sc-visually-hidden" role="status" aria-live="polite">
        {announcement}
      </p>
    </div>
  );
}
