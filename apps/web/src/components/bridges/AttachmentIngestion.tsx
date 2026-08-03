"use client";

import { useCallback, useEffect, useState } from "react";

import { Icon, type IconName } from "@/components/design-system/Icon";
import {
  getAttachmentIngestion,
  type ChatAttachmentProjection,
  type DocumentIngestionProjection,
} from "@/lib/api";

/**
 * 文档摄取状态芯片（Issue 17）。
 *
 * 状态语义与颜色：等待（琥珀）、成功（绿）、失败（红）、恢复（品牌蓝）、
 * 中性（灰）；每个状态都带图标 + 中文文字，不只靠颜色表达。失败用
 * role="alert" 播报，其余用 role="status"，符合 WCAG 状态播报要求。
 */
export interface IngestionStatusChipProps {
  status: string;
  error?: string | null;
  onRetry?: () => void;
  retrying?: boolean;
}

const STATUS_CONFIG: Record<string, { label: string; icon: IconName; color: string; bg: string }> = {
  queued: {
    label: "等待解析",
    icon: "info",
    color: "var(--color-status-wait)",
    bg: "var(--color-status-wait-bg)",
  },
  processing: {
    label: "解析索引中…",
    icon: "info",
    color: "var(--color-status-wait)",
    bg: "var(--color-status-wait-bg)",
  },
  ready: {
    label: "已可检索",
    icon: "check",
    color: "var(--color-status-success)",
    bg: "var(--color-status-success-bg)",
  },
  empty: {
    label: "无可索引内容",
    icon: "info",
    color: "var(--color-text-tertiary)",
    bg: "var(--color-surface-muted, #f1f2f3)",
  },
  error: {
    label: "解析失败",
    icon: "alert",
    color: "var(--color-status-error)",
    bg: "var(--color-status-error-bg)",
  },
  recovery: {
    label: "处理中断，恢复中",
    icon: "retry",
    color: "var(--color-accent-primary)",
    bg: "var(--color-accent-primary-soft)",
  },
  none: {
    label: "未索引",
    icon: "info",
    color: "var(--color-text-tertiary)",
    bg: "var(--color-surface-muted, #f1f2f3)",
  },
  loading: {
    label: "加载中…",
    icon: "info",
    color: "var(--color-text-tertiary)",
    bg: "var(--color-surface-muted, #f1f2f3)",
  },
  permission: {
    label: "无访问权限",
    icon: "account",
    color: "var(--color-status-wait)",
    bg: "var(--color-status-wait-bg)",
  },
};

export function IngestionStatusChip({
  status,
  error,
  onRetry,
  retrying,
}: IngestionStatusChipProps) {
  const config = STATUS_CONFIG[status] ?? STATUS_CONFIG.none;
  const isError = status === "error";
  return (
    <span
      role={isError ? "alert" : "status"}
      data-testid={`ingestion-status-${status}`}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "var(--space-1)",
        padding: "2px var(--space-2)",
        borderRadius: "999px",
        fontSize: "var(--text-xs)",
        fontWeight: 600,
        color: config.color,
        backgroundColor: config.bg,
        maxWidth: "100%",
      }}
    >
      {status === "processing" || status === "loading" ? (
        <span className="bg-spinner" aria-hidden="true" />
      ) : (
        <Icon name={config.icon} size={14} aria-hidden />
      )}
      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {config.label}
      </span>
      {isError && onRetry && (
        <button
          type="button"
          onClick={onRetry}
          disabled={retrying}
          aria-label="重新解析附件"
          title="重新解析附件"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: "2px",
            marginLeft: "var(--space-1)",
            padding: "2px var(--space-1)",
            border: "none",
            borderRadius: "999px",
            backgroundColor: "transparent",
            color: "var(--color-status-error)",
            cursor: retrying ? "default" : "pointer",
            font: "inherit",
            fontSize: "var(--text-xs)",
            fontWeight: 600,
            opacity: retrying ? 0.6 : 1,
          }}
        >
          {retrying ? (
            <span className="bg-spinner" aria-hidden="true" />
          ) : (
            <Icon name="retry" size={13} aria-hidden />
          )}
          {retrying ? "重试中…" : "重试"}
        </button>
      )}
    </span>
  );
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        gap: "var(--space-3)",
        fontSize: "var(--text-xs)",
      }}
    >
      <span style={{ color: "var(--color-text-tertiary)" }}>{label}</span>
      <span style={{ color: "var(--color-text-secondary)", textAlign: "right" }}>{value}</span>
    </div>
  );
}

