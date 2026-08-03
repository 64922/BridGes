"use client";

import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/design-system/Button";
import { Icon } from "@/components/design-system/Icon";
import { CHAT_TOOL_INTENTS } from "@/lib/chat-tools";
import { Menu } from "./Menu";

interface ComposerProps {
  onSend: (text: string) => void;
  generating?: boolean;
  onStop?: () => void;
  /** 外部预填请求（建议卡等）：nonce 变化时把 text 作为结构化意图填入并聚焦 */
  prefill?: { text: string; nonce: number } | null;
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

/** 「+」菜单中的功能入口：与空白态建议卡共享的结构化意图（lib/chat-tools） */
const TOOL_PROMPTS = CHAT_TOOL_INTENTS;

/**
 * 「+」菜单中尚未由后续 Issue 实现的入口：只显示明确不可用原因，
 * 不产生假项目、假插件或任何伪造结果（Issue 13 验收约束）。
 */
const UNAVAILABLE_TOOLS = [
  {
    label: "选择学习项目",
    icon: "learningProject",
    reason: "学习项目功能将在后续版本开放，现在可以直接在消息中描述你的学习目标。",
  },
  {
    label: "选择已启用插件",
    icon: "plugins",
    reason: "插件中心将在后续版本开放，目前没有可选择的已启用插件。",
  },
] as const;

/**
 * 对话输入区。
 *
 * 键位约定（与行为基线一致）：Enter 发送、Shift+Enter 换行；
 * 空输入时发送按钮禁用并说明原因；生成中发送键变为「停止」。
 * 左侧「+」按钮弹出功能菜单：上传文件/图片（真实的本地文件选择器，
 * 模板仅保存文件名，不读取文件内容）、论文搜索、文章人味化、生涯规划助手；
 * 听写按钮位于输入区右侧、发送按钮左边。
 */
export function Composer({ onSend, generating = false, onStop, prefill = null }: ComposerProps) {
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState<string[]>([]);
  const [dictating, setDictating] = useState(false);
  const [dictationError, setDictationError] = useState("");
  const [toolNotice, setToolNotice] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);

  const canSend = text.trim().length > 0 || attachments.length > 0;

  const autoGrow = () => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 12 * 16)}px`;
  };

  const send = () => {
    if (!canSend || generating) return;
    onSend(text.trim() || "（仅附件）");
    setText("");
    setAttachments([]);
    setToolNotice("");
    recognitionRef.current?.stop();
    recognitionRef.current = null;
    setDictating(false);
    requestAnimationFrame(autoGrow);
  };

  const addSelectedFiles = (files: FileList | null) => {
    if (!files) return;
    const names = Array.from(files, (file) => file.name);
    setAttachments((current) => Array.from(new Set([...current, ...names])));
  };

  const insertToolPrefix = (prefix: string) => {
    setText((current) => (current.startsWith(prefix) ? current : `${prefix}${current}`));
    setToolNotice("");
    requestAnimationFrame(() => {
      const el = textareaRef.current;
      if (!el) return;
      el.focus();
      el.setSelectionRange(el.value.length, el.value.length);
      autoGrow();
    });
  };

  // 外部预填（建议卡）：与「+」菜单工具入口同一预填路径，走正常消息流
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

  useEffect(() => () => recognitionRef.current?.stop(), []);

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
          {attachments.map((name) => (
            <li
              key={name}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-2)",
                maxWidth: "100%",
                padding: "var(--space-1) var(--space-2)",
                borderRadius: "var(--radius-md)",
                border: "1px solid var(--color-border)",
                backgroundColor: "var(--color-bg-secondary)",
                fontSize: "var(--text-sm)",
                color: "var(--color-text-secondary)",
              }}
            >
              <Icon name="uploadFile" size={16} aria-hidden />
              <span
                style={{
                  maxWidth: "22rem",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
                title={name}
              >
                {name}
              </span>
              <button
                type="button"
                aria-label={`移除附件 ${name}`}
                onClick={() => setAttachments((list) => list.filter((item) => item !== name))}
                style={{
                  display: "inline-flex",
                  border: "none",
                  background: "none",
                  color: "var(--color-text-tertiary)",
                  cursor: "pointer",
                  padding: "var(--space-1)",
                }}
              >
                <Icon name="close" size={14} aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      )}

      <label htmlFor="composer-input" className="sc-visually-hidden">
        输入消息
      </label>
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
            send();
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

      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-1)" }}>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          tabIndex={-1}
          aria-hidden="true"
          data-testid="composer-file-input"
          onChange={(event) => {
            addSelectedFiles(event.target.files);
            event.target.value = "";
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
            ...UNAVAILABLE_TOOLS.map((tool) => ({
              label: tool.label,
              icon: tool.icon,
              onSelect: () => setToolNotice(tool.reason),
            })),
          ]}
        />

        <span style={{ flex: 1 }} />

        {dictating && (
          <span role="status" style={{ fontSize: "var(--text-sm)", color: "var(--color-accent-primary)" }}>
            听写中，请开始说话…
          </span>
        )}
        {dictationError && (
          <span role="alert" style={{ fontSize: "var(--text-sm)", color: "var(--color-status-error)" }}>
            {dictationError}
          </span>
        )}
        <button
          type="button"
          aria-label={dictating ? "停止听写" : "开始听写"}
          aria-pressed={dictating}
          onClick={toggleDictation}
          style={{
            ...iconButtonStyle,
            color: dictating ? "var(--color-accent-primary)" : "var(--color-text-secondary)",
          }}
        >
          <Icon name="dictation" size={20} aria-hidden />
        </button>

        {generating ? (
          <Button variant="secondary" size="sm" onClick={onStop} aria-label="停止生成">
            <Icon name="close" size={16} aria-hidden />
            停止
          </Button>
        ) : (
          <Button
            variant="primary"
            size="sm"
            onClick={send}
            disabled={!canSend}
            aria-label="发送消息"
            title={canSend ? "发送" : "输入内容后才能发送"}
          >
            <Icon name="send" size={16} aria-hidden />
            发送
          </Button>
        )}
      </div>
      {toolNotice && (
        <p
          role="status"
          data-testid="tool-unavailable-notice"
          style={{
            margin: 0,
            fontSize: "var(--text-sm)",
            color: "var(--color-text-secondary)",
          }}
        >
          {toolNotice}
        </p>
      )}
    </div>
  );
}
