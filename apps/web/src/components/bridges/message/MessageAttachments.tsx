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
}: {
  attachments: ChatAttachmentProjection[];
  conversationId?: string;
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
      {attachments.map((attachment) =>
        // 照片出缩略图；文档出文件名、类型、大小、解析状态与下载，
        // 不把不可预览的文件当图片渲染。
        isPhotoAttachment(attachment.media_type) ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            key={attachment.object_id}
            role="listitem"
            src={
              conversationId
                ? chatAttachmentContentUrl(conversationId, attachment.object_id)
                : undefined
            }
            alt={`用户发送的照片：${attachment.original_filename}`}
            style={{
              width: 96,
              height: 96,
              objectFit: "cover",
              borderRadius: "var(--radius-md)",
              border: "1px solid var(--color-border)",
            }}
          />
        ) : (
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
            <span
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-1)",
                minWidth: 0,
              }}
            >
              <Icon name="documentPage" size={16} aria-hidden />
              <span
                title={attachment.original_filename}
                style={{
                  fontSize: "var(--text-sm)",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {attachment.original_filename}
              </span>
            </span>
            <span
              style={{
                fontSize: "var(--text-xs)",
                color: "var(--color-text-secondary)",
              }}
            >
              {attachmentTypeLabel(attachment.media_type, attachment.original_filename)}
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
        )
      )}
    </div>
  );
}
