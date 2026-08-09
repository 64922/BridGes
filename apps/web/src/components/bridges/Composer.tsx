"use client";

import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/design-system/Button";
import { Icon } from "@/components/design-system/Icon";
import { LearningProjectPickerDialog } from "@/components/learning-projects/LearningProjectPickerDialog";
import { formatFileType, formatSize } from "@/lib/format";
import {
  CAREER_TOOL_LABEL,
  CHAT_TOOL_INTENTS,
  HUMANIZER_TOOL_LABEL,
  IMAGE_TOOL_LABEL,
  VIDEO_TOOL_LABEL,
} from "@/lib/chat-tools";
import {
  cancelChatAttachment,
  cancelChatAttachmentUpload,
  listUnboundAttachments,
  transcribeDictation,
  uploadChatAttachment,
} from "@/lib/api";
import type { ChatPluginSelectionItem } from "@/lib/api";
import { Menu } from "./Menu";
import type { CapabilityAvailability } from "./chat/ReadAloudControls";
import styles from "./chat/chat.module.css";

interface ComposerAttachment {
  id: string;
  file: File;
  filename: string;
  uploadId: string;
  objectId?: string;
  status: "local" | "uploading" | "uploaded" | "error" | "cancelled";
  progress: number;
  error?: string;
  /** 文件大小（恢复的未发送草稿用服务端投影补全；实时上传用 file.size）。 */
  size?: number;
}

interface ComposerProps {
  onSend: (
    text: string,
    attachmentIds?: string[],
    preparedConversationId?: string,
    useKnowledgeBase?: boolean
  ) => Promise<boolean> | boolean | void;
  /** 已存在的真实对话；提供后选择文件会立即上传到该对话。 */
  conversationId?: string;
  /** 新聊天页提供的延迟创建钩子；只有选择附件时才会预建空对话。 */
  ensureConversation?: () => Promise<string | undefined>;
  generating?: boolean;
  onStop?: () => void;
  /** 外部预填请求（建议卡等）：nonce 变化时把 text 作为结构化意图填入并聚焦 */
  prefill?: { text: string; nonce: number } | null;
  /** 当前选中的学习项目（提供 onSelectLearningProject 时生效）。 */
  learningProject?: { project_id: string; name: string } | null;
  /** 「选择学习项目」入口；选择/清除后回调（传 null 表示清除）。 */
  onSelectLearningProject?: (project: { project_id: string; name: string } | null) => void;
  /** Issue 36：当前对话选中的插件（随对话持久化；chip 持续显示）。 */
  pluginSelection?: ChatPluginSelectionItem[];
  /** Issue 36：选中插件的显示名映射（key = `${kind}:${plugin_id}`）。 */
  pluginNames?: Record<string, string>;
  /** Issue 36：「选择已启用插件」入口（打开真实选择器）。 */
  onSelectPlugins?: () => void;
  /** Issue 36：移除单个插件选择（chip 清除按钮；PATCH 持久化）。 */
  onRemovePlugin?: (kind: "skill" | "mcp", pluginId: string) => void;
  /** Issue 36：对选中 MCP 插件发起调用（chip「调用」按钮）。 */
  onInvokeMcp?: (mcpId: string) => void;
  /** Issue 28：打开「文章人味化」任务对话框（由宿主渲染对话框）。 */
  onOpenHumanizer?: () => void;
  /** Issue 29：打开「生涯规划助手」任务对话框（由宿主渲染对话框）。 */
  onOpenCareer?: () => void;
  /** Issue 31：打开「图片生成与编辑」任务对话框（由宿主渲染对话框）。 */
  onOpenImage?: () => void;
  /** Issue 31：图片生成与编辑能力可用性（账户级探测快照；不可用时禁用入口并说明原因） */
  image?: CapabilityAvailability;
  /** Issue 32：打开「视频生成」任务对话框（由宿主渲染对话框）。 */
  onOpenVideo?: () => void;
  /** Issue 32：视频生成能力可用性（账户级探测快照；不可用时禁用入口并说明原因） */
  video?: CapabilityAvailability;
  /** Issue 30：ASR 听写能力可用性（账户级探测快照；不可用时禁用入口并说明原因） */
  asr?: CapabilityAvailability;
}

const TOOL_PROMPTS = CHAT_TOOL_INTENTS;
const MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024;

