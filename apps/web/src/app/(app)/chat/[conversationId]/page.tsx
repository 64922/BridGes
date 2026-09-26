"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";

import { StateBlock } from "@/components/bridges/StateBlock";
import { ChatSendErrorBanner } from "@/components/bridges/chat/ChatSendErrorBanner";
import { ChatThread } from "@/components/bridges/chat/ChatThread";
import { Composer } from "@/components/bridges/Composer";
import { StudyProgress } from "@/components/bridges/chat/StudyProgress";
import {
  type ChatMessage as ChatMessageLike,
  type ChatThinking,
} from "@/components/bridges/MessageList";
import { AppShell } from "@/components/layout/AppShell";
import { MAIN_CONTENT_ID } from "@/components/layout/MainContent";
import { CHAT_LIST_CHANGED_EVENT } from "@/lib/recent-conversations";
import {
  createChatRun,
  getChatConversation,
  isChatStreamEventOf,
  retryChatRun,
  stopChatMessage,
  subscribeChatRunEvents,
  startFirstTurn,
  type ChatConversationProjection,
  type ChatAttachmentProjection,
  type ChatMessageProjection,
  type ChatModuleId,
  type ChatStreamEvent,
  type ArxivSearchProjection,
  type TeachingTurnProjection,
  type WebSearchProjection,
} from "@/lib/api";
import { readAloudSession } from "@/lib/read-aloud";
import {
  hasPendingCommuteClarification,
  pendingClarificationModule,
  type ChatModuleSelectionId,
} from "@/lib/chat-modules";
import type { CapabilityAvailability } from "@/components/bridges/chat/ReadAloudControls";
import { buildThreadMessages } from "@/lib/chat-thread";
import { isIngestionSettled, isPhotoAttachment } from "@/lib/chat-attachments";
import type {
  ChatStreamCareerData,
  ChatStreamHumanizerData,
  ChatStreamImageData,
  ChatStreamNodeData,
  ChatStreamStageData,
  ChatStreamVideoData,
} from "@/lib/api";

import styles from "@/components/bridges/chat/chat.module.css";

/**
 * GQ-03/GQ-04：媒体能力（听写/朗读/图片/视频）由全局运行凭据驱动，
 * 不再按账户探测禁用入口——新账户无需任何个人 Qwen 配置即可使用。
 */
const MEDIA_ALWAYS_AVAILABLE: CapabilityAvailability = { available: true };

interface ActiveRun {
  messageId: string;
  content: string;
  /** send=新发送；retry=重试；resume=页面重开恢复进行中的运行 */
  kind: "send" | "retry" | "resume";
  /** 流式中的可公开思考摘要（生成完成折叠后由服务端历史提供） */
  thinking: ChatThinking | null;
  /** 流式中的公网搜索状态与真实来源 */
  webSearch: WebSearchProjection | null;
  /** 流式中的 arXiv 论文搜索状态与真实论文来源 */
  arxivSearch: ArxivSearchProjection | null;
  /** 流式中的学习模式教学卡片与证据门 */
  teaching: TeachingTurnProjection | null;
  /** Issue 06：流式中的统一阶段状态（检索/生成/检查/收尾，脱敏）。 */
  stage: ChatStreamStageData | null;
  /** V2 Issue 02：流式中的父图节点进度（started 时显示节点标签）。 */
  node: ChatStreamNodeData | null;
  /** Issue 28：流式中的文章人味化过程卡状态（五态中文）。 */
  humanizerProcess: ChatStreamHumanizerData | null;
  /** Issue 29：流式中的生涯规划过程卡状态（五态中文）。 */
  careerProcess: ChatStreamCareerData | null;
  /** Issue 31：流式中的图片任务状态快照（提交即下发，任务卡即时呈现）。 */
  imageProcess: ChatStreamImageData | null;
  /** Issue 32：流式中的视频任务状态快照（提交即下发，任务卡即时呈现）。 */
  videoProcess: ChatStreamVideoData | null;
  /** 终态标识：error 事件后保留渲染直至权威历史加载完成 */
  status: "streaming" | "error";
  errorText?: string;
}
/** 真实生命周期耗时（毫秒）→ 折叠标题秒数（与服务端投影换算一致）。 */
function secondsOf(durationMs: number | null | undefined): number | null {
  if (durationMs === null || durationMs === undefined) return null;
  return Math.max(1, Math.round(durationMs / 1000));
}
/**
 * Issue 02：由助手消息投影构造进行中的 ActiveRun（创建响应/页面恢复共用）。
 * 发送/重试时创建响应即携带初始思考摘要与搜索投影，不再等待 started 事件；
 * 页面恢复时消息投影已含部分内容，由调用方填充 content。
 */
