"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { ChatSendErrorBanner } from "@/components/bridges/chat/ChatSendErrorBanner";
import { Composer } from "@/components/bridges/Composer";
import { AppShell } from "@/components/layout/AppShell";
import { chatPromptKey } from "@/lib/chat-flow";
import { ApiError, createChatConversation } from "@/lib/api";

import styles from "@/components/bridges/chat/chat.module.css";

/**
 * 新聊天落地页（登录后的默认入口，ADR-0001 聊天优先主轴）。
 *
 * Issue 11：完整对话外壳与输入区。发送时先创建对话，再携带待发送消息
 * 跳转到对话页自动发送；未配置 Key / 能力不可用时由服务端预检返回
 * 可操作中文提示，并给出设置入口。
 */
export function NewChatHome() {
  const router = useRouter();
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<{ message: string; code?: string } | null>(null);

  const handleSend = async (text: string) => {
    setSending(true);
    setSendError(null);
    try {
      const conversation = await createChatConversation();
      sessionStorage.setItem(chatPromptKey(conversation.conversation_id), text);
      router.push(`/chat/${conversation.conversation_id}`);
    } catch (error) {
      setSendError({
        message: error instanceof Error ? error.message : "创建对话失败，请稍后重试。",
        code: error instanceof ApiError ? error.code : undefined,
      });
      setSending(false);
    }
  };

  return (
    <AppShell mode="account" showSkipLink={false}>
      <div className={styles.chatShell}>
        <main
          id="main-content"
          tabIndex={-1}
          data-testid="main-content"
          className={styles.chatMain}
        >
          <div className={styles.greeting}>
            <p className="sc-landmark-label">长期科学学习与表达伙伴</p>
            <h1 className={styles.greetingTitle}>有什么可以帮你的？</h1>
            <p className={styles.greetingHint}>
              向 BridGes 提问，或描述你的学习目标。回答会逐字呈现，随时可以停止、重试，并在下次打开时完整恢复。
            </p>
            {sendError && (
              <ChatSendErrorBanner
                message={sendError.message}
                code={sendError.code}
                align="center"
              />
            )}
          </div>
          <div className={styles.composerWrap}>
            <div className={styles.composerInner}>
              <Composer
                onSend={(text) => void handleSend(text)}
                generating={sending}
                onStop={() => setSending(false)}
              />
            </div>
          </div>
        </main>
      </div>
    </AppShell>
  );
}
