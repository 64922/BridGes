"use client";

import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/design-system/Button";
import { Icon } from "@/components/design-system/Icon";
import {
  chatAttachmentDraftContentUrl,
  listChatAttachmentDrafts,
  removeChatAttachmentDraft,
  transcribeDictation,
  uploadChatAttachmentDraft,
} from "@/lib/api";
import type { ChatAttachmentDraftProjection } from "@/lib/api";
import type { CapabilityAvailability } from "./chat/ReadAloudControls";
import styles from "./chat/chat.module.css";

// Issue 05：照片附件客户端约束（与服务端同款限制，提前拦截减少无效上传）。
const PHOTO_ACCEPT = "image/png,image/jpeg,image/gif,image/webp";
const PHOTO_MAX_BYTES = 10 * 1024 * 1024;
const PHOTO_MAX_COUNT = 10;
const PHOTO_TYPE_LABELS: Record<string, string> = {
  "image/png": "PNG",
  "image/jpeg": "JPEG",
  "image/gif": "GIF",
  "image/webp": "WebP",
};
// 中文原因与服务端保持一致：无论客户端还是服务端拦截，用户看到同一句话。
const UNSUPPORTED_TYPE_MESSAGE =
  "暂不支持该文件类型，聊天附件目前仅支持 PNG、JPEG、GIF、WebP 图片。";
const TOO_LARGE_MESSAGE = "文件超过 10 MB 大小限制，请压缩后重试。";
const DUPLICATE_MESSAGE = "同一附件不能重复添加。";
const TOO_MANY_MESSAGE = "一条消息最多添加 10 个附件。";

/** 单条附件提示（类型/体积/数量等被拒绝时的中文原因，可逐条关闭）。 */
interface DraftError {
  id: number;
  filename?: string;
  message: string;
}

interface ComposerProps {
  /**
   * 发送回调：``attachmentIds`` 为本轮照片草稿的 object_id（按页序排列），
   * 空数组表示纯文字消息。纯附件（无文字）也允许发送。
   */
  onSend: (text: string, attachmentIds: string[]) => Promise<boolean> | boolean | void;
  /** 已存在的真实对话；用于发送消息与听写。 */
  conversationId?: string;
  generating?: boolean;
  onStop?: () => void;
  /** 外部预填请求（建议卡等）：nonce 变化时把 text 作为结构化意图填入并聚焦 */
  prefill?: { text: string; nonce: number } | null;
  /** Issue 30：ASR 听写能力可用性（账户级探测快照；不可用时禁用入口并说明原因） */
  asr?: CapabilityAvailability;
  /** 新聊天首页使用原子首轮，并在会话创建前禁用听写入口。 */
  variant?: "conversation" | "new-chat";
}

