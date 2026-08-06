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
  fetchKeySettings,
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
  type CapabilityProbeSummary,
  type ChatConversationProjection,
  type ChatAttachmentProjection,
  type ChatStreamEvent,
  type ArxivSearchProjection,
  type ProfileNotification,
  type TeachingTurnProjection,
  type WebSearchProjection,
} from "@/lib/api";
import { readAloudSession } from "@/lib/read-aloud";
import type { CapabilityAvailability } from "@/components/bridges/chat/ReadAloudControls";
import {
  chatAttachmentKey,
  chatImageKey,
  chatNoProfileKey,
  chatPromptKey,
  chatSkillKey,
  chatVideoKey,
} from "@/lib/chat-flow";
import { HumanizerDialog } from "@/components/bridges/HumanizerDialog";
import { CareerPlanningDialog } from "@/components/bridges/CareerPlanningDialog";
import { ImageDialog } from "@/components/bridges/ImageDialog";
import { VideoDialog } from "@/components/bridges/VideoDialog";
import { PluginPickerDialog } from "@/components/bridges/PluginPickerDialog";
import { McpInvokeDialog } from "@/components/bridges/McpInvokeDialog";
import { McpCallCard } from "@/components/bridges/McpCallCard";
import type { HumanizerSkillInput } from "@/lib/api";
import { buildThreadMessages } from "@/lib/chat-thread";
import type {
  ChatStreamCareerData,
  ChatStreamHumanizerData,
  ChatStreamImageData,
  ChatStreamMcpData,
  ChatStreamVideoData,
} from "@/lib/api";
import type {
  ChatPluginSelectionItem,
  ImageAssetProjection,
  ImageRequestPayload,
  ImageTaskKind,
  McpCallRequestPayload,
  VideoRequestPayload,
} from "@/lib/api";
import {
  approveMessageMcpConfirmation,
  denyMessageMcpConfirmation,
  getImageAsset,
  listMcpServers,
  listPlugins,
  updateChatConversation,
} from "@/lib/api";

import styles from "@/components/bridges/chat/chat.module.css";

