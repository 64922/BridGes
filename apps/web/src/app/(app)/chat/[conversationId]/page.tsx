"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";

import { StateBlock } from "@/components/bridges/StateBlock";
import { ChatProfileNotificationCards } from "@/components/bridges/chat/ChatProfileNotificationCards";
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
import { changeConversationLearningProject } from "@/lib/learning-projects";
import {
  ApiError,
  deleteChatMessageAttachment,
  downloadChatAttachment,
  getChatConversation,
  getLearningProject,
  isChatStreamEventOf,
  markProfileNotificationRead,
  recallProfileNotification as recallProfileNotificationApi,
  retryAttachmentIngestion,
  retryChatMessage,
  stopChatMessage,
  streamChatMessage,
  switchChatMode,
  type ChatConversationProjection,
  type ChatAttachmentProjection,
  type ChatStreamEvent,
  type ArxivSearchProjection,
  type ProfileNotification,
  type TeachingTurnProjection,
  type WebSearchProjection,
} from "@/lib/api";
import { chatAttachmentKey, chatPromptKey, chatSkillKey } from "@/lib/chat-flow";
import { HumanizerDialog } from "@/components/bridges/HumanizerDialog";
import type { HumanizerSkillInput } from "@/lib/api";
import { buildThreadMessages } from "@/lib/chat-thread";
import type { ChatStreamHumanizerData } from "@/lib/api";

import styles from "@/components/bridges/chat/chat.module.css";

interface ActiveRun {
  messageId: string;
  content: string;
  kind: "send" | "retry";
  /** 流式中的可公开思考摘要（生成完成折叠后由服务端历史提供） */
  thinking: ChatThinking | null;
  /** 流式中的公网搜索状态与真实来源 */
  webSearch: WebSearchProjection | null;
  /** 流式中的 arXiv 论文搜索状态与真实论文来源 */
  arxivSearch: ArxivSearchProjection | null;
  /** 流式中的学习模式教学卡片与证据门 */
  teaching: TeachingTurnProjection | null;
  /** Issue 28：流式中的文章人味化过程卡状态（五态中文）。 */
  humanizerProcess: ChatStreamHumanizerData | null;
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
  const [pendingUser, setPendingUser] = useState<{ id: string; text: string } | null>(null);
  const [sendError, setSendError] = useState<{ message: string; code?: string } | null>(null);
  const [announcement, setAnnouncement] = useState<string | null>(null);
  // 对话所属学习项目名称（Issue 19）：由 project_id 解析，仅供 chip 展示
  const [projectName, setProjectName] = useState<string | null>(null);
  // Issue 26：本轮用户消息触发的画像通知（明确记忆/自动写入/候选/单次情绪）
  // Issue 28：文章人味化任务对话框（改写/生成两条路径）
  const [humanizerOpen, setHumanizerOpen] = useState(false);
  const [profileNotifications, setProfileNotifications] = useState<
    ProfileNotification[]
  >([]);
  const abortRef = useRef<AbortController | null>(null);
  const sendingRef = useRef(false);

