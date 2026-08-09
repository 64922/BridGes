"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";

import { StateBlock } from "@/components/bridges/StateBlock";
import { ChatSendErrorBanner } from "@/components/bridges/chat/ChatSendErrorBanner";
import { ChatThread } from "@/components/bridges/chat/ChatThread";
import { Composer } from "@/components/bridges/Composer";
import { ModeToggle, type ChatMode } from "@/components/bridges/ModeToggle";
import {
  type ChatMessage as ChatMessageLike,
  type ChatThinking,
} from "@/components/bridges/MessageList";
import { AppShell } from "@/components/layout/AppShell";
import { CHAT_LIST_CHANGED_EVENT } from "@/lib/recent-conversations";
import {
  downloadChatAttachment,
  createChatRun,
  getChatConversation,
  isChatStreamEventOf,
  retryAttachmentIngestion,
  retryChatRun,
  stopChatMessage,
  subscribeChatRunEvents,
  startFirstTurn,
  type ChatConversationProjection,
  type ChatMessageProjection,
  type ChatAttachmentProjection,
  type ChatStreamEvent,
  type ArxivSearchProjection,
  type TeachingTurnProjection,
  type WebSearchProjection,
} from "@/lib/api";
import { readAloudSession } from "@/lib/read-aloud";
import type { CapabilityAvailability } from "@/components/bridges/chat/ReadAloudControls";
import { HumanizerDialog } from "@/components/bridges/HumanizerDialog";
import { CareerPlanningDialog } from "@/components/bridges/CareerPlanningDialog";
import { VideoDialog } from "@/components/bridges/VideoDialog";
import type { HumanizerSkillInput } from "@/lib/api";
import { buildThreadMessages } from "@/lib/chat-thread";
import type {
  ChatStreamCareerData,
  ChatStreamHumanizerData,
  ChatStreamImageData,
  ChatStreamMcpData,
  ChatStreamStageData,
  ChatStreamVideoData,
} from "@/lib/api";
import type { VideoRequestPayload } from "@/lib/api";

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
  /** Issue 28：流式中的文章人味化过程卡状态（五态中文）。 */
  humanizerProcess: ChatStreamHumanizerData | null;
  /** Issue 29：流式中的生涯规划过程卡状态（五态中文）。 */
  careerProcess: ChatStreamCareerData | null;
  /** Issue 31：流式中的图片任务状态快照（提交即下发，任务卡即时呈现）。 */
  imageProcess: ChatStreamImageData | null;
  /** Issue 32：流式中的视频任务状态快照（提交即下发，任务卡即时呈现）。 */
  videoProcess: ChatStreamVideoData | null;
  /** Issue 36：流式中的 MCP 调用结果投影（同步执行终态，结果卡即时呈现）。 */
  mcpCallProcess: ChatStreamMcpData | null;
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
    humanizerProcess: null,
    careerProcess: null,
    imageProcess: null,
    videoProcess: null,
    mcpCallProcess: null,
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
  const [selectedMode, setSelectedMode] = useState<ChatMode>("companion");
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">("loading");
  const [loadError, setLoadError] = useState("");
  const [activeRun, setActiveRun] = useState<ActiveRun | null>(null);
  const [pendingUser, setPendingUser] = useState<{ id: string; text: string } | null>(null);
  const [sendError, setSendError] = useState<{ message: string } | null>(null);
  const [announcement, setAnnouncement] = useState<string | null>(null);
  // Issue 26：本轮用户消息触发的画像通知（明确记忆/自动写入/候选/单次情绪）
  // Issue 28：文章人味化任务对话框（改写/生成两条路径）
  // Issue 29：生涯规划任务对话框（问题 + 画像开关）
  const [humanizerOpen, setHumanizerOpen] = useState(false);
  const [careerOpen, setCareerOpen] = useState(false);
  // Issue 32：视频生成任务对话框（单一生成页签，Wan 固定绑定）
  const [videoOpen, setVideoOpen] = useState(false);
  // Issue 36：对话级插件选择（随对话持久化；chip 持续显示；停用/卸载/
  // 撤权后由服务端清洗并随投影解释影响）
  // Issue 36：MCP 调用对话框目标（选中插件 chip「调用」按钮打开）
  const [invokeMcpTarget, setInvokeMcpTarget] = useState<{
    mcp_id: string;
    name: string;
  } | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const sendingRef = useRef(false);
  const firstTurnIdempotencyKeyRef = useRef<string | null>(null);
  const firstTurnRetryRef = useRef(false);
  const modeLocked = conversation?.mode_locked ?? (conversation?.messages?.length ?? 0) > 0;
  const currentMode: ChatMode = modeLocked ? conversation?.mode ?? selectedMode : selectedMode;
  const load = useCallback(async (keepContent = false) => {
    // keepContent：本地刷新（如错误收敛后）时保留当前消息渲染，
    // 不闪 loading，避免遮蔽 error 态的思考摘要。
    if (!keepContent) setLoadState("loading");
    setLoadError("");
    try {
      const projection = await getChatConversation(conversationId);
      setConversation(projection);
      if ((projection.removed_selections ?? []).length > 0) {
        setAnnouncement(
          `已移除失效插件：${(projection.removed_selections ?? [])
            .map((entry) => `「${entry.name}」${entry.reason}`)
            .join("；")}`
        );
      }
      setLoadState("ready");
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
    setConversation(null);
    setActiveRun(null);
    setPendingUser(null);
    setSendError(null);
    void load();
  }, [load]);

  // 切换对话/离开页面/切换账户时安全停止朗读播放会话。
  useEffect(() => () => readAloudSession.stop(), [conversationId]);

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

  // Issue 36：解析选中插件的显示名（刷新/恢复历史对话后 chip 名称不丢）。
  // 只拉取列表接口的轻量投影；失败静默降级为显示 plugin_id。
  /*

  // Issue 36：替换本对话插件选择（选择器确认后全量 PATCH；显式空数组
  // 清空）。只更新本地 plugin_selection 字段，避免用 PATCH 响应覆盖消息列表。
          selection.length > 0
            ? `已选择 ${selection.length} 个插件`
            : "已清除插件选择"
        );
      } catch (error) {
        setSendError({
          message: error instanceof Error ? error.message : "更新插件选择失败，请稍后重试。",
        });
      }
    },
  */
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
            humanizerProcess: null,
            careerProcess: null,
            imageProcess: null,
            videoProcess: null,
            mcpCallProcess: null,
          };
          activeRunRef.current = run;
          if (kind === "send") {
            setPendingUser({ id: event.data.user_message_id, text });
          }
          setActiveRun(run);
          setAnnouncement("正在生成回答");
        } else if (isChatStreamEventOf(event, "stage")) {
          // Issue 06：统一阶段事件（脱敏：仅阶段枚举/状态/耗时）；阶段行
          // 在流式期间即时呈现，终态由 done 后权威历史的消息投影接管。
          if (activeRunRef.current?.messageId === event.data.message_id) {
            activeRunRef.current = {
              ...activeRunRef.current,
              stage: event.data,
            };
            setActiveRun((run) => (run ? { ...run, stage: event.data } : run));
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
        } else if (isChatStreamEventOf(event, "mcp_call")) {
          // Issue 36：MCP 调用结果事件（同步执行终态投影）；结果卡在
          // 流式期间即时呈现，终态由 done 后权威历史的消息投影接管。
          if (activeRunRef.current?.messageId === event.data.message_id) {
            activeRunRef.current = {
              ...activeRunRef.current,
              mcpCallProcess: event.data,
            };
            setActiveRun((run) => (run ? { ...run, mcpCallProcess: event.data } : run));
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
              humanizerProcess: current?.humanizerProcess ?? null,
              careerProcess: current?.careerProcess ?? null,
              imageProcess: current?.imageProcess ?? null,
              videoProcess: current?.videoProcess ?? null,
              mcpCallProcess: current?.mcpCallProcess ?? null,
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
      useKnowledgeBase: boolean = true,
      useProfile: boolean = true,
      skillId?: string,
      skillInput?: unknown,
      video?: VideoRequestPayload,
      mcpCall?: McpCallRequestPayload
    ): Promise<boolean> => {
      setSendError(null);
      setAnnouncement("正在生成回答");
      const controller = new AbortController();
      abortRef.current = controller;
      const isFirstTurn =
        firstTurnRetryRef.current ||
        (conversation !== null &&
          !modeLocked &&
          (conversation.messages?.length ?? 0) === 0);
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
            idempotency_key: idempotencyKey,
            conversation_id: conversationId,
            mode: selectedMode,
            use_knowledge_base: useKnowledgeBase,
            use_profile: useProfile,
            ...(skillId !== undefined ? { skill_id: skillId } : {}),
            ...(skillInput !== undefined
              ? { skill_input: skillInput as HumanizerSkillInput }
              : {}),
            ...(video !== undefined ? { video } : {}),
            ...(mcpCall !== undefined ? { mcp_call: mcpCall } : {}),
          });
          setConversation(firstTurn.conversation);
          setSelectedMode(firstTurn.conversation.mode);
          userMessage = firstTurn.user_message;
          assistantMessage = firstTurn.assistant_message;
          cursor = firstTurn.cursor;
          firstTurnRetryRef.current = true;
        } else {
          const run = await createChatRun(
            conversationId,
            text,
            useKnowledgeBase,
            useProfile,
            skillId,
            skillInput,
            undefined,
            video,
            mcpCall
          );
          userMessage = run.user_message;
          assistantMessage = run.assistant_message;
          cursor = run.cursor;
          setConversation((current) =>
            current ? { ...current, mode_locked: true } : current
          );
        }
        const runState = activeRunFromAssistant(assistantMessage, "send");
        activeRunRef.current = runState;
        setPendingUser({ id: userMessage.message_id, text });
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
        void load();
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
      load,
      modeLocked,
      selectedMode,
      subscribeWithRetry,
    ]
  );

  /** Issue 28：提交人味化任务（真实消息流：任务契约随消息落库，可重试）。
   *  消息正文由对话框统一组装（单一来源），这里只转发发送。 */
  const handleHumanizerSubmit = useCallback(
    async (
      content: string,
      skillInput: HumanizerSkillInput,
      useKnowledgeBase: boolean
    ): Promise<boolean> => {
      return sendMessage(
        content,
        useKnowledgeBase,
        true,
        skillInput.skill_id,
        skillInput
      );
    },
    [sendMessage]
  );

  /** Issue 29：提交生涯规划任务（真实消息流；画像开关随本轮发送透传）。 */
  const handleCareerSubmit = useCallback(
    async (content: string, useProfile: boolean): Promise<boolean> => {
      return sendMessage(content, true, useProfile);
    },
    [sendMessage]
  );

  /** Issue 36：提交对选中 MCP 插件的调用（真实消息流：mcp_call 载荷
   *  走服务端选中校验与 invoke，结果卡在消息流中呈现，不伪造结果）。 */
  /*
  const handleMcpInvokeSubmit = useCallback(
    async (payload: McpCallRequestPayload): Promise<boolean> => {
      const target = invokeMcpTarget;
      const ok = await sendMessage(
        `调用 ${payload.mcp_id} 的 ${payload.tool} 工具`,
        true,
        true,
        undefined,
        undefined,
        undefined,
        payload
      );
      setInvokeMcpTarget(null);
      return ok;
    },
    [sendMessage, invokeMcpTarget]
  );

  /** Issue 36：消息内 MCP 敏感操作确认（approve/deny 走 chat 域路由，
   *  结果写回消息投影；确认后刷新权威历史呈现最终结果）。 */
  const confirmMessageMcp = useCallback(
    async () => {
      setSendError({ message: "MCP 调用已退役，历史调用仅供查看。" });
    },
    []
  );

  /** Issue 32：提交文生视频任务（真实消息流：video 载荷创建异步任务，
   *  状态卡与资产卡在消息流中呈现，不在此处伪造视频结果）。 */
  void confirmMessageMcp;
  const handleVideoSubmit = useCallback(
    async (payload: { prompt: string }): Promise<boolean> => {
      const videoPayload: VideoRequestPayload = { prompt: payload.prompt };
      return sendMessage(payload.prompt, true, true, undefined, undefined, undefined, videoPayload);
    },
    [sendMessage]
  );

  const downloadAttachment = useCallback(
    async (attachment: ChatAttachmentProjection) => {
      try {
        await downloadChatAttachment(
          conversationId,
          attachment.object_id,
          attachment.original_filename
        );
        setAnnouncement(`已下载附件：${attachment.original_filename}`);
      } catch (error) {
        const message = error instanceof Error ? error.message : "附件下载失败，请重试。";
        setSendError({ message });
        setAnnouncement(`附件下载失败：${message}`);
      }
    },
    [conversationId]
  );

  const skipTeachingQuestion = useCallback(() => {
    void sendMessage("跳过这道理解检查，我想继续学习。", true);
  }, [sendMessage]);

  // Issue 08：目标确认阶段一键按初学者开始（发送固定确认指令）。
  const beginnerStartTeaching = useCallback(() => {
    void sendMessage("按初学者开始", true);
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
    async (messageId: string) => {
      setSendError(null);
      setAnnouncement("正在重试生成");
      const controller = new AbortController();
      abortRef.current = controller;
      try {
        // Issue 02：重试创建新尝试与 queued 运行，随后订阅持久化事件
        const run = await retryChatRun(conversationId, messageId);
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
        void load();
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
    ? buildThreadMessages(conversation.messages ?? [], conversation.mode_events ?? [])
    : [];
  const resumedMessageId = activeRun?.kind === "resume" ? activeRun.messageId : null;
  const activeSendMessageId = activeRun?.kind === "send" ? activeRun.messageId : null;
  const activeSendUserMessageId = activeRun?.kind === "send" ? pendingUser?.id : null;
  const threadMessages = baseMessages.filter(
    (item) =>
      !("id" in item) ||
      (item.id !== resumedMessageId &&
        item.id !== activeSendMessageId &&
        item.id !== activeSendUserMessageId)
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
      humanizerProcess: activeRun.humanizerProcess,
      careerProcess: activeRun.careerProcess,
      image: activeRun.imageProcess?.task ?? undefined,
      video: activeRun.videoProcess?.task ?? undefined,
      mcpCall: activeRun.mcpCallProcess?.call ?? undefined,
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
          id="main-content"
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
              <ChatThread
                messages={threadMessages}
                onRetry={(messageId) => void retry(messageId)}
                onStop={() => void stop()}
                onTeachingSkip={() => skipTeachingQuestion()}
                onTeachingBeginnerStart={() => beginnerStartTeaching()}
                onDownloadAttachment={(attachment) => void downloadAttachment(attachment)}
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
                  <div className={styles.modeRow}>
                    <ModeToggle
                      value={currentMode}
                      locked={modeLocked}
                      onChange={modeLocked ? undefined : setSelectedMode}
                    />
                  </div>
                  <Composer
                    onSend={(text, _, useKnowledgeBase, useProfile) =>
                      sendMessage(text, useKnowledgeBase, useProfile)
                    }
                    conversationId={conversationId}
                    generating={generating}
                    onStop={() => void stop()}
                    onOpenHumanizer={() => setHumanizerOpen(true)}
                    onOpenCareer={() => setCareerOpen(true)}
                    onOpenVideo={() => setVideoOpen(true)}
                    video={MEDIA_ALWAYS_AVAILABLE}
                    asr={MEDIA_ALWAYS_AVAILABLE}
                  />
                </div>
              </div>
            </>
          )}
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
        conversationId={conversationId}
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
