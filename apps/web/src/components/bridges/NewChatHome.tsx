"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { ChatSendErrorBanner } from "@/components/bridges/chat/ChatSendErrorBanner";
import { Composer } from "@/components/bridges/Composer";
import { ModeToggle, type ChatMode } from "@/components/bridges/ModeToggle";
import { RotatingQuote } from "@/components/bridges/RotatingQuote";
import { AppShell } from "@/components/layout/AppShell";
import { MAIN_CONTENT_ID } from "@/components/layout/MainContent";
import { ApiError, startFirstTurn } from "@/lib/api";
import { type ChatModuleSelectionId } from "@/lib/chat-modules";
import { CHAT_LIST_CHANGED_EVENT } from "@/lib/recent-conversations";

import styles from "@/components/bridges/chat/chat.module.css";

/**
 * 登录后的新聊天首页。
 *
 * 首条消息通过原子首轮命令创建并锁定会话模式。
 */
export function NewChatHome() {
  const router = useRouter();
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<{ message: string } | null>(null);
  const [mode, setMode] = useState<ChatMode>("companion");
  // V2 Issue 11：首页也可显式选择模块（随首条用户消息持久化）。
  const [moduleId, setModuleId] = useState<ChatModuleSelectionId | null>(null);
  const firstTurnInFlightRef = useRef(false);
  const idempotencyKeyRef = useRef<string | null>(null);
  const submitFirstTurn = async (content: string, attachmentIds: string[] = []): Promise<boolean> => {
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
        mode,
        // Issue 05：新聊天页直发照片——账户域草稿随首轮原子绑定。
        attachmentIds,
        // V2 Issue 11：显式模块随首轮消息保存（未选择时不提交）。
        moduleId: mode === "companion" ? moduleId ?? undefined : undefined,
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
          id={MAIN_CONTENT_ID}
          tabIndex={-1}
          data-testid="main-content"
          className={styles.chatMain}
        >
          <h1 className="sc-visually-hidden">新聊天</h1>
          <div className={styles.newChatMode}>
            <ModeToggle
              value={mode}
              onChange={(nextMode) => {
                setMode(nextMode);
                if (nextMode === "study") setModuleId(null);
              }}
              disabled={sending}
            />
          </div>
          <div className={styles.blankState} data-testid="new-chat-home">
            <div className={styles.blankStateInner}>
              {mode === "study" ? (
                <p className={styles.greetingTitle}>上传本节书页照片开始预习</p>
              ) : (
                <RotatingQuote />
              )}
              {sendError && <ChatSendErrorBanner message={sendError.message} align="center" />}
              <Composer
                variant="new-chat"
                onSend={submitFirstTurn}
                generating={sending}
                moduleId={moduleId}
                onModuleChange={setModuleId}
                mode={mode}
              />
            </div>
            <p className={styles.blankStateNote}>
              BridGes 的回答会标注依据与来源；重要内容请核对引用。
            </p>
          </div>
        </main>
      </div>
    </AppShell>
  );
}