  const load = useCallback(async (keepContent = false) => {
    // keepContent：本地刷新（如错误收敛后）时保留当前消息渲染，
    // 不闪 loading，避免遮蔽 error 态的思考摘要。
    if (!keepContent) setLoadState("loading");
    setLoadError("");
    try {
      const projection = await getChatConversation(conversationId);
      setConversation(projection);
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
    setConversation(null);
    setActiveRun(null);
    setPendingUser(null);
    setSendError(null);
    setProjectName(null);
    void load();
  }, [load]);

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

  // 解析对话所属学习项目名称；项目已在别处删除（404）时清除 chip 并提示。
  const projectId = conversation?.project_id ?? null;
  useEffect(() => {
    if (!projectId) {
      setProjectName(null);
      return;
    }
    let cancelled = false;
    getLearningProject(projectId)
      .then((detail) => {
        if (!cancelled) setProjectName(detail.name);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        if (error instanceof ApiError && error.status === 404) {
          setProjectName(null);
          setConversation((current) =>
            current ? { ...current, project_id: null } : current
          );
          setSendError({ message: "该学习项目已被删除，已清除对话的项目归属显示。" });
        } else {
          setProjectName(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  // 变更学习项目归属：立即写入服务端（显式 null 表示移出），
  // 仅更新本地 project_id 字段，避免用 PATCH 响应覆盖消息列表。
  const changeLearningProject = useCallback(
    async (project: { project_id: string; name: string } | null) => {
      if (!conversation || loadState !== "ready") return;
      try {
        await changeConversationLearningProject(
          conversation.conversation_id,
          project?.project_id ?? null
        );
        setConversation((current) =>
          current ? { ...current, project_id: project?.project_id ?? null } : current
        );
        setProjectName(project?.name ?? null);
        setAnnouncement(project ? `已移入学习项目：${project.name}` : "已清除学习项目选择");
      } catch (error) {
        setSendError({
          message: error instanceof Error ? error.message : "更新学习项目归属失败，请稍后重试。",
          code: error instanceof ApiError ? error.code : undefined,
        });
      }
    },
    [conversation, loadState]
  );

  // 新对话首页跳转带来的待发送消息：同步消费防 StrictMode 双发
  useEffect(() => {
    if (loadState !== "ready" || conversation === null || (conversation.messages ?? []).length > 0) {
      return;
    }
    if (sendingRef.current) return;
    const prompt = sessionStorage.getItem(chatPromptKey(conversationId));
    if (prompt) {
      sessionStorage.removeItem(chatPromptKey(conversationId));
      const rawAttachmentIds = sessionStorage.getItem(chatAttachmentKey(conversationId));
      sessionStorage.removeItem(chatAttachmentKey(conversationId));
      let attachmentIds: string[] = [];
      if (rawAttachmentIds) {
        try {
          const parsed: unknown = JSON.parse(rawAttachmentIds);
          if (Array.isArray(parsed) && parsed.every((item) => typeof item === "string")) {
            attachmentIds = parsed;
          }
        } catch {
          setSendError({ message: "附件发送信息损坏，请重新上传后重试。" });
        }
      }
      // Issue 28：首页提交的人味化任务载荷（消费即删除，防 StrictMode 双发）
      const rawSkill = sessionStorage.getItem(chatSkillKey(conversationId));
      sessionStorage.removeItem(chatSkillKey(conversationId));
      let skillId: string | undefined;
      let skillInput: unknown;
      if (rawSkill) {
        try {
          const parsed: unknown = JSON.parse(rawSkill);
          if (parsed && typeof parsed === "object" && "skill_id" in parsed) {
            skillId = (parsed as { skill_id: string }).skill_id;
            skillInput = parsed;
          }
        } catch {
          setSendError({ message: "人味化任务信息损坏，请重新提交。" });
        }
      }
      sendingRef.current = true;
      void sendMessage(prompt, attachmentIds, true, true, skillId, skillInput);
    }
  }, [loadState, conversation, conversationId]);

  const handleStreamEvent = useCallback(
    (kind: ActiveRun["kind"], text: string) =>
      (event: ChatStreamEvent) => {
        if (isChatStreamEventOf(event, "started")) {
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
            humanizerProcess: null,
          };
          activeRunRef.current = run;
          if (kind === "send") {
            setPendingUser({ id: event.data.user_message_id, text });
          }
          setActiveRun(run);
          setAnnouncement("正在生成回答");
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
        } else if (isChatStreamEventOf(event, "profile")) {
          // 画像通知即时展示：已持久化并按账户隔离；重试轮次会重新下发
          // 同一份通知，本地按 notification_id 去重。
          setProfileNotifications((current) => {
            const incoming = event.data.notifications ?? [];
            const known = new Set(current.map((notification) => notification.notification_id));
            return [...current, ...incoming.filter((notification) => !known.has(notification.notification_id))];
          });
          setAnnouncement("已收到画像记忆通知");
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
              humanizerProcess: current?.humanizerProcess ?? null,
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

  const sendMessage = useCallback(
    async (
      text: string,
      attachmentIds: string[] = [],
      useKnowledgeBase: boolean = true,
      useProfile: boolean = true,
      skillId?: string,
      skillInput?: unknown
    ): Promise<boolean> => {
      setSendError(null);
      setProfileNotifications([]);
      setAnnouncement("正在生成回答");
      const controller = new AbortController();
      abortRef.current = controller;
      let started = false;
      try {
        const onEvent = handleStreamEvent("send", text);
        await streamChatMessage(
          conversationId,
          text,
          (event) => {
            if (event.event === "started") started = true;
            onEvent(event);
          },
          controller.signal,
          attachmentIds,
          // Issue 20：本轮知识库开关（关闭后检索与引用不含知识库候选）
          useKnowledgeBase,
          // Issue 27：本轮画像使用开关（关闭后请求与披露均不含画像内容）
          useProfile,
          // Issue 28：内置 SKILL 载荷（bridges-humanizer 走真实消息流程）
          skillId,
          skillInput
        );
        return true;
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return started; // 停止：状态由停止接口收敛
        }
        const message = error instanceof Error ? error.message : "发送失败，请稍后重试。";
        setSendError({ message, code: error instanceof ApiError ? error.code : undefined });
        setAnnouncement(`生成失败：${message}`);
        // 断流/内部错误时服务端已收敛消息状态：刷新展示可重试错误
        void load();
        return started;
      } finally {
        abortRef.current = null;
        sendingRef.current = false;
        window.dispatchEvent(new Event(CHAT_LIST_CHANGED_EVENT));
      }
    },
    [conversationId, handleStreamEvent, load]
  );

  /** Issue 28：提交人味化任务（真实消息流：任务契约随消息落库，可重试）。
   *  消息正文由对话框统一组装（单一来源），这里只转发发送。 */
  const handleHumanizerSubmit = useCallback(
    async (
      content: string,
      skillInput: HumanizerSkillInput,
      attachmentIds: string[]
    ): Promise<boolean> => {
      return sendMessage(
        content,
        attachmentIds,
        true,
        true,
        skillInput.skill_id,
        skillInput
      );
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
        setSendError({ message, code: error instanceof ApiError ? error.code : undefined });
        setAnnouncement(`附件下载失败：${message}`);
      }
    },
    [conversationId]
  );

  const skipTeachingQuestion = useCallback(() => {
    void sendMessage("跳过这道理解检查，我想继续学习。", [], true);
  }, [sendMessage]);

  const deleteAttachment = useCallback(
    async (messageId: string, attachment: ChatAttachmentProjection) => {
      try {
        await deleteChatMessageAttachment(conversationId, messageId, attachment.object_id);
        setAnnouncement(`已删除附件：${attachment.original_filename}`);
        await load(true);
      } catch (error) {
        const message = error instanceof Error ? error.message : "附件删除失败，请重试。";
        setSendError({ message, code: error instanceof ApiError ? error.code : undefined });
        setAnnouncement(`附件删除失败：${message}`);
      }
    },
    [conversationId, load]
  );

  /** 失败文档重新解析（Issue 17）：调用重试 API 并以服务端投影刷新对话。 */
  const retryIngestion = useCallback(
    async (objectId: string) => {
      try {
        await retryAttachmentIngestion(conversationId, objectId);
        setAnnouncement("已重新加入解析队列");
        await load(true);
      } catch (error) {
        const message = error instanceof Error ? error.message : "重新解析失败，请重试。";
        setSendError({ message, code: error instanceof ApiError ? error.code : undefined });
        setAnnouncement(`重新解析失败：${message}`);
        throw error;
      }
    },
    [conversationId, load]
  );

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
        await retryChatMessage(conversationId, messageId, handleStreamEvent("retry", ""), controller.signal);
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        const message = error instanceof Error ? error.message : "重试失败，请稍后再试。";
        setSendError({ message, code: error instanceof ApiError ? error.code : undefined });
        setAnnouncement(`重试失败：${message}`);
        void load();
      } finally {
        abortRef.current = null;
        window.dispatchEvent(new Event(CHAT_LIST_CHANGED_EVENT));
      }
    },
    [conversationId, handleStreamEvent, load]
  );

  // 画像通知：一键撤回（自动写入记录）与关闭（标记已读）。失败可安全重试，
  // 本地状态只在成功后收敛，不显示假成功。
  const dismissProfileNotification = useCallback((notificationId: string) => {
    setProfileNotifications((current) =>
      current.filter((notification) => notification.notification_id !== notificationId)
    );
    markProfileNotificationRead(notificationId).catch(() => {
      // 标记已读失败不影响聊天主流程；画像中心仍可查看该通知。
    });
  }, []);

  const recallProfileNotification = useCallback(
    async (notification: ProfileNotification): Promise<void> => {
      const recalled = await recallProfileNotificationApi(notification.notification_id);
      setProfileNotifications((current) =>
        current.map((item) =>
          item.notification_id === recalled.notification_id ? recalled : item
        )
      );
      setAnnouncement("已撤回自动写入的画像记录");
    },
    []
  );

  // 模式切换：写入服务端并更新对话投影（只影响后续消息，历史不被重写）。
  // 可见事件在消息流中渲染并自带 role=status 播报，页面级不再重复播报。
  const changeMode = useCallback(
    async (mode: ChatMode) => {
      if (!conversation || conversation.mode === mode || loadState !== "ready") return;
      try {
        const result = await switchChatMode(conversation.conversation_id, mode);
        setConversation(result.conversation);
      } catch (error) {
        setSendError({
          message: error instanceof Error ? error.message : "切换模式失败，请稍后重试。",
          code: error instanceof ApiError ? error.code : undefined,
        });
      }
    },
    [conversation, loadState]
  );

  // 组装渲染消息：服务端历史 + 进行中的乐观消息 + 错误收敛后的终态渲染
  const baseMessages = conversation
    ? buildThreadMessages(conversation.messages ?? [], conversation.mode_events ?? [])
    : [];
  const threadMessages = [...baseMessages];
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
      humanizerProcess: activeRun.humanizerProcess,
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
    } else if (activeRun.kind === "retry") {
      // 重试流：在最新尝试（可能失败）之后追加新的流式尝试
      threadMessages.push(assistantItem);
    }
  }

  const generating = activeRun !== null;
  const currentMode: ChatMode = conversation?.mode ?? "companion";

  return (
    <AppShell mode="account">
      <div className={styles.chatShell}>
        <main
          id="main-content"
          tabIndex={-1}
          data-testid="main-content"
          className={styles.chatMain}
        >
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
                onDownloadAttachment={(attachment) => void downloadAttachment(attachment)}
                onDeleteAttachment={(messageId, attachment) =>
                  void deleteAttachment(messageId, attachment)
                }
                onRetryIngestion={retryIngestion}
                conversationId={conversationId}
                announcement={announcement}
              />
              {profileNotifications.length > 0 && (
                <ChatProfileNotificationCards
                  notifications={profileNotifications}
                  onRecall={recallProfileNotification}
                  onDismiss={dismissProfileNotification}
                />
              )}
              {sendError && (
                <ChatSendErrorBanner message={sendError.message} code={sendError.code} />
              )}
              <div className={styles.composerWrap}>
                <div className={styles.composerInner}>
                  <div className={styles.modeRow}>
                    <ModeToggle value={currentMode} onChange={(mode) => void changeMode(mode)} />
                  </div>
                  <Composer
                    onSend={(text, attachmentIds, _, useKnowledgeBase, useProfile) =>
                      sendMessage(text, attachmentIds, useKnowledgeBase, useProfile)
                    }
                    conversationId={conversationId}
                    generating={generating}
                    onStop={() => void stop()}
                    learningProject={
                      conversation?.project_id
                        ? { project_id: conversation.project_id, name: projectName ?? "学习项目" }
                        : null
                    }
                    onSelectLearningProject={(project) => void changeLearningProject(project)}
                    onOpenHumanizer={() => setHumanizerOpen(true)}
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
        conversationId={conversationId}
        onSubmit={handleHumanizerSubmit}
      />
    </AppShell>
  );
}
