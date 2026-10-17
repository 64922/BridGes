"use client";

import { useEffect, useRef, useState } from "react";

import { Icon } from "@/components/design-system/Icon";
import { CHAT_MODULES, type ChatModuleSelectionId } from "@/lib/chat-modules";
import type { ChatMode } from "./ModeToggle";
import type { CapabilityAvailability } from "./chat/capability-availability";
import { ComposerAttachments } from "./chat/composer/ComposerAttachments";
import { ComposerToolbar } from "./chat/composer/ComposerToolbar";
import { useComposerAttachments } from "./chat/composer/useComposerAttachments";
import {
  useComposerText,
  type ComposerPrefill,
} from "./chat/composer/useComposerText";
import { useDictation } from "./chat/composer/useDictation";
import styles from "./chat/chat.module.css";

interface ComposerProps {
  /**
   * 发送回调：``attachmentIds`` 为本轮附件草稿的 object_id（按页序排列），
   * 空数组表示纯文字消息。纯附件（无文字）也允许发送。
   */
  onSend: (text: string, attachmentIds: string[]) => Promise<boolean> | boolean | void;
  /** 已存在的真实对话；用于发送消息与听写。 */
  conversationId?: string;
  generating?: boolean;
  onStop?: () => void;
  /** 外部预填请求（建议卡等）：nonce 变化时把 text 作为结构化意图填入并聚焦 */
  prefill?: ComposerPrefill | null;
  /** Issue 30：ASR 听写能力可用性（账户级探测快照；不可用时禁用入口并说明原因） */
  asr?: CapabilityAvailability;
  /** 新聊天首页使用原子首轮，并在会话创建前禁用听写入口。 */
  variant?: "conversation" | "new-chat";
  /**
   * V2 Issue 11：当前显式模块选择（受控）。``null`` 为普通聊天；选择后
   * 输入区显示可移除 chip，发送时随该条消息保存模块 ID。
   */
  moduleId?: ChatModuleSelectionId | null;
  /** 模块选择变化（选中菜单项传模块 ID，移除 chip 传 null）。 */
  onModuleChange?: (moduleId: ChatModuleSelectionId | null) => void;
  mode?: ChatMode;
  /**
   * 首轮前可选的模式入口（仅新对话传入）。传入时模式选择显示在输入区内；
   * 未传入（已有会话）时不在输入区提供切换。
   */
  onModeChange?: (mode: ChatMode) => void;
  /** 首轮创建中禁用模式切换，避免请求身份与界面选择不一致。 */
  modeDisabled?: boolean;
}

/**
 * 对话输入区组合层。
 *
 * 文本行为、附件草稿、听写状态机与工具栏各自持有唯一状态；本组件只
 * 决定“本轮请求是什么”：正文、按页序的附件 ID 与当前模式下的模块选择。
 * 发送成功才清空本地草稿，失败保留文字与附件等待重发。
 */