/** 对话输入区：发送普通消息与听写结果。 */
export function Composer({
  onSend,
  conversationId,
  generating = false,
  onStop,
  prefill = null,
  asr = { available: true },
  variant = "conversation",
}: ComposerProps) {
  const [text, setText] = useState("");
  // Issue 30：听写状态机（idle → recording → transcribing → idle/error）。
  // 停止录音后才提交完整音频到固定 ASR 快照；转写结果可编辑回填，绝不
  // 自动发送；取消/重录不遗留待发送文本、跨账户临时音频或后台孤儿任务。
  const [dictationPhase, setDictationPhase] = useState<
    "idle" | "recording" | "transcribing" | "error"
  >("idle");
  const [dictationError, setDictationError] = useState("");
  const [dictationSeconds, setDictationSeconds] = useState(0);
  const [dictationTranscribed, setDictationTranscribed] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const dictationTimerRef = useRef<number | null>(null);
  const dictationSecondsRef = useRef(0);
  const dictationAbortRef = useRef<AbortController | null>(null);
  const pendingAudioRef = useRef<Blob | null>(null);
  // Issue 05：照片草稿按页序保存（object_id 即草稿，发送时随消息原子绑定）。
  const [drafts, setDrafts] = useState<ChatAttachmentDraftProjection[]>([]);
  const [draftErrors, setDraftErrors] = useState<DraftError[]>([]);
  const [uploadingCount, setUploadingCount] = useState(0);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const draftErrorSeqRef = useRef(0);
  // 有文字或已有照片即可发送；上传未完成的批次禁止提前发送。
  const canSend =
    (text.trim().length > 0 || drafts.length > 0) &&
    dictationPhase === "idle" &&
    uploadingCount === 0;

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

  const send = async () => {
    if (!canSend || generating) return;
    const attachmentIds = drafts.map((draft) => draft.object_id);
    try {
      const accepted = await onSend(text.trim(), attachmentIds);
      if (accepted === false) return;
      // 发送成功：文字与草稿一并清空（草稿已在服务端随消息绑定）。
      setText("");
      setDrafts([]);
      setDraftErrors([]);
      cancelRecording();
      requestAnimationFrame(autoGrow);
    } catch (error) {
      // 发送错误由宿主页面统一呈现，文字与照片附件保留等待重试。
      void error;
    }
  };

  // ------------------------------------------------------------------
  // Issue 05：照片附件草稿（选择/拖入/粘贴 → 上传草稿 → 发送时绑定）
  // ------------------------------------------------------------------

  const addDraftError = (message: string, filename?: string) => {
    draftErrorSeqRef.current += 1;
    const id = draftErrorSeqRef.current;
    setDraftErrors((current) => [...current, { id, filename, message }]);
  };

  const dismissDraftError = (id: number) => {
    setDraftErrors((current) => current.filter((error) => error.id !== id));
  };

  /** 按服务端同款规则预校验后上传为草稿；单张失败不影响其他草稿与正文。 */
  const uploadDrafts = async (files: File[]) => {
    for (const file of files) {
      try {
        const projection = await uploadChatAttachmentDraft(
          file,
          file.name,
          crypto.randomUUID()
        );
        setDrafts((current) =>
          current.some((draft) => draft.object_id === projection.object_id)
            ? current
            : [...current, projection]
        );
      } catch (error) {
        addDraftError(
          error instanceof Error ? error.message : "上传失败，请检查网络后重试。",
          file.name
        );
      } finally {
        setUploadingCount((count) => Math.max(0, count - 1));
      }
    }
  };

  const addFiles = (files: File[]) => {
    if (files.length === 0) return;
    const seenNames = new Set(
      drafts.map((draft) => `${draft.original_filename}:${draft.content_length}`)
    );
    let capacity = PHOTO_MAX_COUNT - drafts.length;
    const accepted: File[] = [];
    for (const file of files) {
      if (!PHOTO_TYPE_LABELS[file.type]) {
        addDraftError(UNSUPPORTED_TYPE_MESSAGE, file.name);
      } else if (file.size > PHOTO_MAX_BYTES) {
        addDraftError(TOO_LARGE_MESSAGE, file.name);
      } else if (seenNames.has(`${file.name}:${file.size}`)) {
        addDraftError(DUPLICATE_MESSAGE, file.name);
      } else if (capacity <= 0) {
        addDraftError(TOO_MANY_MESSAGE, file.name);
      } else {
        seenNames.add(`${file.name}:${file.size}`);
        accepted.push(file);
        capacity -= 1;
      }
    }
    if (accepted.length === 0) return;
    setUploadingCount((count) => count + accepted.length);
    void uploadDrafts(accepted);
  };

  const onFileInputChange = () => {
    const input = fileInputRef.current;
    if (!input) return;
    addFiles(Array.from(input.files ?? []));
    input.value = "";
  };

  /** 调整页序（上移/下移按钮均为可聚焦元素，键盘即可完成排序）。 */
  const moveDraft = (objectId: string, offset: -1 | 1) => {
    setDrafts((current) => {
      const index = current.findIndex((draft) => draft.object_id === objectId);
      const target = index + offset;
      if (index < 0 || target < 0 || target >= current.length) return current;
      const next = [...current];
      const [item] = next.splice(index, 1);
      next.splice(target, 0, item);
      return next;
    });
  };

  const removeDraft = (objectId: string) => {
    setDrafts((current) => current.filter((draft) => draft.object_id !== objectId));
    void removeChatAttachmentDraft(objectId).catch(() => {
      // 删除请求失败不回滚本地列表：服务端草稿由过期清理兜底。
    });
  };

  // 页面重开后恢复账户既有草稿（草稿账户隔离，刷新/重登不丢失）。
  useEffect(() => {
    let cancelled = false;
    listChatAttachmentDrafts()
      .then((rows) => {
        if (!cancelled) setDrafts(rows);
      })
      .catch(() => {
        // 恢复失败不打断输入：草稿仍在服务端，可稍后重试或重新添加。
      });
    return () => {
      cancelled = true;
    };
  }, []);


  const applyPrefill = (value: string) => {
    setText((current) => (current.startsWith(value) ? current : `${value}${current}`));
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
    applyPrefill(prefill.text);
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
    if (!conversationId) {
      setDictationError("发送首条消息后可使用听写。");
      setDictationPhase("error");
      return;
    }
    setDictationPhase("transcribing");
    setDictationError("");
    const controller = new AbortController();
    dictationAbortRef.current = controller;
    try {
      const projection = await transcribeDictation(
        conversationId,
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
    if (variant === "new-chat" && !conversationId) {
      setDictationError("发送首条消息后可使用听写。");
      setDictationPhase("error");
      return;
    }
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
    },
    [conversationId]
  );

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
      onDragOver={(event) => {
        if (!event.dataTransfer.types.includes("Files")) return;
        event.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(event) => {
        const files = Array.from(event.dataTransfer.files ?? []);
        if (files.length === 0) return;
        event.preventDefault();
        setDragOver(false);
        addFiles(files);
      }}
      style={{
        border: "1px solid var(--color-border-strong)",
        outline: dragOver ? "2px dashed var(--color-primary)" : undefined,
        borderRadius: "var(--radius-xl)",
        backgroundColor: "var(--color-surface)",
        boxShadow: "var(--shadow-sm)",
        padding: "var(--space-3)",
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-2)",
      }}
    >
      <label htmlFor="composer-input" className="sc-visually-hidden">输入消息</label>
      <textarea
        ref={textareaRef}
        id="composer-input"
        rows={2}
        value={text}
        onChange={(event) => {
          setText(event.target.value);
          autoGrow();
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            void send();
          }
        }}
        onPaste={(event) => {
          const files = Array.from(event.clipboardData?.files ?? []);
          if (files.length === 0) return;
          event.preventDefault();
          addFiles(files);
        }}
        placeholder="输入消息，开始日常对话"
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

      {draftErrors.length > 0 && (
        <ul
          role="alert"
          style={{
            listStyle: "none",
            margin: 0,
            padding: 0,
            display: "flex",
            flexDirection: "column",
            gap: "var(--space-1)",
          }}
        >
          {draftErrors.map((error) => (
            <li
              key={error.id}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-1)",
                color: "var(--color-status-error)",
                fontSize: "var(--text-sm)",
              }}
            >
              <Icon name="alert" size={14} aria-hidden />
              <span>{error.filename ? `${error.filename}：${error.message}` : error.message}</span>
              <button
                type="button"
                aria-label="关闭提示"
                onClick={() => dismissDraftError(error.id)}
                style={{ ...iconButtonStyle, minWidth: 0, minHeight: 0 }}
              >
                <Icon name="close" size={12} aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      )}

      {drafts.length > 0 && (
        <ul
          data-testid="composer-attachments"
          aria-label="待发送照片"
          style={{
            listStyle: "none",
            margin: 0,
            padding: 0,
            display: "flex",
            flexWrap: "wrap",
            gap: "var(--space-2)",
          }}
        >
          {drafts.map((draft, index) => {
            const filename = draft.original_filename;
            return (
              <li
                key={draft.object_id}
                style={{
                  width: 148,
                  border: "1px solid var(--color-border)",
                  borderRadius: "var(--radius-md)",
                  padding: "var(--space-2)",
                  display: "flex",
                  flexDirection: "column",
                  gap: "var(--space-1)",
                }}
              >
                {/* 同源预览：经账户授权返回；读取失败由浏览器 alt 文案兜底。 */}
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={chatAttachmentDraftContentUrl(draft.object_id)}
                  alt={`照片预览：${filename}`}
                  style={{
                    width: "100%",
                    height: 64,
                    objectFit: "cover",
                    borderRadius: "var(--radius-sm)",
                  }}
                />
                <span
                  title={filename}
                  style={{
                    fontSize: "var(--text-sm)",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {filename}
                </span>
                <span
                  style={{
                    fontSize: "var(--text-sm)",
                    color: "var(--color-text-secondary)",
                  }}
                >
                  第 {index + 1} 张
                </span>
                <span
                  style={{
                    fontSize: "var(--text-sm)",
                    color: "var(--color-text-secondary)",
                  }}
                >
                  {PHOTO_TYPE_LABELS[draft.media_type] ?? "图片"}
                </span>
                <div style={{ display: "flex", alignItems: "center", gap: "var(--space-1)" }}>
                  <button
                    type="button"
                    aria-label={`将 ${filename} 上移`}
                    disabled={index === 0}
                    onClick={() => moveDraft(draft.object_id, -1)}
                    style={{ ...iconButtonStyle, minWidth: 0, minHeight: 0, padding: "0 var(--space-1)" }}
                  >
                    ↑
                  </button>
                  <button
                    type="button"
                    aria-label={`将 ${filename} 下移`}
                    disabled={index === drafts.length - 1}
                    onClick={() => moveDraft(draft.object_id, 1)}
                    style={{ ...iconButtonStyle, minWidth: 0, minHeight: 0, padding: "0 var(--space-1)" }}
                  >
                    ↓
                  </button>
                  <span style={{ flex: 1 }} />
                  <button
                    type="button"
                    aria-label={`移除图片 ${filename}`}
                    onClick={() => removeDraft(draft.object_id)}
                    style={{ ...iconButtonStyle, minWidth: 0, minHeight: 0 }}
                  >
                    <Icon name="close" size={14} aria-hidden />
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-1)" }}>
        <span style={{ flex: 1 }} />

        {variant === "new-chat" && !conversationId && (
          <span className={styles.composerDictationText} role="note">
            发送首条消息后可使用听写
          </span>
        )}

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

        <input
          ref={fileInputRef}
          type="file"
          accept={PHOTO_ACCEPT}
          multiple
          onChange={onFileInputChange}
          style={{ display: "none" }}
        />
        <button
          type="button"
          aria-label="添加图片"
          title="添加图片（PNG、JPEG、GIF、WebP，10MB 以内）"
          onClick={() => fileInputRef.current?.click()}
          style={iconButtonStyle}
        >
          <Icon name="imagePicture" size={20} aria-hidden />
        </button>
        {uploadingCount > 0 && (
          <span role="status" className={styles.composerDictationText}>
            正在添加图片…
          </span>
        )}
        <button
          type="button"
          aria-label="开始听写"
          aria-pressed={dictationPhase === "recording"}
          disabled={!asr.available || (variant === "new-chat" && !conversationId)}
          title={
            variant === "new-chat" && !conversationId
              ? "发送首条消息后可使用听写。"
              : asr.available
                ? undefined
                : asr.reason
          }
          onClick={() => void startRecording()}
          style={{
            ...iconButtonStyle,
            color:
              dictationPhase === "recording"
                ? "var(--color-status-error)"
                : "var(--color-text-secondary)",
            opacity: asr.available && !(variant === "new-chat" && !conversationId) ? 1 : 0.45,
            cursor:
              asr.available && !(variant === "new-chat" && !conversationId)
                ? "pointer"
                : "not-allowed",
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
    </div>
  );
}
