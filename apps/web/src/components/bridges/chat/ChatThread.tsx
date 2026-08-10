"use client";

import { useEffect, useRef } from "react";

import {
  MessageList,
  type ChatMessage,
} from "@/components/bridges/MessageList";

import styles from "./chat.module.css";

interface ChatThreadProps {
  messages: ChatMessage[];
  onRetry?: (messageId: string) => void;
  onStop?: () => void;
  onTeachingSkip?: (messageId: string) => void;
  onTeachingBeginnerStart?: (messageId: string) => void;
  conversationId?: string;
  /** Issue 30：TTS 能力可用性（账户级探测快照） */
  tts?: { available: boolean; reason?: string };
  /** Issue 31：图片任务成功（资产落库）后刷新消息列表 */
  onRefreshMessages?: () => void;
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
  conversationId,
  tts,
  onRefreshMessages,
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
          onStop={onStop}
          onTeachingSkip={onTeachingSkip}
          onTeachingBeginnerStart={onTeachingBeginnerStart}
          conversationId={conversationId}
          tts={tts}
          onRefreshMessages={onRefreshMessages}
        />
      </div>
      <p className="sc-visually-hidden" role="status" aria-live="polite">
        {announcement}
      </p>
    </div>
  );
}
