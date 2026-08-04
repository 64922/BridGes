"use client";

import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/design-system/Button";
import { Icon } from "@/components/design-system/Icon";
import { LearningProjectPickerDialog } from "@/components/learning-projects/LearningProjectPickerDialog";
import { CHAT_TOOL_INTENTS } from "@/lib/chat-tools";
import {
  cancelChatAttachment,
  cancelChatAttachmentUpload,
  uploadChatAttachment,
} from "@/lib/api";
import { Menu } from "./Menu";

interface ComposerAttachment {
  id: string;
  file: File;
  filename: string;
  uploadId: string;
  objectId?: string;
  status: "local" | "uploading" | "uploaded" | "error" | "cancelled";
  progress: number;
  error?: string;
}

interface ComposerProps {
  onSend: (
    text: string,
    attachmentIds?: string[],
    preparedConversationId?: string
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
}

interface SpeechRecognitionResultEventLike {
  results: ArrayLike<{ 0: { transcript: string } }>;
}

interface SpeechRecognitionErrorEventLike {
  error: string;
}

interface SpeechRecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((event: SpeechRecognitionResultEventLike) => void) | null;
  onerror: ((event: SpeechRecognitionErrorEventLike) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
}

type SpeechRecognitionConstructor = new () => SpeechRecognitionLike;

const TOOL_PROMPTS = CHAT_TOOL_INTENTS;
const MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024;
const UNAVAILABLE_TOOLS = [
  {
    label: "选择已启用插件",
    icon: "plugins",
    reason: "插件中心将在后续版本开放，目前没有可选择的已启用插件。",
  },
] as const;

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
}: ComposerProps) {
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState<ComposerAttachment[]>([]);
  const [dictating, setDictating] = useState(false);
  const [dictationError, setDictationError] = useState("");
  const [toolNotice, setToolNotice] = useState("");
  const [projectPickerOpen, setProjectPickerOpen] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const controllersRef = useRef(new Map<string, AbortController>());
  const preparedConversationRef = useRef<string | undefined>(conversationId);
  const attachmentsRef = useRef<ComposerAttachment[]>([]);
  attachmentsRef.current = attachments;

  const uploadedAttachments = attachments.filter(
    (item) => item.status === "uploaded" && item.objectId
  );
  const hasUploading = attachments.some((item) => item.status === "uploading");
  const canSend = text.trim().length > 0 || uploadedAttachments.length > 0;

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
    if (!canSend || generating) return;
    if (hasUploading) {
      setToolNotice("附件仍在上传，请等待完成或先取消上传。");
      return;
    }
    const ids = uploadedAttachments.flatMap((item) => (item.objectId ? [item.objectId] : []));
    try {
      const accepted = await onSend(
        text.trim() || "（仅附件）",
        ids,
        conversationId ?? preparedConversationRef.current
      );
      if (accepted === false) return;
      setText("");
      setAttachments((current) =>
        current.filter((item) => item.status !== "uploaded" || !item.objectId)
      );
      setToolNotice("");
      recognitionRef.current?.stop();
      recognitionRef.current = null;
      setDictating(false);
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

  const stopDictation = () => {
    recognitionRef.current?.stop();
    recognitionRef.current = null;
    setDictating(false);
  };

  const toggleDictation = () => {
    if (dictating) {
      stopDictation();
      return;
    }
    const speechWindow = window as typeof window & {
      SpeechRecognition?: SpeechRecognitionConstructor;
      webkitSpeechRecognition?: SpeechRecognitionConstructor;
    };
    const Recognition = speechWindow.SpeechRecognition ?? speechWindow.webkitSpeechRecognition;
    if (!Recognition) {
      setDictationError("当前浏览器不支持语音听写，请改用键盘输入。");
      return;
    }
    const recognition = new Recognition();
    recognition.lang = "zh-CN";
    recognition.continuous = true;
    recognition.interimResults = false;
    recognition.onresult = (event) => {
      const last = event.results[event.results.length - 1];
      const transcript = last?.[0]?.transcript?.trim();
      if (!transcript) return;
      setText((current) => `${current}${current ? " " : ""}${transcript}`);
      requestAnimationFrame(autoGrow);
    };
    recognition.onerror = (event) => {
      setDictationError(`听写失败（${event.error}），请重试或改用键盘输入。`);
      recognitionRef.current = null;
      setDictating(false);
    };
    recognition.onend = () => {
      recognitionRef.current = null;
      setDictating(false);
    };
    try {
      setDictationError("");
      recognition.start();
      recognitionRef.current = recognition;
      setDictating(true);
    } catch {
      setDictationError("无法启动语音听写，请检查麦克风权限后重试。");
    }
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
      recognitionRef.current?.stop();
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
                  {attachmentStatus(item)}
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
            {
              label: "上传文件/图片",
              icon: "uploadFile",
              onSelect: () => fileInputRef.current?.click(),
            },
            ...TOOL_PROMPTS.map((tool) => ({
              label: tool.label,
              icon: tool.icon,
              onSelect: () => insertToolPrefix(tool.prefix),
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
            ...UNAVAILABLE_TOOLS.map((tool) => ({
              label: tool.label,
              icon: tool.icon,
              onSelect: () => setToolNotice(tool.reason),
            })),
          ]}
        />

        <span style={{ flex: 1 }} />
        {dictating && <span role="status" style={{ fontSize: "var(--text-sm)", color: "var(--color-accent-primary)" }}>听写中，请开始说话…</span>}
        {dictationError && <span role="alert" style={{ fontSize: "var(--text-sm)", color: "var(--color-status-error)" }}>{dictationError}</span>}
        <button
          type="button"
          aria-label={dictating ? "停止听写" : "开始听写"}
          aria-pressed={dictating}
          onClick={toggleDictation}
          style={{ ...iconButtonStyle, color: dictating ? "var(--color-accent-primary)" : "var(--color-text-secondary)" }}
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