function activeRunFromAssistant(
  assistant: ChatMessageProjection,
  kind: ActiveRun["kind"],
  thinkingSeconds: number | null = null
): ActiveRun {
  return {
    messageId: assistant.message_id,
    content: "",
    kind,
    status: "streaming",
    thinking: assistant.thinking
      ? {
          steps: assistant.thinking.steps ?? [],
          evidence: assistant.thinking.evidence ?? [],
          tools: assistant.thinking.tools ?? [],
          quality: assistant.thinking.quality ?? [],
          seconds: thinkingSeconds,
        }
      : null,
    webSearch: assistant.web_search ?? null,
    arxivSearch: assistant.arxiv_search ?? null,
    teaching: assistant.teaching ?? null,
    // Issue 06：创建即入队——首个真实阶段事件到达前显示"排队中"
    stage: { kind: "stage", message_id: assistant.message_id, stage: "queued", status: "active" },
    node: null,
    humanizerProcess: null,
    careerProcess: null,
    imageProcess: null,
    videoProcess: null,
  };
}
/**
 * 对话页：历史消息 + 流式回答 + 停止/重试 + 能力预检错误提示。
 *
 * 状态覆盖：loading（加载中）/ error（加载失败可重试）/ 恢复后正常（重启
 * 后重新打开同一对话）/ 生成中（streaming + 停止入口）/ 失败（保留正文 +
 * 重试）。键盘可达：跳转链接 → 侧栏最近对话 → 消息 → 输入区，Enter 发送、
 * Shift+Enter 换行、Esc 停止；流式更新不移动焦点、不逐 token 朗读。
 */
