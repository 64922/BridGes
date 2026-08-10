"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { ChatSendErrorBanner } from "@/components/bridges/chat/ChatSendErrorBanner";
import { Composer } from "@/components/bridges/Composer";
import { ModeToggle, type ChatMode } from "@/components/bridges/ModeToggle";
import { RotatingQuote } from "@/components/bridges/RotatingQuote";
import { SuggestionCards } from "@/components/bridges/SuggestionCards";
import { AppShell } from "@/components/layout/AppShell";
import { CHAT_LIST_CHANGED_EVENT } from "@/lib/recent-conversations";
import {
  ApiError,
  createChatConversation,
  startFirstTurn,
} from "@/lib/api";

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
 * Issue 03：发送走「原子首轮」命令——服务端在同一事务内创建会话、用户
 * 消息、助手占位与 queued 运行，返回完整投影；客户端收到成功响应后再
 * 导航，``sessionStorage`` 不再承担业务真相。失败停留在首页且输入不丢失，
 * 不产生不可见空草稿；幂等键抵御双击与网络重放。
 */
export function NewChatHome() {
  const router = useRouter();
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<{ message: string } | null>(null);
  const [prefill, setPrefill] = useState<{ text: string; nonce: number } | null>(null);
  // 新聊天默认日常陪伴；用户可切为学习模式后发送（Issue 14，ADR-0022）
  const [mode, setMode] = useState<ChatMode>("companion");
  // 递增计数器保证每次建议卡点击都触发预填（同毫秒点击不会丢）
  const prefillCounter = useRef(0);
  // 听写需要真实会话时复用的预建会话标识。
  const preparedConversationRef = useRef<string | undefined>();
  // Issue 03：同步防重——setState 是异步的，双击/快速连点会在 React
  // 渲染前触发多次 onSend；ref 在本次首轮完成前拦截后续提交。
  const firstTurnInFlightRef = useRef(false);
  // Issue 03：幂等键复用——请求成功（服务端已创建）后清空；网络/5xx
  // 失败（响应丢失、服务端可能已提交）时保留同键重试，由服务端幂等
  // 收敛到同一会话；4xx 是服务端明确拒绝（未产生数据），清空允许下次
  // 以新意图全新提交。这使「导航重试」不会产生第二份会话/消息/run。
  const idempotencyKeyRef = useRef<string | null>(null);

  const ensureConversation = async (): Promise<string | undefined> => {
    if (preparedConversationRef.current) return preparedConversationRef.current;
    try {
      const conversation = await createChatConversation();
      preparedConversationRef.current = conversation.conversation_id;
      return conversation.conversation_id;
    } catch (error) {
      setSendError({
        message: error instanceof Error ? error.message : "创建对话失败，请稍后重试。",
      });
      return undefined;
    }
  };

  /** Issue 03：原子首轮统一入口；能力由服务端根据自然语言路由。 */
  const submitFirstTurn = async (options: {
    content: string;
    conversationId?: string;
  }): Promise<boolean> => {
    if (firstTurnInFlightRef.current) return false;
    firstTurnInFlightRef.current = true;
    if (idempotencyKeyRef.current === null) {
      idempotencyKeyRef.current = crypto.randomUUID();
    }
    setSending(true);
    setSendError(null);
    try {
      const result = await startFirstTurn({
        content: options.content,
        idempotencyKey: idempotencyKeyRef.current,
        conversationId: options.conversationId,
        mode,
      });
      // 首轮事务已成功：侧栏立即刷新（服务端列表对该会话立即可见，
      // 不等待助手完成），随后导航到会话页从服务端投影恢复。幂等键
      // 一次性：成功后清空，下次发送是新意图。
      idempotencyKeyRef.current = null;
      window.dispatchEvent(new Event(CHAT_LIST_CHANGED_EVENT));
      router.push(`/chat/${result.conversation.conversation_id}`);
      return true;
    } catch (error) {
      // 原子命令失败不产生任何会话/消息：停留在可编辑首页，输入保留。
      // 4xx 是服务端明确拒绝（未产生数据），清空幂等键允许下次全新
      // 提交；网络/5xx（响应丢失、服务端可能已创建）保留同键重试，
      // 由服务端幂等收敛到同一会话——「导航重试」不会产生第二份数据。
      if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
        idempotencyKeyRef.current = null;
      }
      setSendError({
        message: error instanceof Error ? error.message : "发送失败，请稍后重试。",
      });
      setSending(false);
      return false;
    } finally {
      firstTurnInFlightRef.current = false;
    }
  };

  const handleSend = async (text: string): Promise<boolean> => {
    return submitFirstTurn({
      content: text,
      conversationId: preparedConversationRef.current,
    });
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
                <ChatSendErrorBanner message={sendError.message} align="center" />
              )}
              <Composer
                onSend={handleSend}
                ensureConversation={ensureConversation}
                generating={sending}
                onStop={() => setSending(false)}
                prefill={prefill}
              />
              {sending && (
                <p role="status" className={styles.blankStateNote}>
                  正在创建对话并发送…
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
