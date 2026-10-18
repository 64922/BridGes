"use client";

import { useEffect, useRef } from "react";

import { Icon } from "@/components/design-system/Icon";
import type {
  TiebaPostProjection,
  TiebaSummaryCitation,
} from "@/lib/api";

import styles from "./TiebaEvidencePanel.module.css";
import { READ_STATUS_LABELS, formatTime, postReadScope } from "./TiebaEvidenceRows";

export interface TiebaEvidenceSelection {
  conversationId: string;
  messageId: string;
  citation: TiebaSummaryCitation;
  post: TiebaPostProjection | null;
}

/** 只接受同源相对路径或 https 地址，拒绝 javascript: 与协议相对地址。 */
function safeOpenUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  if (value.startsWith("/") && !value.startsWith("//")) return value;
  if (/^https:\/\//i.test(value)) return value;
  return null;
}

function locationOf(citation: TiebaSummaryCitation): string {
  const parts = [`引用 ${citation.citation_id}`];
  parts.push(citation.floor != null ? `第 ${citation.floor} 楼` : "楼层未知");
  if (citation.posted_at) parts.push(`发言时间 ${citation.posted_at}`);
  return parts.join(" · ");
}

function TiebaEvidenceContent({
  selection,
  onClose,
}: {
  selection: TiebaEvidenceSelection;
  onClose: () => void;
}) {
  const { citation, post } = selection;
  const panelRef = useRef<HTMLElement>(null);
  const openUrl = safeOpenUrl(citation.url) ?? safeOpenUrl(post?.url);
  const replies = post?.replies ?? [];
  const citedReply =
    citation.floor != null
      ? replies.find((reply) => reply.floor === citation.floor)
      : citation.reply_id != null
        ? replies.find((reply) => reply.reply_id === citation.reply_id)
        : undefined;
  const hasLocation = citation.floor != null || citation.reply_id != null;

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

  return (
    <aside
      ref={panelRef}
      id="tieba-evidence-panel"
      role="dialog"
      aria-modal="false"
      tabIndex={-1}
      aria-labelledby="tieba-evidence-title"
      className={styles.panel}
      data-testid="tieba-evidence-panel"
    >
      <header className={styles.header}>
        <div className={styles.titleGroup}>
          <span className={styles.kicker}>贴吧资料</span>
          <h2
            id="tieba-evidence-title"
            className={styles.title}
            title={citation.title ?? post?.title ?? "帖子引用"}
          >
            {citation.title ?? post?.title ?? "帖子引用"}
          </h2>
        </div>
        <button
          type="button"
          aria-label="关闭贴吧资料面板"
          className={styles.closeButton}
          data-testid="tieba-evidence-close"
          onClick={onClose}
        >
          <Icon name="close" size={18} aria-hidden />
        </button>
      </header>

      <p className={styles.meta} data-testid="tieba-evidence-location">
        {locationOf(citation)}
      </p>

      <div className={styles.body}>
        <blockquote className={styles.quote} data-testid="tieba-evidence-quote">
          {citation.quote}
        </blockquote>

        {post ? (
          <section className={styles.section} data-testid="tieba-evidence-scope">
            <h3 className={styles.sectionTitle}>本帖读取范围</h3>
            <p className={styles.note}>{postReadScope(post)}</p>
            {post.read_error_message && (
              <p className={styles.warning}>
                {READ_STATUS_LABELS[post.read_status] ?? post.read_status}：
                {post.read_error_message}
              </p>
            )}
          </section>
        ) : (
          <p className={styles.warning} data-testid="tieba-evidence-no-scope">
            本轮投影没有提供对应帖子的读取范围或回复原文，只能按引用摘录定位到帖子。
          </p>
        )}

        {!hasLocation && (
          <p className={styles.warning} data-testid="tieba-evidence-no-location">
            本轮引用没有提供具体楼层或回复定位，只能定位到帖子；请打开原帖核对。
          </p>
        )}
        {hasLocation && !citedReply && (
          <p className={styles.warning} data-testid="tieba-evidence-location-missing">
            引用的
            {citation.floor != null ? `第 ${citation.floor} 楼` : "回复"}
            不在本轮已读原文里，无法展开该楼层原文。
          </p>
        )}

        <section className={styles.section} data-testid="tieba-evidence-replies">
          <h3 className={styles.sectionTitle}>
            帖子与回复（本轮已读 {replies.length} 条）
          </h3>
          {replies.length > 0 ? (
            <ol className={styles.replyList}>
              {replies.map((reply, index) => {
                const active = reply === citedReply;
                return (
                  <li
                    key={`${reply.floor ?? "unknown"}-${reply.reply_id ?? index}`}
                    className={styles.reply}
                    data-active={active || undefined}
                    data-testid={`tieba-evidence-reply-${
                      reply.floor ?? reply.reply_id ?? index
                    }`}
                  >
                    <span className={styles.replyMeta}>
                      {reply.floor != null ? `第 ${reply.floor} 楼` : "楼层未知"}
                      {reply.is_original_poster ? " · 楼主" : ""}
                      {reply.posted_at ? ` · ${reply.posted_at}` : " · 时间未给出"}
                      {active ? " · 当前引用" : ""}
                    </span>
                    <span className={styles.replyBody}>{reply.content}</span>
                  </li>
                );
              })}
            </ol>
          ) : post ? (
            <p className={styles.warning} data-testid="tieba-evidence-no-replies">
              未取得回复内容
              {post.read_error_message ? `：${post.read_error_message}` : "。"}
            </p>
          ) : (
            <p className={styles.warning} data-testid="tieba-evidence-no-replies">
              投影没有提供该帖的回复原文，不能补写回复内容。
            </p>
          )}
        </section>

        <p className={styles.note} data-testid="tieba-evidence-retrieved">
          帖子读取于 {formatTime(post?.retrieved_at)}
        </p>

        {openUrl && (
          <a
            className={styles.openLink}
            href={openUrl}
            target="_blank"
            rel="noopener noreferrer"
            data-testid="tieba-evidence-open"
          >
            <Icon name="documentPage" size={15} aria-hidden />
            打开原帖
          </a>
        )}
      </div>
    </aside>
  );
}

/**
 * 贴吧引用的右侧资料面板：只用该轮投影里的引用与已读原文，不再发起网络
 * 读取；资料身份绑定到具体消息里的 citation，楼层缺失时如实说明。
 */
export function TiebaEvidencePanel({
  selection,
  onClose,
}: {
  selection: TiebaEvidenceSelection | null;
  onClose: () => void;
}) {
  if (!selection) return null;
  return (
    <TiebaEvidenceContent
      key={`${selection.messageId}:${selection.citation.citation_id}`}
      selection={selection}
      onClose={onClose}
    />
  );
}
