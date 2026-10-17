"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";

import { StateBlock } from "@/components/bridges/StateBlock";
import { ChatSendErrorBanner } from "@/components/bridges/chat/ChatSendErrorBanner";
import { ChatThread } from "@/components/bridges/chat/ChatThread";
import { Composer } from "@/components/bridges/Composer";
import { StudyProgress } from "@/components/bridges/chat/StudyProgress";
import type { CapabilityAvailability } from "@/components/bridges/chat/capability-availability";
import { AppShell } from "@/components/layout/AppShell";
import { MAIN_CONTENT_ID } from "@/components/layout/MainContent";
import { buildThreadMessages } from "@/lib/chat-thread";
import { composeConversationMessages } from "@/lib/chat-run-view";
import { isIngestionSettled, isPhotoAttachment } from "@/lib/chat-attachments";
import {
  hasPendingCommuteClarification,
  pendingClarificationModule,
  type ChatModuleSelectionId,
} from "@/lib/chat-modules";
import { useConversationHistory } from "@/lib/use-conversation-history";
import { useConversationRun } from "@/lib/use-conversation-run";
import { useConversationStudy } from "@/lib/use-conversation-study";

import styles from "@/components/bridges/chat/chat.module.css";

/**
 * GQ-03/GQ-04：媒体能力（听写/朗读/图片/视频）由全局运行凭据驱动，
 * 不再按账户探测禁用入口——新账户无需任何个人 Qwen 配置即可使用。
 */
const MEDIA_ALWAYS_AVAILABLE: CapabilityAvailability = { available: true };

/**
 * 对话页：权威历史、运行控制、学习操作与页面布局的组合。
 *
 * 运行状态由 useConversationRun 唯一持有；历史由 useConversationHistory
 * 读取；学习操作由 useConversationStudy 保持原有服务调用。页面只负责
 * 组装渲染与不涉及运行生命周期的轮询、锚点。
 */
