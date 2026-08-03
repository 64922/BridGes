"use client";

import { useCallback, useEffect, useState } from "react";
import { usePathname } from "next/navigation";

import { listChatConversations, type ChatConversationSummary } from "@/lib/api";

/** 对话列表变更事件名：发送/重试完成后派发，侧栏据此刷新。 */
export const CHAT_LIST_CHANGED_EVENT = "bridges:chat-list-changed";

/**
 * 最近对话数据（真实 API）。
 *
 * 数据来自 `listChatConversations()`；账户切换时由 AppShell 的 key 重挂载
 * 触发重新拉取，不读取其他账户的对话。列表随
 * `bridges:chat-list-changed` 事件与路由变化刷新。
 */
export function useRecentConversations() {
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

  return { conversations, loading, loadError, reload: load };
}