/**
 * 附件摄取详情（可展开）：解析器版本、页码/章节、分块数、向量可用性、
 * 失败阶段与重试次数。详情按需请求，展开时显示加载态，失败不静默。
 */
function AttachmentIngestionDetail({
  conversationId,
  attachment,
  onRetryIngestion,
  onStateChange,
}: {
  conversationId: string;
  attachment: ChatAttachmentProjection;
  onRetryIngestion?: (objectId: string) => void;
  onStateChange?: (status: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [detail, setDetail] = useState<DocumentIngestionProjection | null>(null);
  const [loading, setLoading] = useState(false);
  const [fetchError, setFetchError] = useState("");
  const [detailErrorKind, setDetailErrorKind] = useState<"error" | "permission" | null>(null);
  const [retrying, setRetrying] = useState(false);

  const loadDetail = useCallback(async () => {
    setLoading(true);
    setFetchError("");
    setDetailErrorKind(null);
    try {
      const projection = await getAttachmentIngestion(conversationId, attachment.object_id);
      setDetail(projection);
      onStateChange?.(projection.status);
    } catch (error) {
      // 越权/不存在（404 attachment_not_found）呈现 permission 状态，
      // 与 AC9 要求的 permission 状态对齐，不以空面板掩盖。
      const code = error instanceof Error ? (error as Error & { code?: string }).code : undefined;
      const message = error instanceof Error ? error.message : "摄取详情加载失败。";
      if (code === "attachment_not_found" || /没有访问权限/.test(message)) {
        setDetailErrorKind("permission");
      } else {
        setDetailErrorKind("error");
      }
      setFetchError(message);
    } finally {
      setLoading(false);
    }
  }, [conversationId, attachment.object_id, onStateChange]);

  useEffect(() => {
    if (!expanded || detail || loading) return;
    void loadDetail();
  }, [expanded, detail, loading, loadDetail]);

  const retry = async () => {
    if (!onRetryIngestion) return;
    setRetrying(true);
    setFetchError("");
    try {
      // 由页面处理器统一调用重试 API 并刷新对话（状态以服务端投影为准）
      await onRetryIngestion(attachment.object_id);
    } catch (error) {
      setFetchError(error instanceof Error ? error.message : "重新解析失败。");
    } finally {
      setRetrying(false);
    }
  };

  const title = attachment.ingestion_error
    ? `摄取失败原因：${attachment.ingestion_error}`
    : "查看摄取详情";

  return (
    <div data-testid="attachment-ingestion-detail">
      <button
        type="button"
        aria-expanded={expanded}
        aria-controls={`ingestion-detail-${attachment.object_id}`}
        onClick={() => setExpanded((value) => !value)}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: "2px",
          padding: "2px 0",
          border: "none",
          backgroundColor: "transparent",
          color: "var(--color-text-tertiary)",
          cursor: "pointer",
          font: "inherit",
          fontSize: "var(--text-xs)",
          minHeight: "var(--target-size)",
        }}
        title={title}
      >
        <Icon
          name={expanded ? "chevronDown" : "chevronRight"}
          size={14}
          aria-hidden
        />
        {attachment.ingestion_error ? `详情（${attachment.ingestion_error}）` : "详情"}
      </button>
      {expanded && (
        <div
          id={`ingestion-detail-${attachment.object_id}`}
          style={{
            display: "flex",
            flexDirection: "column",
            gap: "var(--space-1)",
            marginTop: "var(--space-1)",
            padding: "var(--space-2)",
            borderRadius: "var(--radius-md)",
            backgroundColor: "var(--color-surface-muted, #f1f2f3)",
          }}
        >
          {loading && <IngestionStatusChip status="loading" />}
          {detailErrorKind && (
            <div
              role={detailErrorKind === "error" ? "alert" : "status"}
              style={{
                display: "flex",
                flexDirection: "column",
                gap: "var(--space-1)",
                alignItems: "flex-start",
              }}
            >
              <IngestionStatusChip status={detailErrorKind} />
              <span style={{ fontSize: "var(--text-xs)", color: "var(--color-text-secondary)" }}>
                {fetchError}
              </span>
            </div>
          )}
          {detail && !loading && (
            <>
              <DetailRow label="状态" value={detail.status} />
              <DetailRow label="解析器" value={detail.parser_version} />
              <DetailRow label="页码 / 章节" value={`${detail.page_count} / ${detail.section_count}`} />
              <DetailRow label="分块数" value={String(detail.chunk_count)} />
              <DetailRow
                label="向量索引"
                value={
                  detail.vector_indexed
                    ? "已写入"
                    : detail.vector_unavailable_reason
                      ? `不可用（${detail.vector_unavailable_reason}）`
                      : "未写入"
                }
              />
              {detail.index_rebuilding && (
                <DetailRow label="索引重建" value="进行中，旧版本继续可用" />
              )}
              {detail.failure_reason && (
                <DetailRow label="失败原因" value={detail.failure_reason} />
              )}
              {detail.retry_count > 0 && (
                <DetailRow label="处理尝试" value={`${detail.retry_count} 次`} />
              )}
              {detail.status === "error" && (
                <button
                  type="button"
                  onClick={retry}
                  disabled={retrying}
                  aria-label="重新解析附件"
                  style={{
                    alignSelf: "flex-start",
                    display: "inline-flex",
                    alignItems: "center",
                    gap: "var(--space-1)",
                    padding: "var(--space-1) var(--space-2)",
                    border: "1px solid var(--color-status-error)",
                    borderRadius: "var(--radius-md)",
                    backgroundColor: "transparent",
                    color: "var(--color-status-error)",
                    cursor: retrying ? "default" : "pointer",
                    font: "inherit",
                    fontSize: "var(--text-xs)",
                    fontWeight: 600,
                    minHeight: "var(--target-size)",
                  }}
                >
                  {retrying && <span className="bg-spinner" aria-hidden="true" />}
                  {retrying ? "重新入队中…" : "重新解析"}
                </button>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

export function AttachmentIngestionInfo({
  conversationId,
  attachment,
  onRetryIngestion,
}: {
  conversationId: string;
  attachment: ChatAttachmentProjection;
  onRetryIngestion?: (objectId: string) => void;
}) {
  // 附件投影随对话刷新变化：状态以服务端最新投影为准，不保留陈旧状态
  const [status, setStatus] = useState(attachment.ingestion_status || "none");
  const [error, setError] = useState(attachment.ingestion_error ?? undefined);

  useEffect(() => {
    setStatus(attachment.ingestion_status || "none");
    setError(attachment.ingestion_error ?? undefined);
  }, [attachment.ingestion_status, attachment.ingestion_error]);

  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        alignItems: "center",
        columnGap: "var(--space-2)",
        rowGap: "var(--space-1)",
        marginTop: "var(--space-1)",
      }}
    >
      <IngestionStatusChip
        status={status}
        error={error}
        onRetry={onRetryIngestion ? () => onRetryIngestion(attachment.object_id) : undefined}
      />
      <AttachmentIngestionDetail
        conversationId={conversationId}
        attachment={attachment}
        onRetryIngestion={onRetryIngestion}
        onStateChange={setStatus}
      />
    </div>
  );
}
