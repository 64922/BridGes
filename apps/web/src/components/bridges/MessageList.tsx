"use client";

import { useEffect, useRef, useState } from "react";

import { Icon, type IconName } from "@/components/design-system/Icon";
import { copyTextToClipboard } from "@/lib/clipboard";
import type {
  ChatAttachmentProjection,
  ChatMode,
  ChatModeEventProjection,
  RetrievalRoundProjection,
} from "@/lib/api";
import { AttachmentIngestionInfo } from "./AttachmentIngestion";
import { BrandLogo } from "./BrandLogo";
import { RetrievalCard } from "./RetrievalCard";

/** 可见的模式切换事件渲染项（Issue 14）：随消息流按时间排序插入。 */
export interface ThreadModeEvent extends ChatModeEventProjection {
  kind: "mode-event";
}

export const MODE_EVENT_LABEL: Record<ChatMode, string> = {
  companion: "日常陪伴",
  study: "学习模式",
};

export interface ChatThinking {
  /** 可公开的处理步骤（生成中会增长） */
  steps: string[];
  /** 回答采用的证据/来源说明（可空） */
  evidence: string[];
  /** 工具调用进度说明（可空） */
  tools: string[];
  /** 质量检查结论（完成/失败/停止的中文状态，可空） */
  quality: string[];
  /** 生成耗时（秒，来自真实生命周期 duration_ms）；流式中为空 */
  seconds: number | null;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  /** 消息的纯文本（用于复制与朗读） */
  plainText: string;
  /** 富内容（段落、代码块、公式、表格等），由页面组装 */
  content: React.ReactNode;
  thinking?: ChatThinking;
  status?: "done" | "streaming" | "error";
  errorText?: string;
  attachments?: ChatAttachmentProjection[];
  /** Issue 20：本条助手消息绑定的分层检索轮次（含引用），无轮次为 null */
  retrieval?: RetrievalRoundProjection | null;
  /** Issue 11：该轮用户消息之下的历史助手尝试（重试保留审计，不静默改写） */
  previousAttempts?: {
    attemptNumber: number;
    status: "done" | "streaming" | "error" | "stopped";
    errorMessage?: string | null;
  }[];
}

interface MessageListProps {
  messages: (ChatMessage | ThreadModeEvent)[];
  onRetry?: (id: string) => void;
  onDownloadAttachment?: (attachment: ChatAttachmentProjection) => void;
  onDeleteAttachment?: (messageId: string, attachment: ChatAttachmentProjection) => void;
  /** 附件摄取重试（Issue 17）：页面处理器调用重试 API 并刷新对话 */
  onRetryIngestion?: (objectId: string) => Promise<void>;
  /** 附件所属对话（摄取详情接口的上下文） */
  conversationId?: string;
}

const actionButtonStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  minWidth: "var(--target-size)",
  minHeight: "var(--target-size)",
  border: "none",
  borderRadius: "var(--radius-md)",
  backgroundColor: "transparent",
  color: "var(--color-text-tertiary)",
  cursor: "pointer",
};

function MessageAction({
  icon,
  label,
  pressed,
  onClick,
}: {
  icon: IconName;
  label: string;
  pressed?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      aria-pressed={pressed}
      onClick={onClick}
      style={{
        ...actionButtonStyle,
        color: pressed ? "var(--color-accent-primary)" : "var(--color-text-tertiary)",
      }}
    >
      <Icon name={icon} size={18} aria-hidden />
    </button>
  );
}

function formatAttachmentSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function MessageAttachments({
  messageId,
  attachments,
  onDownload,
  onDelete,
  onRetryIngestion,
  conversationId,
}: {
  messageId: string;
  attachments: ChatAttachmentProjection[];
  onDownload?: (attachment: ChatAttachmentProjection) => void;
  onDelete?: (messageId: string, attachment: ChatAttachmentProjection) => void;
  onRetryIngestion?: (objectId: string) => Promise<void>;
  conversationId?: string;
}) {
  if (attachments.length === 0) return null;
  return (
    <ul
      role="list"
      aria-label="消息附件"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-2)",
        margin: "var(--space-3) 0 0",
        padding: 0,
        listStyle: "none",
      }}
    >
      {attachments.map((attachment) => (
        <li
          key={attachment.object_id}
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-2)",
            padding: "var(--space-2)",
            border: "1px solid var(--color-accent-warm, #d6a64f)",
            borderRadius: "var(--radius-md)",
            backgroundColor: "var(--color-surface)",
          }}
        >
          <Icon name="uploadFile" size={18} aria-hidden />
          <span style={{ minWidth: 0, flex: 1 }}>
            <span
              style={{
                display: "block",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
                fontWeight: 600,
              }}
              title={attachment.original_filename}
            >
              {attachment.original_filename}
            </span>
            <span
              role="status"
              style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}
            >
              {attachment.media_type} · {formatAttachmentSize(attachment.content_length)} ·
              {attachment.status === "bound" ? " 已关联消息" : " 待发送"}
            </span>
            {conversationId && (
              <AttachmentIngestionInfo
                conversationId={conversationId}
                attachment={attachment}
                onRetryIngestion={onRetryIngestion}
              />
            )}
          </span>
          {onDownload && (
            <button
              type="button"
              onClick={() => onDownload(attachment)}
              aria-label={`下载附件 ${attachment.original_filename}`}
              title="下载附件"
              style={actionButtonStyle}
            >
              <Icon name="download" size={18} aria-hidden />
            </button>
          )}
          {onDelete && (
            <button
              type="button"
              onClick={() => {
                if (window.confirm(`确认删除附件“${attachment.original_filename}”吗？`)) {
                  onDelete(messageId, attachment);
                }
              }}
              aria-label={`删除附件 ${attachment.original_filename}`}
              title="删除附件"
              style={actionButtonStyle}
            >
              <Icon name="trash" size={18} aria-hidden />
            </button>
          )}
        </li>
      ))}
    </ul>
  );
}

function AssistantActions({ message, onRetry }: { message: ChatMessage; onRetry?: (id: string) => void }) {
  const [copyStatus, setCopyStatus] = useState<"idle" | "success" | "error">("idle");
  const [feedback, setFeedback] = useState<"good" | "bad" | null>(null);
  const [reading, setReading] = useState(false);
  const [speechError, setSpeechError] = useState("");
  const utteranceRef = useRef<SpeechSynthesisUtterance | null>(null);

  const copy = async () => {
    const copied = await copyTextToClipboard(message.plainText);
    setCopyStatus(copied ? "success" : "error");
    window.setTimeout(() => setCopyStatus("idle"), 2000);
  };

  const toggleReading = () => {
    if (reading) {
      window.speechSynthesis.cancel();
      utteranceRef.current = null;
      setReading(false);
      return;
    }
    if (!("speechSynthesis" in window) || !("SpeechSynthesisUtterance" in window)) {
      setSpeechError("当前浏览器不支持朗读。");
      return;
    }

    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(message.plainText);
    utterance.lang = "zh-CN";
    utterance.onend = () => {
      utteranceRef.current = null;
      setReading(false);
    };
    utterance.onerror = () => {
      utteranceRef.current = null;
      setReading(false);
      setSpeechError("朗读失败，请检查系统语音设置后重试。");
    };
    setSpeechError("");
    utteranceRef.current = utterance;
    setReading(true);
    window.speechSynthesis.speak(utterance);
  };

  useEffect(
    () => () => {
      if (utteranceRef.current) window.speechSynthesis.cancel();
    },
    [],
  );

  return (
    <div
      role="toolbar"
      aria-label="消息操作"
      style={{ display: "flex", alignItems: "center", gap: "var(--space-1)", marginTop: "var(--space-2)" }}
    >
      <MessageAction icon="copy" label={copyStatus === "success" ? "已复制" : "复制"} onClick={copy} />
      {copyStatus !== "idle" && (
        <span
          role={copyStatus === "error" ? "alert" : "status"}
          style={{
            fontSize: "var(--text-xs)",
            color:
              copyStatus === "error"
                ? "var(--color-status-error)"
                : "var(--color-status-success)",
          }}
        >
          {copyStatus === "success" ? "已复制" : "复制失败，请检查浏览器权限"}
        </span>
      )}
      <MessageAction icon="retry" label="重试" onClick={() => onRetry?.(message.id)} />
      <MessageAction
        icon="feedbackGood"
        label="回答有帮助"
        pressed={feedback === "good"}
        onClick={() => setFeedback((value) => (value === "good" ? null : "good"))}
      />
      <MessageAction
        icon="feedbackBad"
        label="回答需改进"
        pressed={feedback === "bad"}
        onClick={() => setFeedback((value) => (value === "bad" ? null : "bad"))}
      />
      <MessageAction
        icon="readAloud"
        label={reading ? "停止朗读" : "朗读"}
        pressed={reading}
        onClick={toggleReading}
      />
      {reading && (
        <span role="status" style={{ fontSize: "var(--text-xs)", color: "var(--color-accent-primary)" }}>
          朗读中…
        </span>
      )}
      {feedback && (
        <span role="status" style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
          已在当前页面标记为{feedback === "good" ? "有帮助" : "需改进"}
        </span>
      )}
      {speechError && (
        <span role="alert" style={{ fontSize: "var(--text-xs)", color: "var(--color-status-error)" }}>
          {speechError}
        </span>
      )}
    </div>
  );
}

