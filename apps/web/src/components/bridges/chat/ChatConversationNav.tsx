"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";

import { listChatConversations, type ChatConversationSummary } from "@/lib/api";
import { Button } from "@/components/design-system/Button";
import { Icon } from "@/components/design-system/Icon";

import styles from "./chat.module.css";

/** 对话列表变更事件名：发送/重试完成后派发，侧栏据此刷新。 */
export const CHAT_LIST_CHANGED_EVENT = "bridges:chat-list-changed";

/**
 * 对话侧栏：新对话按钮 + 按最近活动排序的对话列表。
 *
 * 数据来自真实 API；账户切换时由 AppShell 的 key 重挂载触发重新拉取，
 * 不读取其他账户的对话。列表随 `bridges:chat-list-changed` 事件刷新。
 */
export function ChatConversationNav({ activeConversationId }: { activeConversationId?: string }) {
  const router = useRouter();
  const pathname = usePathname();
  const [conversations, setConversations] = useState<ChatConversationSummary[]>([]);
  const [loadError, setLoadError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const projection = await listChatConversations();
      setConversations(projection.conversations ?? []);
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : "对话列表加载失败。");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    window.addEventListener(CHAT_LIST_CHANGED_EVENT, load);
    return () => window.removeEventListener(CHAT_LIST_CHANGED_EVENT, load);
  }, [load, pathname]);

  const startNewChat = () => {
    router.push("/");
  };

  return (
    <aside className={styles.sidebar} aria-label="对话列表">
      <Button variant="primary" size="sm" onClick={startNewChat} className={styles.newChatButton}>
        <Icon name="newChat" size={18} aria-hidden />
        新对话
      </Button>

      <p className={styles.sectionLabel}>最近</p>

      {loading && conversations.length === 0 ? (
        <p role="status" style={{ fontSize: "var(--text-sm)", color: "var(--color-text-tertiary)" }}>
          正在加载对话…
        </p>
      ) : loadError ? (
        <div role="alert" style={{ fontSize: "var(--text-sm)", color: "var(--color-status-error)" }}>
          {loadError}
          <Button variant="ghost" size="sm" onClick={() => void load()}>
            重试
          </Button>
        </div>
      ) : conversations.length === 0 ? (
        <p style={{ fontSize: "var(--text-sm)", color: "var(--color-text-tertiary)" }}>
          还没有对话，点击上方「新对话」开始。
        </p>
      ) : (
        <ul role="list" className={styles.conversationList}>
          {conversations.map((conversation) => (
            <li key={conversation.conversation_id}>
              <Link
                href={`/chat/${conversation.conversation_id}`}
                className={`${styles.conversationItem} ${
                  activeConversationId === conversation.conversation_id
                    ? styles.conversationItemActive
                    : ""
                }`}
                aria-current={
                  activeConversationId === conversation.conversation_id ? "page" : undefined
                }
              >
                <Icon name="recent" size={16} aria-hidden />
                <span
                  style={{
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                    minWidth: 0,
                  }}
                >
                  {conversation.title || "新对话"}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </aside>
  );
}
