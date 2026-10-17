"use client";

import { useEffect, useRef, useState } from "react";

import { Icon } from "@/components/design-system/Icon";
import {
  ApiError,
  getCitationDetail,
  type CitationDetailProjection,
  type CitationProjection,
} from "@/lib/api";

import styles from "./CitationViewerPanel.module.css";

type PanelState =
  | { kind: "loading" }
  | { kind: "missing" }
  | { kind: "error" }
  | { kind: "detail"; detail: CitationDetailProjection };

function locationOf(citation: CitationProjection): string {
  const parts: string[] = [];
  if (citation.page_number !== null && citation.page_number !== undefined) {
    parts.push(`第 ${citation.page_number} 页`);
  }
  if (citation.section_title) parts.push(`章节：${citation.section_title}`);
  return parts.length > 0 ? parts.join(" · ") : "原文片段";
}

/** 只接受同源相对路径或 https 地址，拒绝 javascript: 与协议相对地址。 */
function safeOpenUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  if (value.startsWith("/") && !value.startsWith("//")) return value;
  if (/^https:\/\//i.test(value)) return value;
  return null;
}

function isExternalUrl(value: string): boolean {
  return /^https?:\/\//i.test(value);
}

function accessLabel(status: CitationDetailProjection["access_status"]): string {
  if (status === "accessible") return "原文可访问";
  if (status === "deleted") return "原文已删除";
  return "授权已变化";
}

function CitationDetailView({ detail }: { detail: CitationDetailProjection }) {
  const { citation, access_status: status } = detail;
  const openUrl = safeOpenUrl(detail.download_url);
  const accessible = status === "accessible";

  return (
    <div className={styles.detail} data-testid="citation-viewer-detail">
      <span
        role={accessible ? "status" : "alert"}
        className={styles.status}
        data-status={status}
        data-testid="citation-viewer-status"
      >
        {accessLabel(status)}
      </span>

      {accessible ? (
        detail.citation.snippet.trim().length > 0 ? (
          <blockquote className={styles.snippet} data-testid="citation-viewer-snippet">
            {detail.citation.snippet}
          </blockquote>
        ) : (
          <p className={styles.note} data-testid="citation-viewer-empty">
            这条引用没有可读取的原文片段。
          </p>
        )
      ) : (
        // 原文删除或授权变化后不再保留旧片段，避免继续展示敏感内容。
        <p className={styles.note} data-testid="citation-viewer-unavailable">
          {detail.access_message || "当前无法读取这段资料。"}
        </p>
      )}

      {accessible && detail.access_message && (
        <p className={styles.note}>{detail.access_message}</p>
      )}

      {accessible && openUrl && (
        <a
          className={styles.openLink}
          href={openUrl}
          {...(isExternalUrl(openUrl)
            ? { target: "_blank", rel: "noopener noreferrer" }
            : {})}
          data-testid={`citation-open-${citation.rank}`}
        >
          <Icon name="download" size={15} aria-hidden />
          打开原文
        </a>
      )}
    </div>
  );
}

function CitationViewerContent({
  conversationId,
  messageId,
  citation,
  onClose,
}: {
  conversationId: string;
  messageId: string;
  citation: CitationProjection;
  onClose: () => void;
}) {
  const panelRef = useRef<HTMLElement>(null);
  const [retryToken, setRetryToken] = useState(0);
  const [state, setState] = useState<PanelState>({ kind: "loading" });

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
    const controller = new AbortController();
    // 每次读取先清空旧详情，迟到的旧请求与旧内容都不能覆盖当前选择。
    setState({ kind: "loading" });
    getCitationDetail(conversationId, messageId, citation.citation_id, controller.signal)
      .then((detail) => {
        if (controller.signal.aborted) return;
        setState({ kind: "detail", detail });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        if (
          error instanceof ApiError
            ? error.status === 404
            : (error as { status?: number } | null)?.status === 404
        ) {
          setState({ kind: "missing" });
          return;
        }
        setState({ kind: "error" });
      });
    return () => controller.abort();
  }, [conversationId, messageId, citation.citation_id, retryToken]);

  return (
    <aside
      ref={panelRef}
      id="citation-viewer"
      role="dialog"
      aria-modal="false"
      tabIndex={-1}
      aria-labelledby="citation-viewer-title"
      className={styles.panel}
      data-testid="citation-viewer"
    >
      <header className={styles.header}>
        <div className={styles.titleGroup}>
          <span className={styles.kicker}>资料</span>
          <h2 id="citation-viewer-title" className={styles.title} title={citation.filename}>
            {citation.filename}
          </h2>
        </div>
        <button
          type="button"
          aria-label="关闭资料面板"
          className={styles.closeButton}
          data-testid="citation-viewer-close"
          onClick={onClose}
        >
          <Icon name="close" size={18} aria-hidden />
        </button>
      </header>

      <p className={styles.meta} data-testid="citation-viewer-location">
        {locationOf(citation)}
      </p>

      <div className={styles.body}>
        {state.kind === "loading" && (
          <p role="status" className={styles.statusLine} data-testid="citation-viewer-loading">
            <span className="bg-spinner" aria-hidden="true" />
            正在读取资料…
          </p>
        )}

        {state.kind === "missing" && (
          <p role="status" className={styles.note} data-testid="citation-viewer-missing">
            这条引用已不存在或不属于当前账户，没有可显示的资料。
          </p>
        )}

        {state.kind === "error" && (
          <div role="alert" className={styles.error} data-testid="citation-viewer-error">
            <span>资料读取失败，请稍后重试。</span>
            <button
              type="button"
              className={styles.retryButton}
              data-testid="citation-viewer-retry"
              onClick={() => setRetryToken((value) => value + 1)}
            >
              <Icon name="retry" size={14} aria-hidden />
              重试
            </button>
          </div>
        )}

        {state.kind === "detail" && <CitationDetailView detail={state.detail} />}
      </div>
    </aside>
  );
}

/**
 * 右侧资料面板：默认不渲染；只在用户点击真实引用后读取一次详情。
 * 资料身份由 conversation/message/citation 三元组固定，选择变化即取消旧请求。
 */
export function CitationViewerPanel({
  conversationId,
  selection,
  onClose,
}: {
  conversationId: string;
  selection: { messageId: string; citation: CitationProjection } | null;
  onClose: () => void;
}) {
  if (!selection) return null;
  const key = `${selection.messageId}:${selection.citation.citation_id}`;
  return (
    <CitationViewerContent
      key={key}
      conversationId={conversationId}
      messageId={selection.messageId}
      citation={selection.citation}
      onClose={onClose}
    />
  );
}