/**
 * 可折叠思考摘要（Issue 14）。
 *
 * 生成开始时自动展开并展示进行中的步骤；完成后折叠为精确格式
 * 「已思考（用时 X 秒）」，点击或键盘激活（summary 原生 Enter/Space）
 * 可再次展开。内容只包含可公开的步骤、采用的证据、工具调用进度与
 * 质量检查结论，绝不展示原始思维链。耗时来自真实生成生命周期
 * （duration_ms），不使用硬编码数字。受控 details：显式 role=button
 * 与 aria-expanded，保证 ARIA 状态与折叠规则同步。
 */
function ThinkingSummary({
  thinking,
  streaming,
}: {
  thinking: ChatThinking;
  streaming: boolean;
}) {
  const [open, setOpen] = useState(streaming);

  // 生成完成/失败/停止后自动折叠为「已思考（用时 X 秒）」；
  // 用户手动展开过的消息在刷新重建后保持默认折叠规则。
  useEffect(() => {
    if (!streaming) setOpen(false);
  }, [streaming]);

  const collapsedLabel =
    thinking.seconds !== null
      ? `已思考（用时 ${thinking.seconds} 秒）`
      : streaming
        ? "正在思考…"
        : "已思考";

  const sections: { label: string; items: string[]; numbered: boolean }[] = [];
  if (thinking.steps.length > 0) {
    sections.push({ label: "处理步骤", items: thinking.steps, numbered: true });
  }
  if (thinking.evidence.length > 0) {
    sections.push({ label: "采用的证据", items: thinking.evidence, numbered: false });
  }
  if (thinking.tools.length > 0) {
    sections.push({ label: "工具进度", items: thinking.tools, numbered: false });
  }
  if (thinking.quality.length > 0) {
    sections.push({ label: "质量检查", items: thinking.quality, numbered: false });
  }

  return (
    <details
      open={open}
      onToggle={(event) => setOpen((event.target as HTMLDetailsElement).open)}
      data-testid="thinking-summary"
      style={{
        marginBottom: "var(--space-3)",
        border: "1px solid var(--color-border)",
        borderRadius: "var(--radius-md)",
        padding: "var(--space-2) var(--space-3)",
        backgroundColor: "var(--color-bg-secondary)",
        transition: "border-color 150ms ease",
      }}
    >
      <summary
        role="button"
        aria-expanded={open}
        style={{
          cursor: "pointer",
          fontSize: "var(--text-sm)",
          color: streaming ? "var(--color-accent-primary)" : "var(--color-text-secondary)",
          display: "flex",
          alignItems: "center",
          gap: "var(--space-2)",
          minHeight: "var(--target-size)",
          padding: "0 var(--space-1)",
        }}
      >
        {streaming && <ThinkingSpinner />}
        {collapsedLabel}
      </summary>
      <div
        style={{
          marginTop: "var(--space-1)",
          display: "flex",
          flexDirection: "column",
          gap: "var(--space-2)",
          fontSize: "var(--text-sm)",
          color: "var(--color-text-secondary)",
        }}
      >
        {sections.map((section) => (
          <div key={section.label}>
            <p
              style={{
                margin: 0,
                marginBottom: "var(--space-1)",
                fontWeight: 600,
                color: "var(--color-text-secondary)",
              }}
            >
              {section.label}
            </p>
            {section.numbered ? (
              <ol
                style={{
                  margin: 0,
                  paddingLeft: "var(--space-5)",
                  listStyle: "decimal",
                  display: "flex",
                  flexDirection: "column",
                  gap: "var(--space-1)",
                }}
              >
                {section.items.map((item, index) => (
                  <li key={`${item}-${index}`}>{item}</li>
                ))}
              </ol>
            ) : (
              <ul
                style={{
                  margin: 0,
                  paddingLeft: "var(--space-5)",
                  listStyle: "disc",
                  display: "flex",
                  flexDirection: "column",
                  gap: "var(--space-1)",
                }}
              >
                {section.items.map((item, index) => (
                  <li key={`${item}-${index}`}>{item}</li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </div>
    </details>
  );
}

function ThinkingSpinner() {
  return (
    <span
      aria-hidden="true"
      style={{
        width: 12,
        height: 12,
        borderRadius: "50%",
        border: "2px solid var(--color-border-strong)",
        borderTopColor: "var(--color-accent-primary)",
        animation: "thinking-spin 0.8s linear infinite",
        flexShrink: 0,
      }}
    />
  );
}

/**
 * 消息流：用户消息气泡靠右，助手消息占整列并带操作行；
 * 思考摘要在生成中自动展开、完成后折叠为「已思考（用时 X 秒）」，可随时展开。
 */
export function MessageList({
  messages,
  onRetry,
  onDownloadAttachment,
  onDeleteAttachment,
  onRetryIngestion,
  conversationId,
}: MessageListProps) {
  return (
    <ol
      role="list"
      aria-label="对话消息"
      style={{ display: "flex", flexDirection: "column", gap: "var(--space-6)" }}
    >
      {messages.map((message) =>
        "kind" in message ? (
          <li
            key={message.event_id}
            data-testid="mode-event"
            style={{
              display: "flex",
              justifyContent: "center",
            }}
          >
            <p
              role="status"
              style={{
                margin: 0,
                fontSize: "var(--text-xs)",
                color: "var(--color-text-tertiary)",
                padding: "var(--space-1) var(--space-3)",
                borderRadius: "var(--radius-full)",
                backgroundColor: "var(--color-bg-secondary)",
              }}
            >
              已切换为{MODE_EVENT_LABEL[message.to_mode]}
            </p>
          </li>
        ) : (
        <li key={message.id}>
          {message.role === "user" ? (
            <div style={{ display: "flex", justifyContent: "flex-end" }}>
              <div
                style={{
                  maxWidth: "85%",
                  padding: "var(--space-3) var(--space-4)",
                  borderRadius: "var(--radius-xl)",
                  backgroundColor: "var(--color-accent-primary-soft)",
                  color: "var(--color-text-primary)",
                  overflowWrap: "break-word",
                }}
              >
                {message.content}
                <MessageAttachments
                  messageId={message.id}
                  attachments={message.attachments ?? []}
                  onDownload={onDownloadAttachment}
                  onDelete={onDeleteAttachment}
                  onRetryIngestion={onRetryIngestion}
                  conversationId={conversationId}
                />
              </div>
            </div>
          ) : (
            <article style={{ display: "flex", gap: "var(--space-3)", minWidth: 0 }}>
              <span style={{ flexShrink: 0, paddingTop: "var(--space-1)" }} aria-hidden="true">
                <BrandLogo variant="icon" width={24} />
              </span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <p
                  style={{
                    fontWeight: 600,
                    fontSize: "var(--text-sm)",
                    color: "var(--color-text-secondary)",
                    marginBottom: "var(--space-1)",
                  }}
                >
                  BridGes
                </p>

                {message.previousAttempts && message.previousAttempts.length > 0 && (
                  <details
                    style={{
                      marginBottom: "var(--space-3)",
                      border: "1px solid var(--color-border)",
                      borderRadius: "var(--radius-md)",
                      padding: "var(--space-2) var(--space-3)",
                      backgroundColor: "var(--color-bg-secondary)",
                      fontSize: "var(--text-sm)",
                    }}
                  >
                    <summary style={{ cursor: "pointer", color: "var(--color-text-secondary)" }}>
                      此问题的前 {message.previousAttempts.length} 次尝试
                    </summary>
                    <ol
                      style={{
                        marginTop: "var(--space-2)",
                        paddingLeft: "var(--space-4)",
                        display: "flex",
                        flexDirection: "column",
                        gap: "var(--space-2)",
                      }}
                    >
                      {message.previousAttempts.map((attempt) => (
                        <li key={attempt.attemptNumber} style={{ color: "var(--color-text-secondary)" }}>
                          {attempt.status === "error" || attempt.status === "stopped" ? (
                            <span style={{ color: "var(--color-status-error)" }}>
                              第 {attempt.attemptNumber} 次尝试（
                              {attempt.status === "stopped" ? "已停止" : "失败"}）
                              {attempt.errorMessage ? `：${attempt.errorMessage}` : ""}
                            </span>
                          ) : (
                            <span>第 {attempt.attemptNumber} 次尝试</span>
                          )}
                        </li>
                      ))}
                    </ol>
                  </details>
                )}

                {/* Issue 20：本地检索轮次与引用（回答内容的证据卡）。
                    streaming 且无轮次时显示检索中加载态；终态无轮次（无
                    检索作用域）不渲染卡片。 */}
                {conversationId && (
                  <RetrievalCard
                    retrieval={message.retrieval ?? null}
                    conversationId={conversationId}
                    messageId={message.id}
                    streaming={message.status === "streaming"}
                    onRetry={() => onRetry?.(message.id)}
                  />
                )}

                {message.thinking && (
                  <ThinkingSummary
                    thinking={message.thinking}
                    streaming={message.status === "streaming"}
                  />
                )}

                {message.status === "error" ? (
                  <div
                    role="alert"
                    style={{
                      display: "flex",
                      alignItems: "flex-start",
                      gap: "var(--space-2)",
                      padding: "var(--space-3)",
                      borderRadius: "var(--radius-md)",
                      border: "1px solid var(--color-status-error)",
                      backgroundColor: "var(--color-status-error-bg)",
                      color: "var(--color-status-error)",
                      overflowWrap: "break-word",
                    }}
                  >
                    <Icon name="alert" size={18} aria-hidden />
                    <span style={{ fontSize: "var(--text-sm)" }}>{message.errorText}</span>
                  </div>
                ) : (
                  message.content
                )}

                {message.status === "streaming" && (
                  <p role="status" style={{ fontSize: "var(--text-sm)", color: "var(--color-text-tertiary)" }}>
                    正在生成回答…
                  </p>
                )}

                <AssistantActions message={message} onRetry={onRetry} />
              </div>
            </article>
          )}
        </li>
        )
      )}
    </ol>
  );
}