export default function ChatConversationPage() {
  const params = useParams<{ conversationId: string }>();
  const conversationId = params.conversationId;

  const [conversation, setConversation] = useState<ChatConversationProjection | null>(null);
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">("loading");
  const [loadError, setLoadError] = useState("");
  const [activeRun, setActiveRun] = useState<ActiveRun | null>(null);
  // Issue 05：pendingUser 携带本轮照片附件（发送成功后立刻渲染缩略图）。
  const [pendingUser, setPendingUser] = useState<{
    id: string;
    text: string;
    attachments?: ChatAttachmentProjection[];
  } | null>(null);
  const [sendError, setSendError] = useState<{ message: string } | null>(null);
  const [announcement, setAnnouncement] = useState<string | null>(null);
  // V2 Issue 11：输入区的显式模块选择（随每条消息保存，不回溯改写历史）。
  const [moduleId, setModuleId] = useState<ChatModuleSelectionId | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const sendingRef = useRef(false);
  // V2 Issue 11：等待状态的输入区恢复只在本对话首次加载时判定一次，
  // 之后用户移除模块标签的选择必须被尊重（刷新历史不得自动选回）。
  const resumeModuleCheckedRef = useRef(false);
  const firstTurnIdempotencyKeyRef = useRef<string | null>(null);
  const firstTurnRetryRef = useRef(false);
  // V2 Issue 02：发送/重试幂等键（同文本/同消息的失败重发复用，成功后清除）。
  // Issue 05：幂等身份含照片集合——同文字换照片必须换新键。
  // V2 Issue 11：重试幂等身份含模块覆盖——点建议启动模块与普通重试是两次
  // 不同的派发，绝不能复用同键把带模块的运行重放成普通重试。
  const sendIdempotencyRef = useRef<{
    text: string;
    attachments: string[];
    key: string;
  } | null>(null);
  const retryIdempotencyRef = useRef<{
    messageId: string;
    moduleId: string | null;
    key: string;
  } | null>(null);
  const load = useCallback(async (keepContent = false) => {
    // keepContent：本地刷新（如错误收敛后）时保留当前消息渲染，
    // 不闪 loading，避免遮蔽 error 态的思考摘要。
    if (!keepContent) setLoadState("loading");
    setLoadError("");
    try {
      const projection = await getChatConversation(conversationId);
      setConversation(projection);
      setLoadState("ready");
      // V2 Issue 11/12/13/14：重开对话时若最后一条模块消息仍在等澄清，恢复输入
      // 区的模块选择（只在本对话首次加载时判定一次），下一条回复从该处继续。
      // 论文、贴吧与资料共用同一套等待合同，由 pendingClarificationModule 一并
      // 判定；通勤的等待状态形态不同，用自己那一个判定。
      if (!resumeModuleCheckedRef.current) {
        resumeModuleCheckedRef.current = true;
        const pendingModule = pendingClarificationModule(projection.messages ?? []);
        if (pendingModule) {
          setModuleId(pendingModule);
        } else if (hasPendingCommuteClarification(projection.messages ?? [])) {
          setModuleId("commute");
        }
      }
    } catch (error) {
      setLoadState("error");
      setLoadError(error instanceof Error ? error.message : "对话加载失败。");
    } finally {
      // 权威历史接管后收敛 error 态渲染（见 handleStreamEvent error 分支）
      activeRunRef.current = null;
      setActiveRun(null);
    }
  }, [conversationId]);

  useEffect(() => {
    firstTurnIdempotencyKeyRef.current = null;
    firstTurnRetryRef.current = false;
    resumeModuleCheckedRef.current = false;
    setConversation(null);
    setActiveRun(null);
    setPendingUser(null);
    setSendError(null);
    setModuleId(null);
    void load();
  }, [load]);

  // 切换对话/离开页面/切换账户时安全停止朗读播放会话。
  useEffect(() => () => readAloudSession.stop(), [conversationId]);

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
    const stop = window.setTimeout(() => window.clearInterval(poll), 120_000);
    return () => {
      window.clearInterval(poll);
      window.clearTimeout(stop);
    };
  }, [pendingFileParse, load]);

  // Issue 02：组件卸载（切换账户导致 AppShell 重挂载/离开页面）只中断
  // 本地订阅（移除订阅者），绝不调用停止接口——生成由后台执行器持有，
  // 离开页面不会中断回复，返回时从游标恢复（Issue 39 AC3 的收敛职责
  // 移交给持久化运行，页面卸载不再拥有运行生命周期）。
  useEffect(
    () => () => {
      abortRef.current?.abort();
      activeRunRef.current = null;
    },
    [conversationId]
  );

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
    // 兜底：消息始终未出现时停止轮询（如已被删除）。
    const stop = window.setTimeout(() => window.clearInterval(poll), 5000);
    return () => {
      window.clearInterval(poll);
      window.clearTimeout(stop);
      if (clearHighlight !== undefined) window.clearTimeout(clearHighlight);
    };
  }, [loadState]);

  const handleStreamEvent = useCallback(
    (kind: ActiveRun["kind"], text: string) =>
      (event: ChatStreamEvent) => {
        if (isChatStreamEventOf(event, "started")) {
          // Issue 02：ActiveRun 已由创建响应（send/retry）或消息投影
          // （resume）建立，started 事件只在订阅游标回溯时兜底重建——
          // 已有进行中状态时绝不覆盖（避免内容/思考摘要被清空）。
          if (activeRunRef.current) return;
          // 同步写入 ref：SSE 事件可能在同一块内连续到达（started 后紧跟
          // delta），异步 setState 尚未刷新时 delta 处理器依赖 ref 判断归属
          const run: ActiveRun = {
            messageId: event.data.message_id,
            content: "",
            kind,
            status: "streaming",
            // 初始思考摘要：思考区域自动展开（Issue 14）
            thinking: event.data.thinking
              ? {
                  steps: event.data.thinking.steps ?? [],
                  evidence: event.data.thinking.evidence ?? [],
                  tools: event.data.thinking.tools ?? [],
                  quality: event.data.thinking.quality ?? [],
                  seconds: null,
                }
              : null,
            webSearch: event.data.web_search ?? null,
            arxivSearch: event.data.arxiv_search ?? null,
            teaching: event.data.teaching ?? null,
            // Issue 06：回放起点即排队中，首个真实阶段事件到达后覆盖
            stage: {
              kind: "stage",
              message_id: event.data.message_id,
              stage: "queued",
              status: "active",
            },
            node: null,
            humanizerProcess: null,
            careerProcess: null,
            imageProcess: null,
            videoProcess: null,
          };
          activeRunRef.current = run;
          if (kind === "send") {
            setPendingUser((current) => ({
              id: event.data.user_message_id,
              text,
              attachments: current?.attachments ?? [],
            }));
          }
          setActiveRun(run);
          setAnnouncement("正在生成回答");
        } else if (isChatStreamEventOf(event, "node")) {
          // V2 Issue 02：父图节点进度（只映射真实开始/完成的节点）。
          // started 显示节点中文标签；completed 清空节点与阶段——显示权
          // 交给下一节点 started 或 invoke 内更细的 stage 事件。
          if (activeRunRef.current?.messageId === event.data.message_id) {
            const node = event.data.status === "started" ? event.data : null;
            activeRunRef.current = {
              ...activeRunRef.current,
              node,
              ...(node === null ? { stage: null } : {}),
            };
            setActiveRun((run) =>
              run
                ? {
                    ...run,
                    node,
                    ...(node === null ? { stage: null } : {}),
                  }
                : run
            );
          }
        } else if (isChatStreamEventOf(event, "stage")) {
          // Issue 06：统一阶段事件（脱敏：仅阶段枚举/状态/耗时）；阶段行
          // 在流式期间即时呈现，终态由 done 后权威历史的消息投影接管。
          // 阶段事件比节点更细：到达后接管进度显示（清空节点行）。
          if (activeRunRef.current?.messageId === event.data.message_id) {
            activeRunRef.current = {
              ...activeRunRef.current,
              stage: event.data,
              node: null,
            };
            setActiveRun((run) =>
              run ? { ...run, stage: event.data, node: null } : run
            );
          }
        } else if (
          isChatStreamEventOf(event, "delta") &&
          activeRunRef.current?.messageId === event.data.message_id
        ) {
          activeRunRef.current = {
            ...activeRunRef.current,
            content: activeRunRef.current.content + event.data.delta,
          };
          setActiveRun((run) => (run ? { ...run, content: run.content + event.data.delta } : run));
        } else if (isChatStreamEventOf(event, "humanizer")) {
          // Issue 28：文章人味化过程卡五态事件（loading/empty/error/permission/recovery）
          if (activeRunRef.current?.messageId === event.data.message_id) {
            activeRunRef.current = {
              ...activeRunRef.current,
              humanizerProcess: event.data,
            };
            setActiveRun((run) => (run ? { ...run, humanizerProcess: event.data } : run));
          }
        } else if (isChatStreamEventOf(event, "career")) {
          // Issue 29：生涯规划过程卡五态事件（loading/empty/error/permission/recovery）
          if (activeRunRef.current?.messageId === event.data.message_id) {
            activeRunRef.current = {
              ...activeRunRef.current,
              careerProcess: event.data,
            };
            setActiveRun((run) => (run ? { ...run, careerProcess: event.data } : run));
          }
        } else if (isChatStreamEventOf(event, "image")) {
          // Issue 31：图片任务状态事件（提交即下发 queued 快照）；任务卡
          // 在流式期间即时呈现，终态由 done 后权威历史的消息投影接管。
          if (activeRunRef.current?.messageId === event.data.message_id) {
            activeRunRef.current = {
              ...activeRunRef.current,
              imageProcess: event.data,
            };
            setActiveRun((run) => (run ? { ...run, imageProcess: event.data } : run));
          }
        } else if (isChatStreamEventOf(event, "video")) {
          // Issue 32：视频任务状态事件（提交即下发 queued 快照）；任务卡
          // 在流式期间即时呈现，终态由 done 后权威历史的消息投影接管。
          if (activeRunRef.current?.messageId === event.data.message_id) {
            activeRunRef.current = {
              ...activeRunRef.current,
              videoProcess: event.data,
            };
            setActiveRun((run) => (run ? { ...run, videoProcess: event.data } : run));
          }
        } else if (isChatStreamEventOf(event, "done") || isChatStreamEventOf(event, "error")) {
          if (isChatStreamEventOf(event, "error")) {
            // 失败/停止/断流：保留已完成正文与思考摘要的 error 态渲染
            // （消费事件载荷，折叠标题「已思考（用时 X 秒）」不依赖重新
            // 加载的间隙）；权威历史加载完成后由 load 收敛清空。
            const current = activeRunRef.current;
            const errorRun: ActiveRun = {
              messageId: event.data.message_id,
              content: current?.content ?? "",
              kind,
              status: "error",
              errorText: event.data.error.message,
              thinking: event.data.thinking
                ? {
                    steps: event.data.thinking.steps ?? [],
                    evidence: event.data.thinking.evidence ?? [],
                    tools: event.data.thinking.tools ?? [],
                    quality: event.data.thinking.quality ?? [],
                    seconds: secondsOf(event.data.duration_ms),
                  }
                : null,
              webSearch: event.data.web_search ?? current?.webSearch ?? null,
              arxivSearch: event.data.arxiv_search ?? current?.arxivSearch ?? null,
              teaching: event.data.teaching ?? current?.teaching ?? null,
              stage: current?.stage ?? null,
              node: current?.node ?? null,
              humanizerProcess: current?.humanizerProcess ?? null,
              careerProcess: current?.careerProcess ?? null,
              imageProcess: current?.imageProcess ?? null,
              videoProcess: current?.videoProcess ?? null,
            };
            activeRunRef.current = errorRun;
            setActiveRun(errorRun);
            setAnnouncement(`生成失败：${event.data.error.message}`);
          } else {
            // 完成：清除进行中状态，重新加载服务端权威历史
            activeRunRef.current = null;
            setActiveRun(null);
            setPendingUser(null);
            setAnnouncement("回答已生成");
          }
          // 错误走 keepContent 刷新（保留 error 态渲染直至权威历史接管）
          void load(event.event === "error");
        }
      },
    [load]
  );
  const activeRunRef = useRef<ActiveRun | null>(null);
  activeRunRef.current = activeRun;

  /** Issue 02：订阅运行事件并自动重连（断线只移除订阅者，不改变运行）。
   *  重连前刷新权威状态：消息已终态则交给 load 收敛；否则从服务端
   *  最后游标续读（页面重开语义，绝不重复发送或调用模型）。 */
  const subscribeWithRetry = useCallback(
    async (
      targetConversationId: string,
      messageId: string,
      startCursor: number,
      onEvent: (event: ChatStreamEvent) => void,
      signal: AbortSignal
    ): Promise<void> => {
      let cursor = startCursor;
      let attempt = 0;
      for (;;) {
        try {
          await subscribeChatRunEvents(
            targetConversationId,
            messageId,
            cursor,
            onEvent,
            signal
          );
          return; // 正常结束（运行已终态，全部事件已回放）
        } catch (error) {
          if (signal.aborted) return; // 停止/卸载主动中断
          attempt += 1;
          if (attempt > 30) throw error;
          await new Promise((resolve) =>
            setTimeout(resolve, Math.min(300 * attempt, 3000))
          );
          try {
            const projection = await getChatConversation(targetConversationId);
            const message = projection.messages?.find(
              (item) => item.message_id === messageId
            );
            if (!message || message.status !== "streaming") {
              // 运行已终态（或消息已删除）：权威历史收敛
              await load();
              return;
            }
            cursor = message.active_run?.cursor ?? cursor;
          } catch {
            // 会话读取失败：保持原游标继续重试订阅
          }
        }
      }
    },
    [load]
  );

  const sendMessage = useCallback(
    async (
      text: string,
      attachmentIds: string[] = [],
      module: ChatModuleSelectionId | null = null
    ): Promise<boolean> => {
      setSendError(null);
      setAnnouncement("正在生成回答");
      const controller = new AbortController();
      abortRef.current = controller;
      // V2 Issue 02：发送幂等键——同文本的失败重发复用同一键（服务端复用
      // 同一运行，不重复写消息）；文本变化则换新键（绝不重放旧请求）。
      const keyEntry = sendIdempotencyRef.current;
      const samePayload =
        keyEntry && keyEntry.text === text && keyEntry.attachments.join(",") === attachmentIds.join(",");
      const sendIdempotencyKey =
        samePayload ? keyEntry.key : crypto.randomUUID();
      sendIdempotencyRef.current = { text, attachments: attachmentIds, key: sendIdempotencyKey };
      const isFirstTurn =
        firstTurnRetryRef.current ||
        (conversation !== null && (conversation.messages?.length ?? 0) === 0);
      try {
        // Issue 02：创建运行（消息已落库、运行已入队），立即订阅持久化事件
        let userMessage: ChatMessageProjection;
        let assistantMessage: ChatMessageProjection;
        let cursor: number;
        if (isFirstTurn) {
          const idempotencyKey =
            firstTurnIdempotencyKeyRef.current ??
            (firstTurnIdempotencyKeyRef.current = crypto.randomUUID());
          const firstTurn = await startFirstTurn({
            content: text,
            idempotencyKey,
            conversationId,
            mode: conversation?.mode ?? "companion",
            attachmentIds,
            moduleId: module ?? undefined,
          });
          setConversation(firstTurn.conversation);
          userMessage = firstTurn.user_message;
          assistantMessage = firstTurn.assistant_message;
          cursor = firstTurn.cursor;
          firstTurnRetryRef.current = true;
        } else {
          const run = await createChatRun(
            conversationId,
            text,
            sendIdempotencyKey,
            attachmentIds,
            module ?? undefined
          );
          userMessage = run.user_message;
          assistantMessage = run.assistant_message;
          cursor = run.cursor;
        }
        // 创建成功：幂等键使命完成——后续发送（同文本也一样）必须换新键
        sendIdempotencyRef.current = null;
        const runState = activeRunFromAssistant(assistantMessage, "send");
        activeRunRef.current = runState;
        setPendingUser({
          id: userMessage.message_id,
          text,
          attachments: userMessage.attachments ?? [],
        });
        setActiveRun(runState);
        await subscribeWithRetry(
          conversationId,
          assistantMessage.message_id,
          cursor,
          handleStreamEvent("send", text),
          controller.signal
        );
        firstTurnRetryRef.current = false;
        return true;
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return true; // 停止/卸载：运行由后台执行器持有，服务端状态为准
        }
        const message = error instanceof Error ? error.message : "发送失败，请稍后重试。";
        setSendError({ message });
        setAnnouncement(`生成失败：${message}`);
        // 断流/内部错误时服务端已收敛消息状态：刷新展示可重试错误
        // 保留 Composer 的本地正文；失败时不切换 loadState，避免卸载输入区。
        activeRunRef.current = null;
        setActiveRun(null);
        setPendingUser(null);
        return false;
      } finally {
        abortRef.current = null;
        sendingRef.current = false;
        window.dispatchEvent(new Event(CHAT_LIST_CHANGED_EVENT));
      }
    },
    [
      conversation,
      conversationId,
      handleStreamEvent,
      subscribeWithRetry,
    ]
  );

  const skipTeachingQuestion = useCallback(() => {
    void sendMessage("跳过这道理解检查，我想继续学习。");
  }, [sendMessage]);

  // Issue 08：目标确认阶段一键按初学者开始（发送固定确认指令）。
  const beginnerStartTeaching = useCallback(() => {
    void sendMessage("按初学者开始");
  }, [sendMessage]);

  const stop = useCallback(async () => {
    const run = activeRunRef.current;
    if (!run) return;
    // 先收敛服务端状态，再中断客户端流：保证终态为"已停止"而非断流错误
    try {
      await stopChatMessage(conversationId, run.messageId);
    } catch {
      // 停止失败不阻塞中断；重新加载后以服务端状态为准
    }
    abortRef.current?.abort();
    // 中断后清除进行中状态：否则输入区会一直停留在「停止」无法恢复发送
    activeRunRef.current = null;
    setActiveRun(null);
    setPendingUser(null);
    setAnnouncement("已停止生成");
    await load();
  }, [conversationId, load]);

  const retry = useCallback(
    async (messageId: string, moduleOverride?: ChatModuleId) => {
      setSendError(null);
      setAnnouncement("正在重试生成");
      const controller = new AbortController();
      abortRef.current = controller;
      // V2 Issue 02：重试幂等键——同一消息的同一次派发失败后再点重试复用
      // 同一键（服务端复用同一运行）；换消息或换模块自动换新键。
      const override = moduleOverride ?? null;
      const keyEntry = retryIdempotencyRef.current;
      const retryIdempotencyKey =
        keyEntry && keyEntry.messageId === messageId && keyEntry.moduleId === override
          ? keyEntry.key
          : crypto.randomUUID();
      retryIdempotencyRef.current = { messageId, moduleId: override, key: retryIdempotencyKey };
      try {
        // Issue 02：重试创建新尝试与 queued 运行，随后订阅持久化事件
        // V2 Issue 11：带模块时以该轮用户消息原文显式派发到该模块。
        const run = await retryChatRun(
          conversationId,
          messageId,
          retryIdempotencyKey,
          moduleOverride
        );
        retryIdempotencyRef.current = null;
        const runState = activeRunFromAssistant(run.assistant_message, "retry");
        activeRunRef.current = runState;
        setActiveRun(runState);
        await subscribeWithRetry(
          conversationId,
          run.assistant_message.message_id,
          run.cursor,
          handleStreamEvent("retry", ""),
          controller.signal
        );
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        const message = error instanceof Error ? error.message : "重试失败，请稍后再试。";
        setSendError({ message });
        setAnnouncement(`重试失败：${message}`);
        void load(true);
      } finally {
        abortRef.current = null;
        window.dispatchEvent(new Event(CHAT_LIST_CHANGED_EVENT));
      }
    },
    [conversationId, handleStreamEvent, load, subscribeWithRetry]
  );

  // Issue 02：页面重开/刷新后恢复进行中的运行——消息投影已含部分内容
  // 与活跃运行视图（active_run），从最后游标订阅剩余事件；绝不重复
  // 发送用户消息或重复调用模型（运行由后台执行器持有）。
  useEffect(() => {
    if (loadState !== "ready" || conversation === null) return;
    if (activeRunRef.current) return; // 已有进行中状态（发送/重试/恢复中）
    for (const message of conversation.messages ?? []) {
      if (message.status !== "streaming" || !message.active_run) continue;
      const runState = activeRunFromAssistant(message, "resume");
      runState.content = message.content ?? "";
      activeRunRef.current = runState;
      setActiveRun(runState);
      setAnnouncement("正在生成回答");
      const controller = new AbortController();
      abortRef.current = controller;
      void subscribeWithRetry(
        conversationId,
        message.message_id,
        message.active_run.cursor,
        handleStreamEvent("resume", ""),
        controller.signal
      );
      return;
    }
  }, [
    loadState,
    conversation,
    conversationId,
    handleStreamEvent,
    subscribeWithRetry,
  ]);

  // 组装渲染消息：服务端历史 + 进行中的乐观消息 + 错误收敛后的终态渲染。
  // Issue 02：resume 恢复时权威历史已含该 streaming 消息（部分内容），
  // 由 ActiveRun 接管渲染，先从历史中移除同 messageId 项避免双份。
  const baseMessages = conversation
    ? buildThreadMessages(conversation.messages ?? [])
    : [];
  const resumedMessageId = activeRun?.kind === "resume" ? activeRun.messageId : null;
  const activeSendMessageId = activeRun?.kind === "send" ? activeRun.messageId : null;
  const activeSendUserMessageId = activeRun?.kind === "send" ? pendingUser?.id : null;
  const threadMessages = baseMessages.filter(
    (item) =>
      item.id !== resumedMessageId &&
      item.id !== activeSendMessageId &&
      item.id !== activeSendUserMessageId
  );
  if (activeRun) {
    const isError = activeRun.status === "error";
    const assistantItem: ChatMessageLike = {
      id: activeRun.messageId,
      role: "assistant",
      plainText: activeRun.content,
      thinking: activeRun.thinking ?? undefined,
      webSearch: activeRun.webSearch,
      arxivSearch: activeRun.arxivSearch,
      teaching: activeRun.teaching,
      stage: activeRun.stage,
      node: activeRun.node,
      humanizerProcess: activeRun.humanizerProcess,
      careerProcess: activeRun.careerProcess,
      image: activeRun.imageProcess?.task ?? undefined,
      video: activeRun.videoProcess?.task ?? undefined,
      content: (
        <p style={{ whiteSpace: "pre-wrap", overflowWrap: "break-word" }}>
          {activeRun.content}
          {!isError && <span className={styles.streamCursor} aria-hidden="true" />}
        </p>
      ),
      status: isError ? "error" : "streaming",
      errorText: isError ? activeRun.errorText : undefined,
    };
    if (isError) {
      // 失败/停止/断流：保留已完成正文与思考摘要，直至权威历史接管
      threadMessages.push(assistantItem);
    } else if (pendingUser && activeRun.kind === "send") {
      threadMessages.push({
        id: pendingUser.id,
        role: "user",
        plainText: pendingUser.text,
        attachments: pendingUser.attachments ?? [],
        content: (
          <p style={{ whiteSpace: "pre-wrap", overflowWrap: "break-word" }}>{pendingUser.text}</p>
        ),
      });
      threadMessages.push(assistantItem);
    } else if (activeRun.kind === "retry" || activeRun.kind === "resume") {
      // 重试流：在最新尝试（可能失败）之后追加新的流式尝试；
      // resume：恢复的进行中回答（历史项已移除，由本项接管渲染）
      threadMessages.push(assistantItem);
    }
}
  const generating = activeRun !== null;

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
            <StateBlock kind="loading" title="正在加载对话" description="读取消息历史与生成状态。" />
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
                <StudyProgress study={conversation.study} />
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
                tts={MEDIA_ALWAYS_AVAILABLE}
                onRefreshMessages={() => void load(true)}
                announcement={announcement}
              />
              {sendError && (
                <ChatSendErrorBanner message={sendError.message} />
              )}
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
