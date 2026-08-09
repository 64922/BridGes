"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { ChatSendErrorBanner } from "@/components/bridges/chat/ChatSendErrorBanner";
import { Composer } from "@/components/bridges/Composer";
import { ModeToggle, type ChatMode } from "@/components/bridges/ModeToggle";
import { RotatingQuote } from "@/components/bridges/RotatingQuote";
import { HumanizerDialog } from "@/components/bridges/HumanizerDialog";
import { CareerPlanningDialog } from "@/components/bridges/CareerPlanningDialog";
import { VideoDialog } from "@/components/bridges/VideoDialog";
import type {
  HumanizerSkillInput,
  VideoRequestPayload,
} from "@/lib/api";
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
  // 预建空会话（Composer 选择附件上传时才创建，作为附件归属上下文）
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

  const [humanizerOpen, setHumanizerOpen] = useState(false);
  const [careerOpen, setCareerOpen] = useState(false);
  const [videoOpen, setVideoOpen] = useState(false);
  // Issue 36：新聊天首页暂存的插件选择（随首轮写入会话；chip 与
  // 真实选择器共用，随对话持久化；停用/卸载/撤权由服务端清洗解释）。
  // Issue 34：插件页「在聊天中使用 humanizer」意图——消费即删除，
  // 防止刷新或 StrictMode 双触发重复打开。
  /** Issue 03：原子首轮统一入口——普通消息/生涯规划/图片/视频/人味化
   *  全部经同一命令提交，成功后导航到会话页并刷新侧栏最近列表。 */
  const submitFirstTurn = async (options: {
    content: string;
    conversationId?: string;
    useKnowledgeBase?: boolean;
    skillId?: string;
    skillInput?: HumanizerSkillInput;
    video?: VideoRequestPayload;
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
        idempotency_key: idempotencyKeyRef.current,
        conversation_id: options.conversationId,
        mode,
        // Issue 04：人味化改写默认关闭知识库（只有用户显式勾选才开启）；
        // 普通消息沿用既有默认开启语义。
        use_knowledge_base: options.useKnowledgeBase ?? true,
        ...(options.skillId !== undefined ? { skill_id: options.skillId } : {}),
        ...(options.skillInput !== undefined ? { skill_input: options.skillInput } : {}),
        ...(options.video !== undefined ? { video: options.video } : {}),
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

  /** Issue 29：首页提交生涯规划任务（真实消息流）。 */
  const handleCareerSubmit = async (
    content: string
  ): Promise<boolean> => {
    return submitFirstTurn({ content });
  };

  /** Issue 32：首页提交视频任务（video 载荷随首轮落库，任务异步执行）。 */
  const handleVideoSubmit = async (payload: { prompt: string }): Promise<boolean> => {
    const videoPayload: VideoRequestPayload = { prompt: payload.prompt };
    return submitFirstTurn({ content: payload.prompt, video: videoPayload });
  };

  /** Issue 28：首页提交人味化任务（SKILL 载荷随首轮落库，可重试）。 */
  const handleHumanizerSubmit = async (
    content: string,
    skillInput: HumanizerSkillInput,
    useKnowledgeBase: boolean
  ): Promise<boolean> => {
    return submitFirstTurn({
      content,
      conversationId: preparedConversationRef.current,
      useKnowledgeBase,
      skillId: skillInput.skill_id,
      skillInput,
    });
  };

  const handleSend = async (
    text: string,
    preparedConversationId?: string
  ): Promise<boolean> => {
    // 新消息不再携带聊天附件或学习项目归属。
    return submitFirstTurn({
      content: text,
      conversationId: preparedConversationId ?? preparedConversationRef.current,
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
                onOpenHumanizer={() => setHumanizerOpen(true)}
                onOpenCareer={() => setCareerOpen(true)}
                onOpenVideo={() => setVideoOpen(true)}
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
                onHumanizer={() => setHumanizerOpen(true)}
                onCareer={() => setCareerOpen(true)}
              />
              <p className={styles.blankStateNote}>
                BridGes 的回答会标注依据与来源；重要内容请核对引用。
              </p>
            </div>
          </div>
        </main>
      </div>
      <HumanizerDialog
        open={humanizerOpen}
        onClose={() => setHumanizerOpen(false)}
        onSubmit={handleHumanizerSubmit}
      />
      <CareerPlanningDialog
        open={careerOpen}
        onClose={() => setCareerOpen(false)}
        ensureConversation={ensureConversation}
        onSubmit={handleCareerSubmit}
      />
      <VideoDialog
        open={videoOpen}
        onClose={() => setVideoOpen(false)}
        onSubmit={handleVideoSubmit}
      />
    </AppShell>
  );
}
