"use client";

import type { RefObject } from "react";

import { Button } from "@/components/design-system/Button";
import { Icon } from "@/components/design-system/Icon";
import {
  CHAT_ATTACHMENT_ACCEPT,
} from "@/lib/chat-attachments";
import { CHAT_MODULES, type ChatModuleSelectionId } from "@/lib/chat-modules";
import { ModeToggle, type ChatMode } from "../../ModeToggle";
import type { CapabilityAvailability } from "../capability-availability";
import { Menu, type MenuItem } from "../../Menu";
import styles from "../chat.module.css";
import { iconButtonStyle } from "./composer-styles";
import type { useDictation } from "./useDictation";

function formatDictationTime(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
}

type DictationState = ReturnType<typeof useDictation>;

interface ComposerToolbarProps {
  mode: ChatMode;
  variant: "conversation" | "new-chat";
  conversationId?: string;
  asr: CapabilityAvailability;
  dictation: DictationState;
  uploadingCount: number;
  generating: boolean;
  canSend: boolean;
  fileInputRef: RefObject<HTMLInputElement>;
  onFileInputChange: () => void;
  onModuleSelect?: (moduleId: ChatModuleSelectionId) => void;
  onModeSelect?: (mode: ChatMode) => void;
  modeDisabled?: boolean;
  onSend: () => void;
  onStop?: () => void;
}

/**
 * 输入区工具栏：功能选择、附件入口、听写状态与发送/停止。
 *
 * 这里只呈现状态并调用所有者提供的动作；草稿、文本与听写状态仍由各自
 * 模块持有，工具栏重排不会改变发送请求的内容。
 */