export default function ChatConversationPage() {
  const params = useParams<{ conversationId: string }>();
  const conversationId = params.conversationId;
  const router = useRouter();

  const { conversation, setConversation, loadState, loadError, load, revision } =
    useConversationHistory(conversationId);
  const run = useConversationRun({
    conversationId,
    conversation,
    ready: loadState === "ready",
    revision,
    load,
    setConversation,
  });
  const study = useConversationStudy({
    conversationId,
    load,
    reportError: run.reportError,
    announce: run.announce,
    router,
  });
  const {
    sendMessage,
    retry,
    stop,
    generating,
    activeRun,
    pendingUser,
    sendError,
    announcement,
  } = run;

  const [moduleId, setModuleId] = useState<ChatModuleSelectionId | null>(null);
  const resumeModuleCheckedRef = useRef<string | null>(null);

  useEffect(() => {
    resumeModuleCheckedRef.current = null;
    setModuleId(null);
  }, [conversationId]);

  // V2 Issue 06：已发送文件附件的解析在后台继续，卡片状态要刷新才会从
  // 「排队解析中」走到「已解析，可引用／解析失败／无法识别正文」。只要还有
  // 非终态的文件附件就轮流刷新对话，全部终态即停（有界兜底 2 分钟）；照片
  // 不参与解析，纯文字消息不触发任何轮询。
  const pendingFileParse = (conversation?.messages ?? []).some((message) =>
    (message.attachments ?? []).some(
      (attachment) =>
        !isPhotoAttachment(attachment.media_type) &&
        !isIngestionSettled(attachment.ingestion_status)
    )
  );
  useEffect(() => {
    if (!pendingFileParse) return;
    const poll = window.setInterval(() => void load(), 3000);
    const stopPolling = window.setTimeout(() => window.clearInterval(poll), 120_000);
    return () => {
      window.clearInterval(poll);
      window.clearTimeout(stopPolling);
    };
  }, [pendingFileParse, load]);

  // Issue 05：后台诊断独立于回复交付执行——只要还有待核验/核验中的任务就
  // 轮流刷新真实状态（有界兜底 2 分钟，与附件解析轮询同一节奏）；终态即停。
  const pendingDiagnosis = (conversation?.study_diagnosis_tasks ?? []).some(
    (task) => task.status === "pending" || task.status === "running"
  );
  useEffect(() => {
    if (!pendingDiagnosis) return;
    const poll = window.setInterval(() => void load(true), 3000);
    const stopPolling = window.setTimeout(() => window.clearInterval(poll), 120_000);
    return () => {
      window.clearInterval(poll);
      window.clearTimeout(stopPolling);
    };
  }, [pendingDiagnosis, load]);

  // V2 Issue 11/12/13/14/15：重开对话时若最后一条模块消息仍在等澄清，恢复输入
  // 区的模块选择（每条新终态只恢复一次），下一条回复从该处继续。
  useEffect(() => {
    if (!conversation || conversation.conversation_id !== conversationId) return;
    const latestAssistant = [...(conversation.messages ?? [])]
      .reverse()
      .find((message) => message.role === "assistant");
    if (!latestAssistant || latestAssistant.status === "streaming") return;
    if (resumeModuleCheckedRef.current === latestAssistant.message_id) return;
    resumeModuleCheckedRef.current = latestAssistant.message_id;
    const pendingModule = pendingClarificationModule(conversation.messages ?? []);
    if (pendingModule) {
      setModuleId(pendingModule);
    } else if (hasPendingCommuteClarification(conversation.messages ?? [])) {
      setModuleId("commute");
    }
  }, [conversation, conversationId]);

  // Issue 24：统一搜索跳转的消息锚点。?message=<id> 时等消息渲染完成后
  // 滚动到对应消息并短暂高亮（data-anchor-highlight，2s 后自动消退；
  // 高亮过渡走 --motion-* 令牌，prefers-reduced-motion 下趋近即时）。
  useEffect(() => {
    if (loadState !== "ready") return;
    const anchorId = new URLSearchParams(window.location.search).get("message");
    if (!anchorId) return;
    let clearHighlight: number | undefined;
    const poll = window.setInterval(() => {
      const element = document.getElementById(`msg-${anchorId}`);
      if (!element) return;
      window.clearInterval(poll);
      element.scrollIntoView({ block: "center" });
      element.setAttribute("data-anchor-highlight", "true");
      clearHighlight = window.setTimeout(
        () => element.removeAttribute("data-anchor-highlight"),
        2000
      );
    }, 100);
    const stopPolling = window.setTimeout(() => window.clearInterval(poll), 5000);
    return () => {
      window.clearInterval(poll);
      window.clearTimeout(stopPolling);
      if (clearHighlight !== undefined) window.clearTimeout(clearHighlight);
    };
  }, [loadState]);

  const skipTeachingQuestion = useCallback(() => {
    void sendMessage("跳过这道理解检查，我想继续学习。");
  }, [sendMessage]);

  const beginnerStartTeaching = useCallback(() => {
    void sendMessage("按初学者开始");
  }, [sendMessage]);

  const baseMessages = conversation
    ? buildThreadMessages(conversation.messages ?? [])
    : [];
  const threadMessages = composeConversationMessages(
    baseMessages,
    activeRun,
    pendingUser
  );

  return (
    <AppShell>
      <div className={styles.chatShell}>
        <main
          id={MAIN_CONTENT_ID}
          tabIndex={-1}
          data-testid="main-content"
          className={styles.chatMain}
        >
          <h1 className="sc-visually-hidden">{conversation?.title || "新对话"}</h1>
          {loadState === "loading" ? (
            <StateBlock
              kind="loading"
              title="正在加载对话"
              description="读取消息历史与生成状态。"
            />
          ) : loadState === "error" ? (
            <StateBlock
              kind="error"
              title="对话加载失败"
              description={loadError || "请检查连接后重试。"}
              actionLabel="重新加载"
              onAction={() => void load()}
            />
          ) : (
            <>
              {conversation?.mode === "study" && (
                <StudyProgress
                  study={conversation.study}
                  studyLocalProgress={conversation.study_local_progress}
                  studyGoal={conversation.study_goal}
                  studyGoalNotice={conversation.study_goal_notice}
                  studyPendingCheck={conversation.study_pending_check}
                  studyLastControl={conversation.study_last_control}
                  studyPause={conversation.study_pause}
                  studySummary={conversation.study_summary}
                  historyStatus={conversation.study_history_status}
                  studyDiagnosisTasks={conversation.study_diagnosis_tasks}
                  studyLearningRecord={conversation.study_learning_record}
                  studyRecordAdoptions={conversation.study_record_adoptions}
                  restart={study.restart}
                  busy={generating}
                  onRetryDiagnosis={study.retryDiagnosis}
                  onCorrectRecord={study.correctRecord}
                  onDeleteRecord={study.deleteRecord}
                />
              )}
              <ChatThread
                messages={threadMessages}
                onRetry={(messageId) => void retry(messageId)}
                onStop={() => void stop()}
                onTeachingSkip={() => skipTeachingQuestion()}
                onTeachingBeginnerStart={() => beginnerStartTeaching()}
                onUseModuleSuggestion={(messageId, suggestionModuleId) =>
                  void retry(messageId, suggestionModuleId)
                }
                conversationId={conversationId}
                onRefreshMessages={() => void load(true)}
                announcement={announcement}
              />
              {sendError && <ChatSendErrorBanner message={sendError} />}
              <div className={styles.composerWrap}>
                <div className={styles.composerInner}>
                  <p className={styles.modeLabel} data-testid="conversation-mode">
                    {conversation?.mode === "study" ? "学习模式" : "日常陪伴"}
                  </p>
                  <Composer
                    onSend={(text, attachmentIds) =>
                      sendMessage(text, attachmentIds, moduleId)
                    }
                    conversationId={conversationId}
                    generating={generating}
                    onStop={() => void stop()}
                    asr={MEDIA_ALWAYS_AVAILABLE}
                    moduleId={moduleId}
                    onModuleChange={setModuleId}
                    mode={conversation?.mode ?? "companion"}
                  />
                </div>
              </div>
            </>
          )}
        </main>
      </div>
    </AppShell>
  );
}
