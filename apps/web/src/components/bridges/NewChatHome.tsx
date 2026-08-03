"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { ChatSendErrorBanner } from "@/components/bridges/chat/ChatSendErrorBanner";
import { Composer } from "@/components/bridges/Composer";
import { ModeToggle, type ChatMode } from "@/components/bridges/ModeToggle";
import { RotatingQuote } from "@/components/bridges/RotatingQuote";
import { SuggestionCards } from "@/components/bridges/SuggestionCards";
import { AppShell } from "@/components/layout/AppShell";
import { chatPromptKey } from "@/lib/chat-flow";
import { ApiError, createChatConversation } from "@/lib/api";

import styles from "@/components/bridges/chat/chat.module.css";

/**
 * 新聊天落地页（登录后的默认入口，ADR-0001 聊天优先主轴）。
 *
 * Issue 13：ChatGPT 式电脑端空白态——输入区顶部按参考网页可变文字
 * 机制轮换恰好五条已核查学习名言（尊重减少动态效果设置）；输入区
 * 下方为「论文搜索 / 文章人味化 / 生涯规划助手」三张原创图标建议卡，
 * 点击预填结构化意图到输入区，经正常消息流发送（不跳过授权、审计与
 * 对话保存）。无临时聊天、无模型选择器、无实时语音入口。
 *
 * 发送时先创建对话，再携带待发送消息跳转到对话页自动发送；未配置
 * Key / 能力不可用时由服务端预检返回可操作中文提示，并给出设置入口。
 */
export function NewChatHome() {
  const router = useRouter();
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<{ message: string; code?: string } | null>(null);
  const [prefill, setPrefill] = useState<{ text: string; nonce: number } | null>(null);
  // 新聊天默认日常陪伴；用户可切为学习模式后发送（Issue 14，ADR-0022）
  const [mode, setMode] = useState<ChatMode>("companion");
  // 递增计数器保证每次建议卡点击都触发预填（同毫秒点击不会丢）
  const prefillCounter = useRef(0);

  const handleSend = async (text: string) => {
    setSending(true);
    setSendError(null);
    try {
      const conversation = await createChatConversation(undefined, mode);
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
          <div className={styles.blankState}>
            <div className={styles.blankStateInner}>
              <div className={styles.greetingCompact}>
                <p className="sc-landmark-label">长期科学学习与表达伙伴</p>
                <h1 className={styles.greetingTitle}>有什么可以帮你的？</h1>
              </div>
              <RotatingQuote />
              <div className={styles.modeRow}>
                <ModeToggle value={mode} onChange={setMode} />
              </div>
              {sendError && (
                <ChatSendErrorBanner
                  message={sendError.message}
                  code={sendError.code}
                  align="center"
                />
              )}
              <Composer
                onSend={(text) => void handleSend(text)}
                generating={sending}
                onStop={() => setSending(false)}
                prefill={prefill}
              />
              {sending && (
                <p role="status" className={styles.blankStateNote}>
                  正在创建对话…
                </p>
              )}
              <SuggestionCards
                onPrefill={(text) => {
                  prefillCounter.current += 1;
                  setPrefill({ text, nonce: prefillCounter.current });
                }}
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
