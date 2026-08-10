"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { ChatSendErrorBanner } from "@/components/bridges/chat/ChatSendErrorBanner";
import { Composer } from "@/components/bridges/Composer";
import { ModeToggle, type ChatMode } from "@/components/bridges/ModeToggle";
import { RotatingQuote } from "@/components/bridges/RotatingQuote";
import { AppShell } from "@/components/layout/AppShell";
import { ApiError, createChatConversation, startFirstTurn } from "@/lib/api";
import { CHAT_LIST_CHANGED_EVENT } from "@/lib/recent-conversations";

import styles from "@/components/bridges/chat/chat.module.css";

/**
 * 登录后的新聊天首页。
 *
 * 首页只负责收集自然语言首轮和会话模式；论文、改写、图片、视频等能力
 * 由服务端根据正文路由。听写仍保留，但只有用户真正点击听写时才预建
 * 一个带模式的空会话，普通打开、输入和发送都直接走原子首轮命令。
 */
export function NewChatHome() {
  const router = useRouter();
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<{ message: string } | null>(null);
  const [mode, setMode] = useState<ChatMode>("companion");
  const [modeLocked, setModeLocked] = useState(false);
  const preparedConversationRef = useRef<string | undefined>();
  const firstTurnInFlightRef = useRef(false);
  const idempotencyKeyRef = useRef<string | null>(null);

  /** 只在听写需要会话归属时调用；普通首轮不会走这个预建分支。 */
  const ensureConversation = async (): Promise<string | undefined> => {
    if (preparedConversationRef.current) return preparedConversationRef.current;
    try {
      const conversation = await createChatConversation(undefined, mode);
      preparedConversationRef.current = conversation.conversation_id;
      setModeLocked(true);
      return conversation.conversation_id;
    } catch (error) {
      setSendError({
        message: error instanceof Error ? error.message : "创建对话失败，请稍后重试。",
      });
      return undefined;
    }
  };

  const submitFirstTurn = async (
    content: string,
    conversationId?: string
  ): Promise<boolean> => {
    if (firstTurnInFlightRef.current) return false;
    firstTurnInFlightRef.current = true;
    if (idempotencyKeyRef.current === null) {
      idempotencyKeyRef.current = crypto.randomUUID();
    }
    setSending(true);
    setSendError(null);

    try {
      const result = await startFirstTurn({
        content,
        idempotencyKey: idempotencyKeyRef.current,
        conversationId,
        mode,
      });
      idempotencyKeyRef.current = null;
      window.dispatchEvent(new Event(CHAT_LIST_CHANGED_EVENT));
      router.push(`/chat/${result.conversation.conversation_id}`);
      return true;
    } catch (error) {
      // 明确拒绝时允许下一次意图使用新键；网络/5xx 保留同键交给服务端
      // 幂等合同收敛，避免重试产生第二个会话。
      if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
        idempotencyKeyRef.current = null;
      }
      setSendError({
        message: error instanceof Error ? error.message : "发送失败，请稍后重试。",
      });
      setSending(false);
      requestAnimationFrame(() => document.getElementById("composer-input")?.focus());
      return false;
    } finally {
      firstTurnInFlightRef.current = false;
    }
  };

  return (
    <AppShell showSkipLink={false}>
      <div className={styles.chatShell}>
        <main
          id="main-content"
          tabIndex={-1}
          data-testid="main-content"
          className={styles.chatMain}
        >
          <h1 className="sc-visually-hidden">新聊天</h1>
          <div className={styles.newChatMode}>
            <ModeToggle value={mode} onChange={setMode} disabled={sending || modeLocked} />
          </div>
          <div className={styles.blankState} data-testid="new-chat-home">
            <div className={styles.blankStateInner}>
              <RotatingQuote />
              {sendError && <ChatSendErrorBanner message={sendError.message} align="center" />}
              <Composer
                variant="new-chat"
                onSend={(text, preparedConversationId) =>
                  submitFirstTurn(text, preparedConversationId)
                }
                ensureConversation={ensureConversation}
                generating={sending}
              />
              <p className={styles.blankStateNote}>
                BridGes 的回答会标注依据与来源；重要内容请核对引用。
              </p>
            </div>
          </div>
        </main>
      </div>
    </AppShell>
  );
}
