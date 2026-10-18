"use client";

import { useEffect, useRef, useState } from "react";

import { AttachmentIngestionInfo } from "@/components/bridges/AttachmentIngestion";
import { MarkdownBody } from "@/components/bridges/message/MarkdownBody";
import { Icon } from "@/components/design-system/Icon";
import {
  chatAttachmentContentUrl,
  fetchChatAttachmentContent,
  type ChatAttachmentProjection,
} from "@/lib/api";
import {
  CHAT_INGESTION_LABELS,
  attachmentTypeLabel,
  formatAttachmentSize,
  isPhotoAttachment,
} from "@/lib/chat-attachments";

import styles from "./AttachmentViewerPanel.module.css";

const TEXT_PREVIEW_MAX_BYTES = 1024 * 1024;

type PreviewKind = "image" | "pdf" | "markdown" | "text" | "unsupported";

type PanelState =
  | { kind: "loading" }
  | { kind: "ready"; objectUrl?: string; text?: string; truncated?: boolean }
  | { kind: "error"; message: string };

function previewKindOf(mediaType: string): PreviewKind {
  if (isPhotoAttachment(mediaType)) return "image";
  if (mediaType === "application/pdf") return "pdf";
  if (mediaType === "text/markdown") return "markdown";
  if (mediaType === "text/plain") return "text";
  return "unsupported";
}

function AttachmentMeta({ attachment }: { attachment: ChatAttachmentProjection }) {
  const photo = isPhotoAttachment(attachment.media_type);
  const ordinal =
    attachment.ordinal !== null && attachment.ordinal !== undefined
      ? `第 ${attachment.ordinal} ${photo ? "张" : "个"}`
      : null;
  return (
    <p className={styles.meta} data-testid="attachment-viewer-meta">
      {attachmentTypeLabel(attachment.media_type, attachment.original_filename)}
      {" · "}
      {formatAttachmentSize(attachment.content_length)}
      {ordinal ? ` · ${ordinal}` : ""}
    </p>
  );
}

function AttachmentStatus({
  conversationId,
  attachment,
}: {
  conversationId: string;
  attachment: ChatAttachmentProjection;
}) {
  if (isPhotoAttachment(attachment.media_type)) {
    return (
      <p className={styles.note} data-testid="attachment-viewer-photo-note">
        照片随这条消息保留，不进入全局知识库或画像。
      </p>
    );
  }
  return (
    <div data-testid="attachment-viewer-status">
      <AttachmentIngestionInfo
        conversationId={conversationId}
        attachment={attachment}
        labels={CHAT_INGESTION_LABELS}
      />
      {attachment.ingestion_status === "error" && attachment.ingestion_error && (
        <>
          <p
            role="alert"
            className={styles.note}
            data-testid="attachment-viewer-ingestion-error"
          >
            {attachment.ingestion_error}
          </p>
          <p className={styles.note}>可下载原件核对后在本会话重新发送该文件。</p>
        </>
      )}
      {attachment.ingestion_status === "empty" && (
        <p className={styles.note} data-testid="attachment-viewer-ingestion-empty">
          解析完成但没有可引用的正文，可下载原件查看或重新发送可解析的版本。
        </p>
      )}
      <p className={styles.note}>文件仅在本会话内解析和引用，不进入全局知识库或画像。</p>
    </div>
  );
}