/** Issue 30：把账户级探测快照折叠为语音入口可用性（不可用时带中文原因）。 */
function speechAvailability(
  capabilities: CapabilityProbeSummary[] | null | undefined,
  capabilityId: string,
  displayName: string
): CapabilityAvailability {
  const item = capabilities?.find((c) => c.capability_id === capabilityId);
  if (!item) {
    return {
      available: false,
      reason: `${displayName}能力尚未探测，请前往「设置」中的密钥页重新探测。`,
    };
  }
  if (item.status === "available") return { available: true };
  if (item.status === "probing") {
    return { available: false, reason: `${displayName}能力正在探测中，请稍候再试。` };
  }
  return {
    available: false,
    reason: `${displayName}能力当前不可用：${item.message ?? "请前往「设置」重新探测。"}`,
  };
}

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
  // Issue 29：生涯规划任务对话框（问题 + 画像开关）
  const [humanizerOpen, setHumanizerOpen] = useState(false);
  const [careerOpen, setCareerOpen] = useState(false);
  // Issue 31：图片生成/编辑任务对话框（生成页签 + 编辑页签）
  const [imageOpen, setImageOpen] = useState(false);
  const [imageAssets, setImageAssets] = useState<ImageAssetProjection[]>([]);
  // Issue 32：视频生成任务对话框（单一生成页签，Wan 固定绑定）
  const [videoOpen, setVideoOpen] = useState(false);
  // Issue 36：对话级插件选择（随对话持久化；chip 持续显示；停用/卸载/
  // 撤权后由服务端清洗并随投影解释影响）
  const [pluginSelection, setPluginSelection] = useState<ChatPluginSelectionItem[]>([]);
  const [pluginNames, setPluginNames] = useState<Record<string, string>>({});
  const [removedSelections, setRemovedSelections] = useState<
    { kind: "skill" | "mcp"; plugin_id: string; name: string; reason: string }[]
  >([]);
  const [pluginPickerOpen, setPluginPickerOpen] = useState(false);
  // Issue 36：MCP 调用对话框目标（选中插件 chip「调用」按钮打开）
  const [invokeMcpTarget, setInvokeMcpTarget] = useState<{
    mcp_id: string;
    name: string;
  } | null>(null);
  const [profileNotifications, setProfileNotifications] = useState<
    ProfileNotification[]
  >([]);
  const abortRef = useRef<AbortController | null>(null);
  const sendingRef = useRef(false);
  // Issue 30：账户级语音能力探测快照（asr 听写 / tts 朗读独立门控；
  // 不可用时禁用入口并说明原因，服务端仍做权威校验）
  const [speechCapabilities, setSpeechCapabilities] = useState<
    CapabilityProbeSummary[] | null
  >(null);

  const load = useCallback(async (keepContent = false) => {
    // keepContent：本地刷新（如错误收敛后）时保留当前消息渲染，
    // 不闪 loading，避免遮蔽 error 态的思考摘要。
    if (!keepContent) setLoadState("loading");
    setLoadError("");
    try {
      const projection = await getChatConversation(conversationId);
      setConversation(projection);
      setPluginSelection(projection.plugin_selection ?? []);
      setRemovedSelections(projection.removed_selections ?? []);
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
    setConversation(null);
    setActiveRun(null);
    setPendingUser(null);
    setSendError(null);
    setProjectName(null);
    void load();
  }, [load]);

  // Issue 30：拉取一次账户级语音能力探测快照（asr/tts 独立门控）；
  // 切换对话/离开页面/切换账户时安全停止朗读播放会话。
  useEffect(() => {
    setSpeechCapabilities(null);
    void fetchKeySettings()
      .then((projection) => setSpeechCapabilities(projection.capabilities ?? []))
      .catch(() => setSpeechCapabilities([]));
  }, [conversationId]);

  useEffect(() => () => readAloudSession.stop(), [conversationId]);

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

  // Issue 36：解析选中插件的显示名（刷新/恢复历史对话后 chip 名称不丢）。
  // 只拉取列表接口的轻量投影；失败静默降级为显示 plugin_id。
  useEffect(() => {
    if (pluginSelection.length === 0) {
      setPluginNames({});
      return;
    }
    let cancelled = false;
    Promise.all([listPlugins(), listMcpServers()])
      .then(([plugins, mcp]) => {
        if (cancelled) return;
        const names: Record<string, string> = {};
        for (const item of plugins.builtin ?? []) {
          if (pluginSelection.some((s) => s.kind === "skill" && s.plugin_id === item.skill_id)) {
            names[`skill:${item.skill_id}`] = item.name;
          }
        }
        for (const item of plugins.user ?? []) {
          if (pluginSelection.some((s) => s.kind === "skill" && s.plugin_id === item.plugin_id)) {
            names[`skill:${item.plugin_id}`] = item.name;
          }
        }
        for (const server of mcp.servers ?? []) {
          if (pluginSelection.some((s) => s.kind === "mcp" && s.plugin_id === server.mcp_id)) {
            names[`mcp:${server.mcp_id}`] = server.name;
          }
        }
        setPluginNames(names);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [pluginSelection]);

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

  // Issue 36：替换本对话插件选择（选择器确认后全量 PATCH；显式空数组
  // 清空）。只更新本地 plugin_selection 字段，避免用 PATCH 响应覆盖消息列表。
  const changePlugins = useCallback(
    async (
      selection: ChatPluginSelectionItem[],
      names?: Record<string, string>
    ) => {
      if (!conversation || loadState !== "ready") return;
      try {
        const projection = await updateChatConversation(
          conversation.conversation_id,
          { pluginSelection: selection }
        );
        setPluginSelection(projection.plugin_selection ?? selection);
        setRemovedSelections(projection.removed_selections ?? []);
        if (names) setPluginNames((current) => ({ ...current, ...names }));
        setAnnouncement(
          selection.length > 0
            ? `已选择 ${selection.length} 个插件`
            : "已清除插件选择"
        );
      } catch (error) {
        setSendError({
          message: error instanceof Error ? error.message : "更新插件选择失败，请稍后重试。",
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
      // Issue 29：首页生涯规划对话框关闭画像时暂存标记（消费即删除）。
      const rawNoProfile = sessionStorage.getItem(chatNoProfileKey(conversationId));
      sessionStorage.removeItem(chatNoProfileKey(conversationId));
      // Issue 31：首页提交的图片任务载荷（消费即删除，防 StrictMode 双发）。
      const rawImage = sessionStorage.getItem(chatImageKey(conversationId));
      sessionStorage.removeItem(chatImageKey(conversationId));
      let imagePayload: ImageRequestPayload | undefined;
      if (rawImage) {
        try {
          const parsed: unknown = JSON.parse(rawImage);
          if (
            parsed &&
            typeof parsed === "object" &&
            "kind" in parsed &&
            typeof (parsed as { kind: unknown }).kind === "string" &&
            "prompt" in parsed &&
            typeof (parsed as { prompt: unknown }).prompt === "string"
          ) {
            imagePayload = parsed as ImageRequestPayload;
          }
        } catch {
          setSendError({ message: "图片任务信息损坏，请重新提交。" });
        }
      }
      // Issue 32：首页提交的视频任务载荷（消费即删除，防 StrictMode 双发）。
      const rawVideo = sessionStorage.getItem(chatVideoKey(conversationId));
      sessionStorage.removeItem(chatVideoKey(conversationId));
      let videoPayload: VideoRequestPayload | undefined;
      if (rawVideo) {
        try {
          const parsed: unknown = JSON.parse(rawVideo);
          if (
            parsed &&
            typeof parsed === "object" &&
            "prompt" in parsed &&
            typeof (parsed as { prompt: unknown }).prompt === "string"
          ) {
            videoPayload = parsed as VideoRequestPayload;
          }
        } catch {
          setSendError({ message: "视频任务信息损坏，请重新提交。" });
        }
      }
      sendingRef.current = true;
      void sendMessage(
        prompt,
        attachmentIds,
        true,
        rawNoProfile ? false : true,
        skillId,
        skillInput,
        imagePayload,
        videoPayload
      );
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

  const sendMessage = useCallback(
    async (
      text: string,
      attachmentIds: string[] = [],
      useKnowledgeBase: boolean = true,
      useProfile: boolean = true,
      skillId?: string,
      skillInput?: unknown,
      image?: ImageRequestPayload,
      video?: VideoRequestPayload,
      mcpCall?: McpCallRequestPayload
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
          skillInput,
          // Issue 31：图片生成/编辑载荷（图片对话框走真实消息流程）
          image,
          // Issue 32：文生视频载荷（视频对话框走真实消息流程）
          video,
          // Issue 36：对选中 MCP 插件的调用载荷（调用对话框走真实消息流程）
          mcpCall
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

  /** Issue 29：提交生涯规划任务（真实消息流；画像开关随本轮发送透传）。 */
  const handleCareerSubmit = useCallback(
    async (content: string, useProfile: boolean): Promise<boolean> => {
      return sendMessage(content, [], true, useProfile);
    },
    [sendMessage]
  );

  /** Issue 31：提交图片生成/编辑任务（真实消息流：image 载荷创建异步任务，
   *  状态卡与资产卡在消息流中呈现，不在此处伪造图片结果）。 */
  const handleImageSubmit = useCallback(
    async (payload: {
      kind: ImageTaskKind;
      prompt: string;
      sourceVersionId?: string;
      sourceObjectId?: string;
    }): Promise<boolean> => {
      const imagePayload: ImageRequestPayload = {
        kind: payload.kind,
        prompt: payload.prompt,
        ...(payload.sourceVersionId
          ? { source_version_id: payload.sourceVersionId }
          : {}),
        ...(payload.sourceObjectId
          ? { source_object_id: payload.sourceObjectId }
          : {}),
      };
      return sendMessage(payload.prompt, [], true, true, undefined, undefined, imagePayload);
    },
    [sendMessage]
  );

  /** Issue 36：提交对选中 MCP 插件的调用（真实消息流：mcp_call 载荷
   *  走服务端选中校验与 invoke，结果卡在消息流中呈现，不伪造结果）。 */
  const handleMcpInvokeSubmit = useCallback(
    async (payload: McpCallRequestPayload): Promise<boolean> => {
      const target = invokeMcpTarget;
      const ok = await sendMessage(
        `调用 ${payload.mcp_id} 的 ${payload.tool} 工具`,
        [],
        true,
        true,
        undefined,
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
    async (messageId: string, confirmationId: string, action: "approve" | "deny") => {
      if (action === "approve") {
        await approveMessageMcpConfirmation(
          conversationId,
          messageId,
          confirmationId
        );
      } else {
        await denyMessageMcpConfirmation(
          conversationId,
          messageId,
          confirmationId
        );
      }
      void load(true);
    },
    [conversationId, load]
  );

  /** Issue 32：提交文生视频任务（真实消息流：video 载荷创建异步任务，
   *  状态卡与资产卡在消息流中呈现，不在此处伪造视频结果）。 */
  const handleVideoSubmit = useCallback(
    async (payload: { prompt: string }): Promise<boolean> => {
      const videoPayload: VideoRequestPayload = { prompt: payload.prompt };
      return sendMessage(payload.prompt, [], true, true, undefined, undefined, undefined, videoPayload);
    },
    [sendMessage]
  );

  // Issue 31：图片编辑对话框的可选来源——当前对话中已成功且未删除的
  // 图片资产（打开对话框时按消息流收集一次；资产详情含版本链）。
  useEffect(() => {
    if (!imageOpen || !conversation) return;
    let cancelled = false;
    const assetIds = new Set<string>();
    for (const message of conversation.messages ?? []) {
      const image = message.image;
      if (
        image &&
        image.status === "succeeded" &&
        image.asset_id &&
        !image.deleted
      ) {
        assetIds.add(image.asset_id);
      }
    }
    Promise.all(
      Array.from(assetIds).map((assetId) =>
        getImageAsset(conversationId, assetId).catch(() => null)
      )
    ).then((results) => {
      if (!cancelled) {
        setImageAssets(
          results.filter((result): result is ImageAssetProjection => result !== null)
        );
      }
    });
    return () => {
      cancelled = true;
    };
  }, [imageOpen, conversation, conversationId]);

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
                onDownloadAttachment={(attachment) => void downloadAttachment(attachment)}
                onDeleteAttachment={(messageId, attachment) =>
                  void deleteAttachment(messageId, attachment)
                }
                onRetryIngestion={retryIngestion}
                conversationId={conversationId}
                tts={speechAvailability(speechCapabilities, "tts", "语音朗读")}
                onRefreshMessages={() => void load(true)}
                announcement={announcement}
                onConfirmMcpCall={confirmMessageMcp}
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
                    onOpenCareer={() => setCareerOpen(true)}
                    onOpenImage={() => setImageOpen(true)}
                    image={speechAvailability(speechCapabilities, "image", "图片生成与编辑")}
                    onOpenVideo={() => setVideoOpen(true)}
                    video={speechAvailability(speechCapabilities, "video", "视频生成")}
                    asr={speechAvailability(speechCapabilities, "asr", "语音转写")}
                    pluginSelection={pluginSelection}
                    pluginNames={pluginNames}
                    onSelectPlugins={() => setPluginPickerOpen(true)}
                    onRemovePlugin={(kind, pluginId) => {
                      void changePlugins(
                        pluginSelection.filter(
                          (item) => !(item.kind === kind && item.plugin_id === pluginId)
                        )
                      );
                    }}
                    onInvokeMcp={(mcpId) => {
                      const target = pluginSelection.find(
                        (item) => item.kind === "mcp" && item.plugin_id === mcpId
                      );
                      if (!target) return;
                      setInvokeMcpTarget({
                        mcp_id: mcpId,
                        name: pluginNames[`mcp:${mcpId}`] ?? mcpId,
                      });
                    }}
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
      <CareerPlanningDialog
        open={careerOpen}
        onClose={() => setCareerOpen(false)}
        conversationId={conversationId}
        onSubmit={handleCareerSubmit}
      />
      <ImageDialog
        open={imageOpen}
        onClose={() => setImageOpen(false)}
        assets={imageAssets}
        attachmentOptions={imageAttachmentOptions(threadMessages)}
        onSubmit={handleImageSubmit}
      />
      <VideoDialog
        open={videoOpen}
        onClose={() => setVideoOpen(false)}
        onSubmit={handleVideoSubmit}
      />
      <PluginPickerDialog
        open={pluginPickerOpen}
        onClose={() => setPluginPickerOpen(false)}
        selected={pluginSelection}
        removedSelections={removedSelections}
        onSelect={changePlugins}
      />
      <McpInvokeDialog
        open={invokeMcpTarget !== null}
        server={invokeMcpTarget}
        onClose={() => setInvokeMcpTarget(null)}
        selected={pluginSelection}
        onSubmit={handleMcpInvokeSubmit}
      />
    </AppShell>
  );
}

/** Issue 31：从消息流收集图片附件作为编辑来源（本账户聊天附件对象）。 */
function imageAttachmentOptions(
  messages: (ChatMessageLike | { kind: string; event_id: string; to_mode: string })[]
): {
  key: string;
  objectId: string;
  label: string;
  hint: string;
}[] {
  const options: { key: string; objectId: string; label: string; hint: string }[] = [];
  for (const message of messages) {
    if ("kind" in message || message.role !== "user") continue;
    for (const attachment of message.attachments ?? []) {
      if (!attachment.media_type.startsWith("image/")) continue;
      options.push({
        key: `object:${attachment.object_id}`,
        objectId: attachment.object_id,
        label: attachment.original_filename,
        hint: attachment.media_type,
      });
    }
  }
  return options;
}