export function Composer({
  onSend,
  conversationId,
  generating = false,
  onStop,
  prefill = null,
  asr = { available: true },
  variant = "conversation",
  moduleId = null,
  onModuleChange,
  mode = "companion",
  onModeChange,
  modeDisabled = false,
}: ComposerProps) {
  const attachments = useComposerAttachments();
  const [submitting, setSubmitting] = useState(false);
  const submittingRef = useRef(false);
  const sendRef = useRef<() => void>(() => {});
  const onModuleChangeRef = useRef(onModuleChange);
  onModuleChangeRef.current = onModuleChange;

  const textInput = useComposerText({
    prefill,
    onSubmit: () => sendRef.current(),
  });

  const dictation = useDictation({
    conversationId,
    variant,
    asr,
    onTranscribed: (transcript) => textInput.replaceText(transcript, true),
    onRetake: () => textInput.resetText(),
  });

  // 有文字或已有附件即可发送；上传未完成的批次禁止提前发送。
  // Issue 10：学习模式接受同一份附件合同（照片与文件），发送按钮不再
  // 因文件附件被禁用；读取范围与解析状态由服务端与消息附件卡如实呈现。
  const canSend =
    (textInput.text.trim().length > 0 || attachments.drafts.length > 0) &&
    dictation.phase === "idle" &&
    attachments.uploadingCount === 0 &&
    !submitting;

  const send = async () => {
    if (!canSend || generating || submittingRef.current) return;
    submittingRef.current = true;
    setSubmitting(true);
    const attachmentIds = attachments.drafts.map((draft) => draft.object_id);
    try {
      const accepted = await onSend(textInput.text.trim(), attachmentIds);
      if (accepted === false) return;
      // 发送成功：文字与草稿一并清空（草稿已在服务端随消息绑定）。
      textInput.resetText();
      attachments.clearAfterSend(attachmentIds);
      dictation.cancelRecording();
    } catch (error) {
      // 发送错误由宿主页面统一呈现，文字与附件保留等待重试。
      void error;
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  };

  sendRef.current = () => {
    void send();
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

  // 日常模块只属于陪伴模式：切到学习模式时清掉宿主仍保留的无效选择。
  useEffect(() => {
    if (mode === "study" && moduleId !== null) {
      onModuleChangeRef.current?.(null);
    }
  }, [mode, moduleId]);

  // V2 Issue 11：显式模块 chip（菜单选择后在输入区可见、可移除）。
  // 移除只取消本轮之后的模块选择，不改写已发送消息的逐条模块标识，
  // 也不触碰已输入文字。
  const selectedModule =
    mode === "companion" ? CHAT_MODULES.find((item) => item.id === moduleId) ?? null : null;
  const clearModule = () => {
    onModuleChange?.(null);
    textInput.textareaRef.current?.focus();
  };

  return (
    <div
      data-testid="composer"
      onDragOver={(event) => {
        if (!event.dataTransfer.types.includes("Files")) return;
        event.preventDefault();
        attachments.setDragOver(true);
      }}
      onDragLeave={() => attachments.setDragOver(false)}
      onDrop={(event) => {
        const files = Array.from(event.dataTransfer.files ?? []);
        if (files.length === 0) return;
        event.preventDefault();
        attachments.setDragOver(false);
        attachments.addFiles(files);
      }}
      style={{
        border: "1px solid var(--color-border-strong)",
        outline: attachments.dragOver ? "2px dashed var(--color-primary)" : undefined,
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
      <div style={{ display: "flex", alignItems: "flex-start", gap: "var(--space-2)" }}>
        {selectedModule && (
          <span className={styles.composerChip} data-testid="composer-module-chip">
            {selectedModule.label}
            <button
              type="button"
              aria-label={`移除${selectedModule.label}模块`}
              onClick={clearModule}
            >
              <Icon name="close" size={12} aria-hidden />
            </button>
          </span>
        )}
        <textarea
          ref={textInput.textareaRef}
          id="composer-input"
          rows={2}
          value={textInput.text}
          onChange={textInput.onChange}
          onKeyDown={textInput.onKeyDown}
          onCompositionStart={textInput.onCompositionStart}
          onCompositionEnd={textInput.onCompositionEnd}
          onPaste={(event) => {
            const files = Array.from(event.clipboardData?.files ?? []);
            if (files.length === 0) return;
            event.preventDefault();
            attachments.addFiles(files);
          }}
          placeholder={mode === "study"
            ? "哪里不清楚就问，或说说你想学什么"
            : "输入消息，开始日常对话"}
          style={{
            width: "100%",
            flex: 1,
            minWidth: 0,
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
      </div>

      <ComposerAttachments
        drafts={attachments.drafts}
        draftErrors={attachments.draftErrors}
        brokenPreviews={attachments.brokenPreviews}
        onDismissError={attachments.dismissDraftError}
        onMove={attachments.moveDraft}
        onRemove={attachments.removeDraft}
        onPreviewError={attachments.markPreviewBroken}
      />

      <ComposerToolbar
        mode={mode}
        variant={variant}
        conversationId={conversationId}
        asr={asr}
        dictation={dictation}
        uploadingCount={attachments.uploadingCount}
        generating={generating}
        canSend={canSend}
        fileInputRef={attachments.fileInputRef}
        onFileInputChange={attachments.onFileInputChange}
        onModuleSelect={onModuleChange}
        onModeSelect={onModeChange}
        modeDisabled={modeDisabled}
        onSend={() => void send()}
        onStop={onStop}
      />
    </div>
  );
}