function AttachmentViewerContent({
  conversationId,
  attachment,
  onClose,
}: {
  conversationId: string;
  attachment: ChatAttachmentProjection;
  onClose: () => void;
}) {
  const panelRef = useRef<HTMLElement>(null);
  const [retryToken, setRetryToken] = useState(0);
  const [state, setState] = useState<PanelState>({ kind: "loading" });
  const previewKind = previewKindOf(attachment.media_type);

  useEffect(() => {
    panelRef.current?.focus();
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.isComposing) return;
      event.preventDefault();
      onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  useEffect(() => {
    if (previewKind === "unsupported") return;
    const controller = new AbortController();
    let objectUrl: string | null = null;
    setState({ kind: "loading" });

    const load = async () => {
      try {
        const response = await fetchChatAttachmentContent(
          conversationId,
          attachment.object_id,
          controller.signal
        );
        const blob = await response.blob();
        if (controller.signal.aborted) return;
        if (previewKind === "image" || previewKind === "pdf") {
          objectUrl = URL.createObjectURL(blob);
          setState({ kind: "ready", objectUrl });
          return;
        }
        const truncated = blob.size > TEXT_PREVIEW_MAX_BYTES;
        const textBlob = truncated ? blob.slice(0, TEXT_PREVIEW_MAX_BYTES) : blob;
        const text = await textBlob.text();
        if (controller.signal.aborted) return;
        setState({ kind: "ready", text, truncated });
      } catch (error) {
        if (controller.signal.aborted) return;
        setState({
          kind: "error",
          message:
            error instanceof Error ? error.message : "附件内容读取失败，请稍后重试。",
        });
      }
    };
    void load();

    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [attachment.object_id, conversationId, previewKind, retryToken]);

  const markPreviewBroken = () => {
    setState({ kind: "error", message: "附件内容无法显示，请下载原件查看。" });
  };

  const downloadUrl = chatAttachmentContentUrl(conversationId, attachment.object_id);

  return (
    <aside
      ref={panelRef}
      id="attachment-viewer"
      role="dialog"
      aria-modal="false"
      tabIndex={-1}
      aria-labelledby="attachment-viewer-title"
      className={styles.panel}
      data-testid="attachment-viewer"
    >
      <header className={styles.header}>
        <div className={styles.titleGroup}>
          <span className={styles.kicker}>附件</span>
          <h2
            id="attachment-viewer-title"
            className={styles.title}
            title={attachment.original_filename}
          >
            {attachment.original_filename}
          </h2>
        </div>
        <button
          type="button"
          aria-label="关闭附件预览"
          className={styles.closeButton}
          data-testid="attachment-viewer-close"
          onClick={onClose}
        >
          <Icon name="close" size={18} aria-hidden />
        </button>
      </header>

      <AttachmentMeta attachment={attachment} />
      <div className={styles.statusRow}>
        <AttachmentStatus conversationId={conversationId} attachment={attachment} />
      </div>

      <div className={styles.body}>
        {previewKind === "unsupported" ? (
          <div className={styles.preview} data-testid="attachment-viewer-unsupported">
            <p className={styles.note}>
              该格式暂不支持在右侧内嵌预览。可下载原件后用本地应用打开；当前可预览
              PDF、TXT、Markdown 与 PNG、JPEG、GIF、WebP 图片。
            </p>
          </div>
        ) : state.kind === "loading" ? (
          <p role="status" className={styles.statusLine} data-testid="attachment-viewer-loading">
            <span className="bg-spinner" aria-hidden="true" />
            正在读取附件…
          </p>
        ) : state.kind === "error" ? (
          <div role="alert" className={styles.error} data-testid="attachment-viewer-error">
            <span>{state.message}</span>
            <button
              type="button"
              className={styles.retryButton}
              data-testid="attachment-viewer-retry"
              onClick={() => setRetryToken((value) => value + 1)}
            >
              <Icon name="retry" size={14} aria-hidden />
              重试
            </button>
          </div>
        ) : previewKind === "image" && state.objectUrl ? (
          <div className={styles.preview}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              className={styles.image}
              src={state.objectUrl}
              alt={`附件预览：${attachment.original_filename}`}
              data-testid="attachment-viewer-image"
              onError={markPreviewBroken}
            />
          </div>
        ) : previewKind === "pdf" && state.objectUrl ? (
          <div className={styles.preview}>
            <iframe
              className={styles.pdfFrame}
              src={state.objectUrl}
              title={`PDF 预览：${attachment.original_filename}`}
              data-testid="attachment-viewer-pdf"
            />
            <p className={styles.note}>如浏览器无法显示 PDF，请下载原件后用本地应用打开。</p>
          </div>
        ) : previewKind === "markdown" ? (
          <div className={styles.preview}>
            <div className={styles.markdownPreview} data-testid="attachment-viewer-markdown">
              <MarkdownBody text={state.text ?? ""} />
            </div>
            {state.truncated && (
              <p className={styles.truncated} data-testid="attachment-viewer-truncated">
                文件较大，仅显示前 1 MB；完整内容请下载原件。
              </p>
            )}
          </div>
        ) : previewKind === "text" ? (
          <div className={styles.preview}>
            <pre className={styles.plainText} data-testid="attachment-viewer-text">
              {state.text ?? ""}
            </pre>
            {state.truncated && (
              <p className={styles.truncated} data-testid="attachment-viewer-truncated">
                文件较大，仅显示前 1 MB；完整内容请下载原件。
              </p>
            )}
          </div>
        ) : null}

        <div className={styles.downloadRow} style={{ marginTop: "var(--space-4)" }}>
          <a
            className={styles.downloadLink}
            href={downloadUrl}
            download={attachment.original_filename}
            data-testid="attachment-viewer-download"
          >
            <Icon name="download" size={15} aria-hidden />
            下载原件
          </a>
        </div>
      </div>
    </aside>
  );
}

/**
 * 右侧附件预览面板：默认不渲染；只在用户点击消息附件后读取该对象。
 *
 * 对象身份由 conversation/message/object + 内容摘要固定；切换附件、关闭、
 * 切换会话或账户卸载都会中止读取并释放 object URL，迟到结果不覆盖新选择。
 */
export function AttachmentViewerPanel({
  conversationId,
  selection,
  onClose,
}: {
  conversationId: string;
  selection: { messageId: string; attachment: ChatAttachmentProjection } | null;
  onClose: () => void;
}) {
  if (!selection) return null;
  const key = `${conversationId}:${selection.messageId}:${selection.attachment.object_id}:${selection.attachment.content_hash}`;
  return (
    <AttachmentViewerContent
      key={key}
      conversationId={conversationId}
      attachment={selection.attachment}
      onClose={onClose}
    />
  );
}
