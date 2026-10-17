"use client";

import { chatAttachmentDraftContentUrl } from "@/lib/api";
import type { ChatAttachmentDraftProjection } from "@/lib/api";
import {
  CHAT_INGESTION_LABELS,
  attachmentIcon,
  attachmentTypeLabel,
  formatAttachmentSize,
  isPhotoAttachment,
} from "@/lib/chat-attachments";
import { Icon } from "@/components/design-system/Icon";
import { IngestionStatusChip } from "../../AttachmentIngestion";
import { iconButtonStyle } from "./composer-styles";
import type { ComposerDraftError } from "./useComposerAttachments";

interface ComposerAttachmentsProps {
  drafts: ChatAttachmentDraftProjection[];
  draftErrors: ComposerDraftError[];
  brokenPreviews: Set<string>;
  onDismissError: (id: number) => void;
  onMove: (objectId: string, offset: -1 | 1) => void;
  onRemove: (objectId: string) => void;
  onPreviewError: (objectId: string) => void;
}

/** 输入区附件草稿呈现：被拒绝原因与按页序排列的待发送附件。 */
export function ComposerAttachments({
  drafts,
  draftErrors,
  brokenPreviews,
  onDismissError,
  onMove,
  onRemove,
  onPreviewError,
}: ComposerAttachmentsProps) {
  return (
    <>
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
                onClick={() => onDismissError(error.id)}
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
          aria-label="待发送附件"
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
            const photo = isPhotoAttachment(draft.media_type);
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
                {/* 照片出缩略图（同源预览，经账户授权返回）；文件出文档卡片
                    （图标 + 类型 + 大小），不把不可预览的文件当图片渲染。 */}
                {photo ? (
                  brokenPreviews.has(draft.object_id) ? (
                    <div
                      role="note"
                      style={{
                        width: "100%",
                        height: 64,
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        border: "1px dashed var(--color-border)",
                        borderRadius: "var(--radius-sm)",
                        color: "var(--color-status-error)",
                        fontSize: "var(--text-sm)",
                        textAlign: "center",
                        padding: "0 var(--space-1)",
                      }}
                    >
                      照片内容当前无法读取，请移除后重新添加。
                    </div>
                  ) : (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={chatAttachmentDraftContentUrl(draft.object_id)}
                      alt={`照片预览：${filename}`}
                      onError={() => onPreviewError(draft.object_id)}
                      style={{
                        width: "100%",
                        height: 64,
                        objectFit: "cover",
                        borderRadius: "var(--radius-sm)",
                      }}
                    />
                  )
                ) : (
                  <div
                    data-testid="composer-file-card"
                    style={{
                      width: "100%",
                      height: 64,
                      display: "flex",
                      flexDirection: "column",
                      alignItems: "center",
                      justifyContent: "center",
                      gap: "2px",
                      border: "1px solid var(--color-border)",
                      borderRadius: "var(--radius-sm)",
                      backgroundColor: "var(--color-bg-secondary)",
                      color: "var(--color-text-secondary)",
                      fontSize: "var(--text-sm)",
                    }}
                  >
                    <Icon name={attachmentIcon(draft.media_type)} size={20} aria-hidden />
                    <span>{attachmentTypeLabel(draft.media_type, filename)}</span>
                    <span>{formatAttachmentSize(draft.content_length)}</span>
                  </div>
                )}
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
                  第 {index + 1} {photo ? "张" : "个"}
                </span>
                {photo && (
                  <span
                    style={{
                      fontSize: "var(--text-sm)",
                      color: "var(--color-text-secondary)",
                    }}
                  >
                    {attachmentTypeLabel(draft.media_type, filename)}
                  </span>
                )}
                {/* AC2：解析中、可用、失败与无法识别的状态可见；失败原因
                    直接给出中文说明，不用颜色或图标代替文字。 */}
                {!photo && (
                  <IngestionStatusChip
                    status={draft.ingestion_status}
                    label={CHAT_INGESTION_LABELS[draft.ingestion_status]}
                  />
                )}
                {!photo && draft.ingestion_error && (
                  <span
                    role="alert"
                    style={{
                      fontSize: "var(--text-xs)",
                      color: "var(--color-status-error)",
                      overflowWrap: "break-word",
                    }}
                  >
                    {draft.ingestion_error}
                  </span>
                )}
                <div style={{ display: "flex", alignItems: "center", gap: "var(--space-1)" }}>
                  <button
                    type="button"
                    aria-label={`将 ${filename} 上移`}
                    disabled={index === 0}
                    onClick={() => onMove(draft.object_id, -1)}
                    style={{ ...iconButtonStyle, minWidth: 0, minHeight: 0, padding: "0 var(--space-1)" }}
                  >
                    ↑
                  </button>
                  <button
                    type="button"
                    aria-label={`将 ${filename} 下移`}
                    disabled={index === drafts.length - 1}
                    onClick={() => onMove(draft.object_id, 1)}
                    style={{ ...iconButtonStyle, minWidth: 0, minHeight: 0, padding: "0 var(--space-1)" }}
                  >
                    ↓
                  </button>
                  <span style={{ flex: 1 }} />
                  <button
                    type="button"
                    aria-label={`移除附件 ${filename}`}
                    onClick={() => onRemove(draft.object_id)}
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
    </>
  );
}