export function ComposerToolbar({
  mode,
  variant,
  conversationId,
  asr,
  dictation,
  uploadingCount,
  generating,
  canSend,
  fileInputRef,
  onFileInputChange,
  onModuleSelect,
  onModeSelect,
  modeDisabled = false,
  onSend,
  onStop,
}: ComposerToolbarProps) {
  const dictationDisabled =
    !asr.available || (variant === "new-chat" && !conversationId);
  const menuItems: MenuItem[] = [
    {
      label: "添加照片和文件",
      description:
        "支持 PDF、DOCX、TXT、Markdown 与图片，单个 10 MB 内，也可拖入或粘贴",
      icon: "imagePicture",
      // 随后打开系统文件选择框：焦点先回到触发按钮，关闭对话框
      // 时归还目标不会是已卸载的菜单项。
      returnFocus: false,
      onSelect: () => fileInputRef.current?.click(),
    },
    ...(mode === "companion" ? CHAT_MODULES : []).map((module) => ({
      label: module.label,
      description: module.description,
      icon: module.icon,
      onSelect: () => onModuleSelect?.(module.id),
    })),
  ];

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        flexWrap: "wrap",
        gap: "var(--space-1)",
      }}
    >
      {onModeSelect && (
        <div className={styles.composerModeSelect}>
          <ModeToggle
            compact
            value={mode}
            onChange={onModeSelect}
            disabled={modeDisabled}
          />
        </div>
      )}
      <span style={{ flex: 1 }} />

      {variant === "new-chat" && !conversationId && (
        <span className={styles.composerDictationText} role="note">
          发送首条消息后可使用听写
        </span>
      )}

      {dictation.phase === "recording" && (
        <span className={styles.composerDictationRow} role="status" aria-live="polite">
          <span className={styles.composerDictationDot} aria-hidden="true" />
          <span className={styles.composerDictationTimer}>
            {formatDictationTime(dictation.seconds)}
          </span>
          <button
            type="button"
            className={styles.composerDictationAction}
            aria-label="停止录音并转写"
            onClick={dictation.stopRecordingAndTranscribe}
          >
            <Icon name="stopSquare" size={16} aria-hidden />
            停止
          </button>
          <button
            type="button"
            className={styles.composerDictationAction}
            aria-label="取消录音"
            onClick={dictation.cancelRecording}
          >
            <Icon name="close" size={16} aria-hidden />
            取消
          </button>
        </span>
      )}

      {dictation.phase === "transcribing" && (
        <span className={styles.composerDictationRow} role="status" aria-live="polite">
          <span className={styles.readAloudSpinner} aria-hidden="true" />
          <span className={styles.composerDictationText}>正在转写…</span>
          <button
            type="button"
            className={styles.composerDictationAction}
            aria-label="取消转写"
            onClick={dictation.cancelRecording}
          >
            <Icon name="close" size={16} aria-hidden />
            取消
          </button>
        </span>
      )}

      {dictation.phase === "error" && (
        <span className={styles.composerDictationRow} role="alert">
          <Icon name="alert" size={16} aria-hidden />
          <span className={styles.composerDictationError}>{dictation.error}</span>
          {dictation.canRetryTranscription && (
            <button
              type="button"
              className={styles.composerDictationAction}
              aria-label="重试转写"
              onClick={() => void dictation.retryTranscription()}
            >
              <Icon name="retry" size={16} aria-hidden />
              重试
            </button>
          )}
          <button
            type="button"
            className={styles.composerDictationAction}
            aria-label="重新录制"
            onClick={() => void dictation.startRecording()}
          >
            <Icon name="dictation" size={16} aria-hidden />
            重新录制
          </button>
          <button
            type="button"
            className={styles.composerDictationAction}
            aria-label="取消听写"
            onClick={dictation.cancelRecording}
          >
            <Icon name="close" size={16} aria-hidden />
            取消
          </button>
        </span>
      )}

      {dictation.phase === "idle" && !asr.available && asr.reason && (
        <span className={styles.composerDictationRow} role="note">
          <Icon name="alert" size={16} aria-hidden />
          <span className={styles.composerDictationText}>{asr.reason}</span>
        </span>
      )}

      {dictation.phase === "idle" && dictation.transcribed && (
        <span className={styles.composerDictationRow} role="status">
          <span className={styles.composerDictationText}>已转写，可编辑后发送</span>
          <button
            type="button"
            className={styles.composerDictationAction}
            aria-label="重新录制"
            onClick={() => void dictation.startRecording()}
          >
            <Icon name="dictation" size={16} aria-hidden />
            重新录制
          </button>
        </span>
      )}

      <input
        ref={fileInputRef}
        data-testid="composer-file-input"
        type="file"
        accept={CHAT_ATTACHMENT_ACCEPT}
        multiple
        onChange={onFileInputChange}
        style={{ display: "none" }}
      />
      {/* V2 Issue 11：`+` 上拉菜单（添加照片和文件 + 显式模块选择）。
          菜单键位与焦点归还由无障碍 Menu 组件统一承担：方向键/Home/End
          移动、Enter 激活、Tab/点击外部关闭、Esc 关闭并把焦点还给 `+`；
          选择模块只切换到「显式派发」，不在此处发起任何检索。 */}
      <Menu
        ariaLabel="添加功能或文件"
        openUp
        trigger={<Icon name="plus" size={20} aria-hidden />}
        triggerStyle={{
          width: "auto",
          minWidth: "var(--target-size)",
          minHeight: "var(--target-size)",
          padding: "0 var(--space-2)",
          justifyContent: "center",
          border: "none",
          backgroundColor: "transparent",
          color: "var(--color-text-secondary)",
        }}
        items={menuItems}
      />
      {uploadingCount > 0 && (
        <span role="status" className={styles.composerDictationText}>
          正在添加附件…
        </span>
      )}
      <button
        type="button"
        aria-label="开始听写"
        aria-pressed={dictation.phase === "recording"}
        disabled={dictationDisabled}
        title={
          variant === "new-chat" && !conversationId
            ? "发送首条消息后可使用听写。"
            : asr.available
              ? undefined
              : asr.reason
        }
        onClick={() => void dictation.startRecording()}
        style={{
          ...iconButtonStyle,
          color:
            dictation.phase === "recording"
              ? "var(--color-status-error)"
              : "var(--color-text-secondary)",
          opacity: dictationDisabled ? 0.45 : 1,
          cursor: dictationDisabled ? "not-allowed" : "pointer",
        }}
      >
        <Icon name="dictation" size={20} aria-hidden />
      </button>
      {generating && onStop ? (
        <Button variant="secondary" size="sm" onClick={onStop} aria-label="停止生成">
          <Icon name="close" size={16} aria-hidden />停止
        </Button>
      ) : (
        <Button
          variant="primary"
          size="sm"
          onClick={onSend}
          disabled={!canSend || generating}
          aria-label={generating ? "正在发送" : "发送消息"}
          title={
            generating
              ? "正在创建会话"
              : canSend
                ? "发送"
                : "输入内容或添加附件后才能发送"
          }
        >
          <Icon name="send" size={16} aria-hidden />
          {generating ? "正在发送" : "发送"}
        </Button>
      )}
    </div>
  );
}