function newId(prefix: string): string {
  return `${prefix}-${globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`}`;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "上传失败，请重试。";
}

function attachmentStatus(item: ComposerAttachment): string {
  if (item.status === "local") return "待上传";
  if (item.status === "uploading") return `上传中 ${item.progress}%`;
  if (item.status === "uploaded") return "已上传，等待发送";
  if (item.status === "cancelled") return "已取消，可移除";
  return item.error ?? "上传失败，可重试";
}

/** 对话输入区：统一支持真实字节上传、取消、失败重试与键盘发送。 */
export function Composer({
  onSend,
  conversationId,
  ensureConversation,
  generating = false,
  onStop,
  prefill = null,
  learningProject = null,
  onSelectLearningProject,
  pluginSelection = [],
  pluginNames = {},
  onSelectPlugins,
  onRemovePlugin,
  onInvokeMcp,
  onOpenHumanizer,
  onOpenCareer,
  onOpenImage,
  image = { available: true },
  onOpenVideo,
  video = { available: true },
  asr = { available: true },
}: ComposerProps) {
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState<ComposerAttachment[]>([]);
  // Issue 30：听写状态机（idle → recording → transcribing → idle/error）。
  // 停止录音后才提交完整音频到固定 ASR 快照；转写结果可编辑回填，绝不
  // 自动发送；取消/重录不遗留待发送文本、跨账户临时音频或后台孤儿任务。
  const [dictationPhase, setDictationPhase] = useState<
    "idle" | "recording" | "transcribing" | "error"
  >("idle");
  const [dictationError, setDictationError] = useState("");
  const [dictationSeconds, setDictationSeconds] = useState(0);
  const [dictationTranscribed, setDictationTranscribed] = useState(false);
  const [toolNotice, setToolNotice] = useState("");
  const [projectPickerOpen, setProjectPickerOpen] = useState(false);
  // Issue 20：本轮是否启用全局知识库层（发送前可关闭；关闭后本轮请求、
  // 检索记录与引用均不含知识库候选）。
  const [useKnowledgeBase, setUseKnowledgeBase] = useState(true);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const dictationTimerRef = useRef<number | null>(null);
  const dictationSecondsRef = useRef(0);
  const dictationAbortRef = useRef<AbortController | null>(null);
  const pendingAudioRef = useRef<Blob | null>(null);
  const controllersRef = useRef(new Map<string, AbortController>());
  const preparedConversationRef = useRef<string | undefined>(conversationId);
  const attachmentsRef = useRef<ComposerAttachment[]>([]);
  attachmentsRef.current = attachments;

  const uploadedAttachments = attachments.filter(
    (item) => item.status === "uploaded" && item.objectId
  );
  const hasUploading = attachments.some((item) => item.status === "uploading");
  // Issue 04：上传未完成时发送按钮明确禁用（不能静默发送空附件）——
  // 提示文案由状态栏给出，用户可等待完成或移除附件。
  const canSend =
    (text.trim().length > 0 || uploadedAttachments.length > 0) &&
    dictationPhase === "idle" &&
    !hasUploading;
  // 真实对话上下文（模板设计基线不渲染来源层面板）
  const isRealChat = conversationId !== undefined || ensureConversation !== undefined;

  // Issue 30：录音硬上限（服务端 ASR 同款 300 秒限制，客户端提前自动停止）。
  const MAX_RECORDING_SECONDS = 299;

  const formatDictationTime = (seconds: number) => {
    const minutes = Math.floor(seconds / 60);
    const rest = seconds % 60;
    return `${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
  };

  const autoGrow = () => {
    const element = textareaRef.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, 12 * 16)}px`;
  };

  const setAttachment = (id: string, update: Partial<ComposerAttachment>) => {
    setAttachments((current) =>
      current.map((item) => (item.id === id ? { ...item, ...update } : item))
    );
  };

  const resolveConversation = async (): Promise<string> => {
    if (conversationId) return conversationId;
    if (preparedConversationRef.current) return preparedConversationRef.current;
    const created = await ensureConversation?.();
    if (!created) throw new Error("无法创建附件所属对话，请稍后重试。");
    preparedConversationRef.current = created;
    return created;
  };

  const startUpload = async (
    item: ComposerAttachment,
    targetConversationId: string
  ): Promise<void> => {
    const controller = new AbortController();
    controllersRef.current.set(item.id, controller);
    setAttachment(item.id, { status: "uploading", progress: 0, error: undefined });
    try {
      const projection = await uploadChatAttachment(
        targetConversationId,
        item.file,
        item.uploadId,
        (loaded, total) =>
          setAttachment(item.id, {
            progress: total > 0 ? Math.min(100, Math.round((loaded / total) * 100)) : 0,
          }),
        controller.signal
      );
      setAttachment(item.id, {
        status: "uploaded",
        objectId: projection.object_id,
        progress: 100,
      });
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        setAttachment(item.id, { status: "cancelled", progress: 0 });
      } else {
        setAttachment(item.id, { status: "error", error: errorMessage(error) });
      }
    } finally {
      controllersRef.current.delete(item.id);
    }
  };

  const addSelectedFiles = async (files: FileList | null) => {
    if (!files) return;
    const selected = Array.from(files).map((file) => {
      const tooLarge = file.size > MAX_ATTACHMENT_BYTES;
      const empty = file.size === 0;
      return {
        id: newId("attachment"),
        file,
        filename: file.name,
        uploadId: newId("upload"),
        status: tooLarge || empty ? ("error" as const) : ("local" as const),
        progress: 0,
        error: tooLarge ? "文件超过 10 MB 大小限制，请压缩后重试。" : empty ? "文件为空，无法上传。" : undefined,
      };
    });
    setAttachments((current) => [...current, ...selected]);
    setToolNotice("");

    if (!conversationId && !ensureConversation) return;
    let targetConversationId: string;
    try {
      targetConversationId = await resolveConversation();
    } catch (error) {
      const message = errorMessage(error);
      selected.forEach((item) => setAttachment(item.id, { status: "error", error: message }));
      return;
    }
    await Promise.all(
      selected
        .filter((item) => item.status === "local")
        .map((item) => startUpload(item, targetConversationId))
    );
  };

  const retryUpload = async (item: ComposerAttachment) => {
    try {
      await startUpload(item, await resolveConversation());
    } catch (error) {
      setAttachment(item.id, { status: "error", error: errorMessage(error) });
    }
  };

  const removeAttachment = async (item: ComposerAttachment) => {
    if (item.status === "uploading") {
      controllersRef.current.get(item.id)?.abort();
      try {
        await cancelChatAttachmentUpload(await resolveConversation(), item.uploadId);
        setAttachment(item.id, { status: "cancelled", progress: 0 });
      } catch (error) {
        setAttachment(item.id, { status: "error", error: errorMessage(error) });
      }
      return;
    }
    if (item.objectId && preparedConversationRef.current) {
      try {
        await cancelChatAttachment(preparedConversationRef.current, item.objectId);
      } catch (error) {
        setAttachment(item.id, { status: "error", error: errorMessage(error) });
        return;
      }
    }
    setAttachments((current) => current.filter((candidate) => candidate.id !== item.id));
  };

  const send = async () => {
    // canSend 已包含 !hasUploading：上传中按钮禁用且 Enter 发送同门，见
    // 下方键处理；这里不再重复拦截（Issue 04 移除不可达分支）。
    if (!canSend || generating) return;
    const ids = uploadedAttachments.flatMap((item) => (item.objectId ? [item.objectId] : []));
    try {
      const accepted = await onSend(
        text.trim() || "（仅附件）",
        ids,
        conversationId ?? preparedConversationRef.current,
        useKnowledgeBase
      );
      if (accepted === false) return;
      setText("");
      setAttachments((current) =>
        current.filter((item) => item.status !== "uploaded" || !item.objectId)
      );
      setToolNotice("");
      cancelRecording();
      requestAnimationFrame(autoGrow);
    } catch (error) {
      setToolNotice(errorMessage(error));
    }
  };

  const insertToolPrefix = (prefix: string) => {
    setText((current) => (current.startsWith(prefix) ? current : `${prefix}${current}`));
    setToolNotice("");
    requestAnimationFrame(() => {
      const element = textareaRef.current;
      if (!element) return;
      element.focus();
      element.setSelectionRange(element.value.length, element.value.length);
      autoGrow();
    });
  };

  const prefillNonce = prefill?.nonce;
  useEffect(() => {
    if (prefillNonce === undefined || !prefill) return;
    insertToolPrefix(prefill.text);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefillNonce]);

  // ------------------------------------------------------------------
  // Issue 30：录音状态机（MediaRecorder → 固定 ASR 快照 → 可编辑文本）
  // ------------------------------------------------------------------

  const clearDictationTimer = () => {
    if (dictationTimerRef.current !== null) {
      window.clearInterval(dictationTimerRef.current);
      dictationTimerRef.current = null;
    }
  };

  const stopAudioTracks = () => {
    const stream = streamRef.current;
    if (stream) {
      stream.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    }
  };

  /** 取消录音/转写：停止音轨、丢弃片段与在途请求，不遗留任何状态。 */
  const cancelRecording = () => {
    const recorder = mediaRecorderRef.current;
    if (recorder && recorder.state !== "inactive") {
      recorder.onstop = null;
      try {
        recorder.stop();
      } catch {
        // 已停止/不活动时忽略
      }
    }
    mediaRecorderRef.current = null;
    stopAudioTracks();
    chunksRef.current = [];
    pendingAudioRef.current = null;
    dictationAbortRef.current?.abort();
    dictationAbortRef.current = null;
    clearDictationTimer();
    setDictationSeconds(0);
    setDictationTranscribed(false);
    setDictationError("");
    setDictationPhase("idle");
  };

  /** 把已录片段提交到固定 ASR 快照（停止录音后才提交；失败保留片段可重试）。 */
  const transcribePendingAudio = async () => {
    const audio = pendingAudioRef.current;
    if (!audio || audio.size === 0) {
      setDictationError("录音为空，请重新录制。");
      setDictationPhase("error");
      return;
    }
    setDictationPhase("transcribing");
    setDictationError("");
    const controller = new AbortController();
    dictationAbortRef.current = controller;
    let targetConversationId: string | null = null;
    try {
      targetConversationId = await resolveConversation();
    } catch {
      targetConversationId = null;
    }
    if (!targetConversationId) {
      dictationAbortRef.current = null;
      setDictationError("无法获取当前对话，请稍后重试。");
      setDictationPhase("error");
      return;
    }
    try {
      const projection = await transcribeDictation(
        targetConversationId,
        audio,
        dictationSecondsRef.current,
        controller.signal
      );
      dictationAbortRef.current = null;
      if (projection.status === "success" && projection.transcript) {
        // 转写结果进入输入框可自由编辑；不自动产生用户消息。
        pendingAudioRef.current = null;
        setText(projection.transcript);
        setDictationTranscribed(true);
        setDictationPhase("idle");
        requestAnimationFrame(() => {
          autoGrow();
          textareaRef.current?.focus();
        });
      } else {
        // 失败保留录音片段：同一音频可原样重试（受控重试，不换快照）。
        setDictationError(projection.error_message || "转写失败，请重试或重新录制。");
        setDictationPhase("error");
      }
    } catch (error) {
      dictationAbortRef.current = null;
      if (error instanceof DOMException && error.name === "AbortError") {
        // 用户取消转写：不遗留待发送文本与后台请求。
        pendingAudioRef.current = null;
        setDictationPhase("idle");
        return;
      }
      if (error instanceof TypeError) {
        // fetch 网络层失败（断网/服务不可达）映射为可操作中文原因。
        setDictationError("网络异常，转写失败，请检查网络后重试。");
      } else {
        setDictationError(error instanceof Error ? error.message : "转写失败，请重试或重新录制。");
      }
      setDictationPhase("error");
    }
  };

  /** 停止录音并提交完整音频（不超过硬上限，到达上限自动停止提交）。 */
  const stopRecordingAndTranscribe = () => {
    const recorder = mediaRecorderRef.current;
    if (!recorder || recorder.state === "inactive") {
      cancelRecording();
      return;
    }
    clearDictationTimer();
    recorder.onstop = () => {
      // 先在 onstop 里取完数据再停音轨：先停轨会截断 MediaRecorder 数据。
      const blob = new Blob(chunksRef.current, {
        type: recorder.mimeType || "audio/webm",
      });
      chunksRef.current = [];
      stopAudioTracks();
      mediaRecorderRef.current = null;
      pendingAudioRef.current = blob.size > 0 ? blob : null;
      void transcribePendingAudio();
    };
    try {
      recorder.stop();
    } catch {
      cancelRecording();
    }
  };

  /** 开始录音：申请麦克风权限（拒绝/无设备/被占用分别给出中文原因）。 */
  const startRecording = async () => {
    if (dictationPhase === "recording" || dictationPhase === "transcribing") return;
    if (!asr.available) {
      // GQ-03：听写由全局运行凭据驱动，入口恒可用；该分支仅防御未来
      // 调用方传入不可用状态，不再引导前往密钥设置页。
      setDictationError(asr.reason ?? "语音转写能力暂不可用，请稍后重试。");
      setDictationPhase("error");
      return;
    }
    // 重新录制：先清掉上一段转写文本（用户显式选择重录，不留待发送文本）。
    if (dictationTranscribed) {
      setText("");
      setDictationTranscribed(false);
      requestAnimationFrame(autoGrow);
    }
    setDictationError("");
    if (!("MediaRecorder" in window)) {
      setDictationError("当前浏览器不支持录音，请改用键盘输入。");
      setDictationPhase("error");
      return;
    }
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (error) {
      const name = error instanceof DOMException ? error.name : "";
      if (name === "NotAllowedError" || name === "SecurityError") {
        setDictationError("麦克风权限被拒绝，请在浏览器设置中允许麦克风后重试。");
      } else if (name === "NotFoundError" || name === "OverconstrainedError") {
        setDictationError("未检测到可用麦克风设备，请检查设备连接后重试。");
      } else if (name === "NotReadableError") {
        setDictationError("麦克风设备不可用或被其他应用占用，请检查后重试。");
      } else {
        setDictationError("无法启动麦克风，请检查设备与浏览器权限后重试。");
      }
      setDictationPhase("error");
      return;
    }
    streamRef.current = stream;
    chunksRef.current = [];
    let recorder: MediaRecorder;
    try {
      recorder = new MediaRecorder(stream);
    } catch {
      stopAudioTracks();
      setDictationError("当前浏览器不支持录音，请改用键盘输入。");
      setDictationPhase("error");
      return;
    }
    recorder.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) chunksRef.current.push(event.data);
    };
    try {
      recorder.start();
    } catch {
      stopAudioTracks();
      setDictationError("无法启动录音，请检查麦克风后重试。");
      setDictationPhase("error");
      return;
    }
    mediaRecorderRef.current = recorder;
    setDictationSeconds(0);
    setDictationPhase("recording");
    // 时长计时；到达硬上限自动停止并提交（服务端同款 300 秒上限）。
    const startedAt = Date.now();
    dictationTimerRef.current = window.setInterval(() => {
      const seconds = Math.floor((Date.now() - startedAt) / 1000);
      dictationSecondsRef.current = seconds;
      setDictationSeconds(seconds);
      if (seconds >= MAX_RECORDING_SECONDS) {
        void stopRecordingAndTranscribe();
      }
    }, 250);
  };

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && generating) {
        event.preventDefault();
        onStop?.();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [generating, onStop]);

  useEffect(
    () => () => {
      cancelRecording();
      controllersRef.current.forEach((controller, id) => {
        controller.abort();
        const item = attachmentsRef.current.find((candidate) => candidate.id === id);
        const targetConversationId = conversationId ?? preparedConversationRef.current;
        if (item && targetConversationId) {
          void cancelChatAttachmentUpload(targetConversationId, item.uploadId);
        }
      });
    },
    [conversationId]
  );

  // Issue 04：关页重开后恢复未发送草稿——从服务端读取本会话「已上传
  // 未绑定」附件并显示为待绑定状态（浏览器 sessionStorage 不承担事实
  // 源）。恢复失败静默：附件仍在服务端，可再次打开恢复；非数组响应
  // （协议替身等）按空列表处理，不中断输入。
  useEffect(() => {
    if (!conversationId) return;
    let cancelled = false;
    void (async () => {
      try {
        const unbound = await listUnboundAttachments(conversationId);
        if (cancelled || !Array.isArray(unbound) || unbound.length === 0) return;
        const restored = unbound.map((item) => ({
          id: newId("attachment"),
          file: new File([], item.original_filename, { type: item.media_type }),
          filename: item.original_filename,
          uploadId: `restore-${item.object_id}`,
          objectId: item.object_id,
          status: "uploaded" as const,
          progress: 100,
          size: item.content_length,
        }));
        if (cancelled) return;
        // 去重只在 updater 内做一次：以提交时的最新附件状态为准
        // （获取期间用户可能已重新上传同一文件）。
        setAttachments((current) => {
          const known = new Set(current.map((item) => item.objectId));
          return [...current, ...restored.filter((item) => !known.has(item.objectId))];
        });
      } catch {
        // 恢复失败静默（协议替身可能拦截并返回非预期形状）。
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [conversationId]);

  const iconButtonStyle: React.CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    minWidth: "var(--target-size)",
    minHeight: "var(--target-size)",
    border: "none",
    borderRadius: "var(--radius-md)",
    backgroundColor: "transparent",
    color: "var(--color-text-secondary)",
    cursor: "pointer",
  };

  return (
    <div
      data-testid="composer"
      style={{
        border: "1px solid var(--color-border-strong)",
        borderRadius: "var(--radius-xl)",
        backgroundColor: "var(--color-surface)",
        boxShadow: "var(--shadow-sm)",
        padding: "var(--space-3)",
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-2)",
      }}
    >
      {attachments.length > 0 && (
        <ul role="list" aria-label="待发送附件" style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-2)" }}>
          {attachments.map((item) => (
            <li
              key={item.id}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-2)",
                maxWidth: "100%",
                padding: "var(--space-1) var(--space-2)",
                borderRadius: "var(--radius-md)",
                border: `1px solid ${item.status === "error" ? "var(--color-status-error)" : "var(--color-border)"}`,
                backgroundColor: "var(--color-bg-secondary)",
                fontSize: "var(--text-sm)",
                color: "var(--color-text-secondary)",
              }}
            >
              <Icon name="uploadFile" size={16} aria-hidden />
              <span style={{ minWidth: 0 }}>
                <span
                  style={{
                    display: "block",
                    maxWidth: "22rem",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                  title={item.filename}
                >
                  {item.filename}
                </span>
                <span role={item.status === "error" ? "alert" : "status"} style={{ fontSize: "var(--text-xs)" }}>
                  {formatFileType(item.file.type, item.filename)} · {formatSize(item.size ?? item.file.size)} · {attachmentStatus(item)}
                </span>
              </span>
              {item.status === "uploading" && (
                <progress value={item.progress} max={100} aria-label={`${item.filename} 上传进度`} />
              )}
              {item.status === "error" ? (
                <button
                  type="button"
                  onClick={() => void retryUpload(item)}
                  aria-label={`重试上传 ${item.filename}`}
                  style={{ ...iconButtonStyle, minWidth: "auto", minHeight: "auto", padding: "var(--space-1)" }}
                >
                  重试
                </button>
              ) : (
                <button
                  type="button"
                  aria-label={`${item.status === "uploading" ? "取消上传" : "移除附件"} ${item.filename}`}
                  onClick={() => void removeAttachment(item)}
                  style={{ ...iconButtonStyle, minWidth: "auto", minHeight: "auto", padding: "var(--space-1)" }}
                >
                  <Icon name={item.status === "uploading" ? "close" : "close"} size={14} aria-hidden />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      <label htmlFor="composer-input" className="sc-visually-hidden">输入消息</label>
      <textarea
        ref={textareaRef}
        id="composer-input"
        rows={2}
        value={text}
        onChange={(event) => {
          setText(event.target.value);
          setToolNotice("");
          autoGrow();
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            void send();
          }
        }}
        placeholder="向 BridGes 提问，或描述你的学习目标"
        style={{
          width: "100%",
          border: "none",
          outline: "none",
          resize: "none",
          backgroundColor: "transparent",
          color: "var(--color-text-primary)",
          fontSize: "var(--text-base)",
          lineHeight: "var(--line-height-normal)",
          maxHeight: "12rem",
        }}
      />

      {learningProject && (
        <div
          data-testid="composer-learning-project-chip"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: "var(--space-2)",
            alignSelf: "flex-start",
            maxWidth: "100%",
            padding: "var(--space-1) var(--space-2)",
            borderRadius: "var(--radius-md)",
            border: "1px solid var(--color-border)",
            backgroundColor: "var(--color-bg-secondary)",
            fontSize: "var(--text-sm)",
            color: "var(--color-text-secondary)",
          }}
        >
          <Icon name="learningProject" size={16} aria-hidden />
          <span
            style={{
              minWidth: 0,
              maxWidth: "22rem",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
            title={learningProject.name}
          >
            {learningProject.name}
          </span>
          <button
            type="button"
            aria-label="清除学习项目选择"
            onClick={() => onSelectLearningProject?.(null)}
            style={{ ...iconButtonStyle, minWidth: "auto", minHeight: "auto", padding: "var(--space-1)" }}
          >
            <Icon name="close" size={14} aria-hidden />
          </button>
        </div>
      )}

      {/* Issue 36：选中的插件 chip（与学习项目 chip 平行，随对话持久化）。
          SKILL 插件展示名称与移除；MCP 插件额外提供「调用」按钮（真实
          invoke 走消息流）。停用/卸载/撤权后由服务端清洗，此处不再出现。 */}
      {pluginSelection.length > 0 && (
        <div
          data-testid="composer-plugin-chips"
          role="group"
          aria-label="本对话选中的插件"
          style={{
            display: "inline-flex",
            alignItems: "center",
            flexWrap: "wrap",
            gap: "var(--space-2)",
            alignSelf: "flex-start",
            maxWidth: "100%",
          }}
        >
          {pluginSelection.map((item) => {
            const key = `${item.kind}:${item.plugin_id}`;
            const name = pluginNames[key] ?? item.plugin_id;
            return (
              <span
                key={key}
                data-testid={`composer-selected-plugin-${item.plugin_id}`}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "var(--space-2)",
                  padding: "var(--space-1) var(--space-2)",
                  borderRadius: "var(--radius-md)",
                  border: "1px solid var(--color-border)",
                  backgroundColor: "var(--color-bg-secondary)",
                  fontSize: "var(--text-sm)",
                  color: "var(--color-text-secondary)",
                }}
              >
                <Icon name={item.kind === "skill" ? "plugins" : "mcpServer"} size={14} aria-hidden />
                <span
                  style={{
                    maxWidth: "14rem",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                  title={`${name}（${item.kind === "skill" ? "SKILL 插件" : "MCP 服务器"}）`}
                >
                  {name}
                </span>
                {item.kind === "mcp" && onInvokeMcp && (
                  <button
                    type="button"
                    aria-label={`调用插件 ${name}`}
                    data-testid={`composer-invoke-plugin-${item.plugin_id}`}
                    onClick={() => onInvokeMcp(item.plugin_id)}
                    style={{ ...iconButtonStyle, minWidth: "auto", minHeight: "auto", padding: "var(--space-1)" }}
                  >
                    <Icon name="play" size={12} aria-hidden />
                  </button>
                )}
                <button
                  type="button"
                  aria-label={`移除插件 ${name}`}
                  onClick={() => onRemovePlugin?.(item.kind, item.plugin_id)}
                  style={{ ...iconButtonStyle, minWidth: "auto", minHeight: "auto", padding: "var(--space-1)" }}
                >
                  <Icon name="close" size={14} aria-hidden />
                </button>
              </span>
            );
          })}
        </div>
      )}

      {/* Issue 20：本轮启用的来源层面板（附件 → 项目 → 知识库）。
          附件/项目仅作展示，知识库可发送前关闭；关闭后本轮检索与引用
          均不含知识库候选。只在真实对话（有 conversationId 或延迟创建
          钩子）渲染：模板设计基线不引入实时功能。 */}
      {isRealChat && (
      <div
        data-testid="composer-source-layers"
        role="group"
        aria-label="本轮检索来源"
        style={{
          display: "flex",
          alignItems: "center",
          flexWrap: "wrap",
          gap: "var(--space-2)",
          padding: "var(--space-1) 0",
        }}
      >
        <span
          data-testid="source-layer-attachment"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: "var(--space-1)",
            padding: "2px var(--space-2)",
            borderRadius: "999px",
            border: "1px solid var(--color-border)",
            backgroundColor: "var(--color-bg-secondary)",
            fontSize: "var(--text-xs)",
            color: "var(--color-text-secondary)",
          }}
        >
          <Icon name="uploadFile" size={13} aria-hidden />
          {uploadedAttachments.length > 0
            ? `当前附件 ${uploadedAttachments.length} 份`
            : "当前附件 未附加"}
        </span>
        <span
          data-testid="source-layer-project"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: "var(--space-1)",
            padding: "2px var(--space-2)",
            borderRadius: "999px",
            border: "1px solid var(--color-border)",
            backgroundColor: "var(--color-bg-secondary)",
            fontSize: "var(--text-xs)",
            color: "var(--color-text-secondary)",
            maxWidth: "16rem",
          }}
          title={learningProject?.name ?? "该对话未归属学习项目"}
        >
          <Icon name="learningProject" size={13} aria-hidden />
          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {learningProject ? `项目：${learningProject.name}` : "当前项目 未归属"}
          </span>
        </span>
        <button
          type="button"
          role="switch"
          aria-checked={useKnowledgeBase}
          data-testid="source-layer-knowledge-base"
          onClick={() => setUseKnowledgeBase((value) => !value)}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: "var(--space-1)",
            padding: "2px var(--space-2)",
            borderRadius: "999px",
            border: `1px solid ${
              useKnowledgeBase ? "var(--color-accent-primary)" : "var(--color-border)"
            }`,
            backgroundColor: useKnowledgeBase
              ? "var(--color-accent-primary-soft)"
              : "var(--color-bg-secondary)",
            fontSize: "var(--text-xs)",
            color: useKnowledgeBase
              ? "var(--color-accent-primary)"
              : "var(--color-text-tertiary)",
            cursor: "pointer",
            font: "inherit",
            minHeight: "var(--target-size)",
          }}
          title={useKnowledgeBase ? "本轮将检索全局知识库，点击关闭" : "点击开启本轮全局知识库检索"}
        >
          <Icon name="knowledgeBase" size={13} aria-hidden />
          全局知识库
          <span
            aria-hidden="true"
            style={{
              display: "inline-block",
              width: 24,
              height: 14,
              borderRadius: 999,
              padding: 2,
              backgroundColor: useKnowledgeBase
                ? "var(--color-accent-primary)"
                : "var(--color-border-strong)",
              transition: "background-color 150ms ease",
            }}
          >
            <span
              style={{
                display: "block",
                width: 10,
                height: 10,
                borderRadius: "50%",
                backgroundColor: "#FFFFFF",
                transform: useKnowledgeBase ? "translateX(10px)" : "translateX(0)",
                transition: "transform 150ms ease",
              }}
            />
          </span>
        </button>
      </div>
      )}

      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-1)" }}>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          tabIndex={-1}
          aria-hidden="true"
          data-testid="composer-file-input"
          onChange={(event) => {
            const files = event.target.files;
            event.target.value = "";
            void addSelectedFiles(files);
          }}
          style={{ display: "none" }}
        />
        <Menu
          ariaLabel="更多功能"
          openUp
          trigger={<Icon name="plus" size={20} aria-hidden />}
          triggerStyle={{
            width: "var(--target-size)",
            padding: 0,
            justifyContent: "center",
            border: "1px solid var(--color-border)",
          }}
          items={[
            // Issue 36：六入口固定顺序（上传文件/图片、论文搜索、文章
            // 人味化、生涯规划助手、选择学习项目、选择已启用插件）。
            {
              label: "上传文件/图片",
              icon: "uploadFile",
              onSelect: () => fileInputRef.current?.click(),
            },
            // Issue 28/29：文章人味化与生涯规划进入真实任务对话框，
            // 不再只是预填前缀；论文搜索仍为结构化预填（真实 arXiv MCP）。
            ...TOOL_PROMPTS.filter(
              (tool) =>
                tool.label !== IMAGE_TOOL_LABEL && tool.label !== VIDEO_TOOL_LABEL
            ).map((tool) => ({
              label: tool.label,
              icon: tool.icon,
              onSelect:
                tool.label === HUMANIZER_TOOL_LABEL && onOpenHumanizer
                  ? () => {
                      setToolNotice("");
                      onOpenHumanizer?.();
                    }
                  : tool.label === CAREER_TOOL_LABEL && onOpenCareer
                    ? () => {
                        setToolNotice("");
                        onOpenCareer?.();
                      }
                    : () => insertToolPrefix(tool.prefix),
              returnFocus: false,
            })),
            ...(onSelectLearningProject
              ? [
                  {
                    label: "选择学习项目",
                    icon: "learningProject" as const,
                    returnFocus: false,
                    onSelect: () => setProjectPickerOpen(true),
                  },
                ]
              : []),
            // Issue 36：占位「选择已启用插件」实现为真实选择器（可用集合
            // = 当前账户已安装且启用；选择随对话持久化）。
            ...(onSelectPlugins
              ? [
                  {
                    label: "选择已启用插件",
                    icon: "plugins" as const,
                    returnFocus: false,
                    onSelect: () => {
                      setToolNotice("");
                      onSelectPlugins?.();
                    },
                  },
                ]
              : []),
            // 既有能力入口（图片/视频生成）保留在清单六入口之后：
            // 它们是已实现能力，非清单新增项。
            ...TOOL_PROMPTS.filter(
              (tool) =>
                tool.label === IMAGE_TOOL_LABEL || tool.label === VIDEO_TOOL_LABEL
            ).map((tool) => ({
              label: tool.label,
              icon: tool.icon,
              onSelect:
                tool.label === IMAGE_TOOL_LABEL && onOpenImage
                  ? () => {
                      // Issue 31：图片能力不可用时入口明确停用并说明
                      // 原因（探测快照；服务端仍做权威校验）。
                      if (!image.available) {
                        setToolNotice(
                          image.reason ?? "图片生成与编辑能力当前不可用。"
                        );
                        return;
                      }
                      setToolNotice("");
                      onOpenImage?.();
                    }
                  : tool.label === VIDEO_TOOL_LABEL && onOpenVideo
                    ? () => {
                        // Issue 32：视频能力不可用时入口明确停用并说明
                        // 原因（探测快照；服务端仍做权威校验）。
                        if (!video.available) {
                          setToolNotice(
                            video.reason ?? "视频生成能力当前不可用。"
                          );
                          return;
                        }
                        setToolNotice("");
                        onOpenVideo?.();
                      }
                    : () => insertToolPrefix(tool.prefix),
              returnFocus: false,
            })),
          ]}
        />

        <span style={{ flex: 1 }} />

        {dictationPhase === "recording" && (
          <span className={styles.composerDictationRow} role="status" aria-live="polite">
            <span className={styles.composerDictationDot} aria-hidden="true" />
            <span className={styles.composerDictationTimer}>
              {formatDictationTime(dictationSeconds)}
            </span>
            <button
              type="button"
              className={styles.composerDictationAction}
              aria-label="停止录音并转写"
              onClick={() => void stopRecordingAndTranscribe()}
            >
              <Icon name="stopSquare" size={16} aria-hidden />
              停止
            </button>
            <button
              type="button"
              className={styles.composerDictationAction}
              aria-label="取消录音"
              onClick={cancelRecording}
            >
              <Icon name="close" size={16} aria-hidden />
              取消
            </button>
          </span>
        )}

        {dictationPhase === "transcribing" && (
          <span className={styles.composerDictationRow} role="status" aria-live="polite">
            <span className={styles.readAloudSpinner} aria-hidden="true" />
            <span className={styles.composerDictationText}>正在转写…</span>
            <button
              type="button"
              className={styles.composerDictationAction}
              aria-label="取消转写"
              onClick={cancelRecording}
            >
              <Icon name="close" size={16} aria-hidden />
              取消
            </button>
          </span>
        )}

        {dictationPhase === "error" && (
          <span className={styles.composerDictationRow} role="alert">
            <Icon name="alert" size={16} aria-hidden />
            <span className={styles.composerDictationError}>{dictationError}</span>
            {pendingAudioRef.current && (
              <button
                type="button"
                className={styles.composerDictationAction}
                aria-label="重试转写"
                onClick={() => void transcribePendingAudio()}
              >
                <Icon name="retry" size={16} aria-hidden />
                重试
              </button>
            )}
            <button
              type="button"
              className={styles.composerDictationAction}
              aria-label="重新录制"
              onClick={() => void startRecording()}
            >
              <Icon name="dictation" size={16} aria-hidden />
              重新录制
            </button>
            <button
              type="button"
              className={styles.composerDictationAction}
              aria-label="取消听写"
              onClick={cancelRecording}
            >
              <Icon name="close" size={16} aria-hidden />
              取消
            </button>
          </span>
        )}

        {dictationPhase === "idle" && !asr.available && asr.reason && (
          <span className={styles.composerDictationRow} role="note">
            <Icon name="alert" size={16} aria-hidden />
            <span className={styles.composerDictationText}>{asr.reason}</span>
          </span>
        )}

        {dictationPhase === "idle" && dictationTranscribed && (
          <span className={styles.composerDictationRow} role="status">
            <span className={styles.composerDictationText}>已转写，可编辑后发送</span>
            <button
              type="button"
              className={styles.composerDictationAction}
              aria-label="重新录制"
              onClick={() => void startRecording()}
            >
              <Icon name="dictation" size={16} aria-hidden />
              重新录制
            </button>
          </span>
        )}

        <button
          type="button"
          aria-label="开始听写"
          aria-pressed={dictationPhase === "recording"}
          disabled={!asr.available}
          title={asr.available ? undefined : asr.reason}
          onClick={() => void startRecording()}
          style={{
            ...iconButtonStyle,
            color:
              dictationPhase === "recording"
                ? "var(--color-status-error)"
                : "var(--color-text-secondary)",
            opacity: asr.available ? 1 : 0.45,
            cursor: asr.available ? "pointer" : "not-allowed",
          }}
        >
          <Icon name="dictation" size={20} aria-hidden />
        </button>
        {generating ? (
          <Button variant="secondary" size="sm" onClick={onStop} aria-label="停止生成">
            <Icon name="close" size={16} aria-hidden />停止
          </Button>
        ) : (
          <Button
            variant="primary"
            size="sm"
            onClick={() => void send()}
            disabled={!canSend}
            aria-label="发送消息"
            title={canSend ? "发送" : "输入内容后才能发送"}
          >
            <Icon name="send" size={16} aria-hidden />发送
          </Button>
        )}
      </div>
      {(toolNotice || hasUploading) && (
        <p
          role={toolNotice ? "alert" : "status"}
          data-testid={toolNotice ? "tool-unavailable-notice" : "composer-status"}
          style={{ margin: 0, fontSize: "var(--text-sm)", color: toolNotice ? "var(--color-status-error)" : "var(--color-text-secondary)" }}
        >
          {toolNotice || "附件正在上传，完成后即可发送。"}
        </p>
      )}
      {projectPickerOpen && onSelectLearningProject && (
        <LearningProjectPickerDialog
          selectedProjectId={learningProject?.project_id ?? null}
          onSelect={(project) => {
            setProjectPickerOpen(false);
            onSelectLearningProject(
              project ? { project_id: project.project_id, name: project.name } : null
            );
          }}
          onClose={() => setProjectPickerOpen(false)}
        />
      )}
    </div>
  );
}
