"use client";

import { AttachmentIngestionInfo } from "@/components/bridges/AttachmentIngestion";
import { Icon } from "@/components/design-system/Icon";
import {
  CHAT_INGESTION_LABELS,
  attachmentTypeLabel,
  formatAttachmentSize,
  isPhotoAttachment,
} from "@/lib/chat-attachments";
import { chatAttachmentContentUrl } from "@/lib/api";
import type { ChatAttachmentProjection } from "@/lib/api";

export function MessageAttachments({
  attachments,
  conversationId,
  onOpenAttachment,
}: {
  attachments: ChatAttachmentProjection[];
  conversationId?: string;
  onOpenAttachment?: (
    attachment: ChatAttachmentProjection,
    trigger: HTMLButtonElement
  ) => void;
}) {
  return (
    <div
      role="list"
      aria-label="消息中的附件"
      style={{
        display: "flex",
        flexWrap: "wrap",
        alignItems: "flex-start",
        gap: "var(--space-2)",
        marginBottom: "var(--space-2)",
      }}
    >
      {attachments.map((attachment) => {
        const filename = attachment.original_filename;
        // 照片出缩略图；文档出文件名、类型、大小、解析状态与下载，
        // 不把不可预览的文件当图片渲染。有预览回调时，缩略图和文件名
        // 成为打开右侧预览的真实按钮；下载与摄取详情保持独立入口。
        if (isPhotoAttachment(attachment.media_type)) {
          const thumbnail = (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={
                conversationId
                  ? chatAttachmentContentUrl(conversationId, attachment.object_id)
                  : undefined
              }
              alt={`用户发送的照片：${filename}`}
              style={{
                display: "block",
                width: 96,
                height: 96,
                objectFit: "cover",
                borderRadius: "var(--radius-md)",
                border: "1px solid var(--color-border)",
              }}
            />
          );
          return (
            <div key={attachment.object_id} role="listitem">
              {onOpenAttachment ? (
                <button
                  type="button"
                  aria-label={`查看照片：${filename}`}
                  data-testid={`attachment-open-${attachment.object_id}`}
                  title="打开照片预览"
                  onClick={(event) => onOpenAttachment(attachment, event.currentTarget)}
                  style={{
                    display: "block",
                    padding: 0,
                    border: "none",
                    background: "transparent",
                    cursor: "pointer",
                  }}
                >
                  {thumbnail}
                </button>
              ) : (
                thumbnail
              )}
            </div>
          );
        }
        const filenameRow = (
          <>
            <Icon name="documentPage" size={16} aria-hidden />
            <span
              title={filename}
              style={{
                flex: 1,
                minWidth: 0,
                fontSize: "var(--text-sm)",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {filename}
            </span>
            {onOpenAttachment && <Icon name="chevronRight" size={14} aria-hidden />}
          </>
        );
        return (
          <div
            key={attachment.object_id}
            role="listitem"
            data-testid="message-file-card"
            style={{
              width: 232,
              padding: "var(--space-2)",
              border: "1px solid var(--color-border)",
              borderRadius: "var(--radius-md)",
              display: "flex",
              flexDirection: "column",
              gap: "var(--space-1)",
            }}
          >
            {onOpenAttachment ? (
              <button
                type="button"
                aria-label={`查看附件：${filename}`}
                data-testid={`attachment-open-${attachment.object_id}`}
                title="打开附件预览"
                onClick={(event) => onOpenAttachment(attachment, event.currentTarget)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "var(--space-1)",
                  minWidth: 0,
                  minHeight: "var(--target-size)",
                  padding: 0,
                  border: "none",
                  background: "transparent",
                  color: "inherit",
                  cursor: "pointer",
                  font: "inherit",
                  textAlign: "left",
                }}
              >
                {filenameRow}
              </button>
            ) : (
              <span
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "var(--space-1)",
                  minWidth: 0,
                }}
              >
                {filenameRow}
              </span>
            )}
            <span
              style={{
                fontSize: "var(--text-xs)",
                color: "var(--color-text-secondary)",
              }}
            >
              {attachmentTypeLabel(attachment.media_type, filename)}
              {" · "}
              {formatAttachmentSize(attachment.content_length)}
              {attachment.ordinal !== null &&
                attachment.ordinal !== undefined &&
                ` · 第 ${attachment.ordinal} 个`}
            </span>
            {conversationId && (
              <>
                <AttachmentIngestionInfo
                  conversationId={conversationId}
                  attachment={attachment}
                  labels={CHAT_INGESTION_LABELS}
                />
                {attachment.ingestion_status === "error" && (
                  <span
                    style={{
                      fontSize: "var(--text-xs)",
                      color: "var(--color-text-secondary)",
                      overflowWrap: "anywhere",
                    }}
                  >
                    可下载原件核对后在本会话重新发送该文件。
                  </span>
                )}
                {attachment.ingestion_status === "empty" && (
                  <span
                    style={{
                      fontSize: "var(--text-xs)",
                      color: "var(--color-text-secondary)",
                      overflowWrap: "anywhere",
                    }}
                  >
                    没有可引用的正文；可下载原件查看，或重新发送可解析的版本。
                  </span>
                )}
                <a
                  href={chatAttachmentContentUrl(conversationId, attachment.object_id)}
                  download={attachment.original_filename}
                  style={{
                    alignSelf: "flex-start",
                    display: "inline-flex",
                    alignItems: "center",
                    gap: "2px",
                    fontSize: "var(--text-xs)",
                    color: "var(--color-text-secondary)",
                    minHeight: "var(--target-size)",
                  }}
                >
                  <Icon name="download" size={14} aria-hidden />
                  下载原件
                </a>
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}
