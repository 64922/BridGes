"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";

import { useAuth } from "@/context/AuthContext";
import { ApiError, listChatConversations, type ChatConversationSummary } from "@/lib/api";

/** 对话列表变更事件名：发送/重试完成后派发，侧栏据此刷新。 */
export const CHAT_LIST_CHANGED_EVENT = "bridges:chat-list-changed";

/**
 * 最近对话数据（真实 API）。
 *
 * 数据来自 `listChatConversations()`；账户切换时先清空旧快照，再按账户修订号
 * 重新拉取，不读取其他账户的对话。列表随 `bridges:chat-list-changed` 事件与
 * 路由变化刷新。
 */
export function useRecentConversations() {
  const pathname = usePathname();
  const { accountRevision, authState } = useAuth();
  const [conversations, setConversations] = useState<ChatConversationSummary[]>([]);
  const [loadError, setLoadError] = useState("");
  const [permissionDenied, setPermissionDenied] = useState(false);
  const [loading, setLoading] = useState(true);
  const [viewRevision, setViewRevision] = useState<number | null>(null);
  const requestIdRef = useRef(0);

  const load = useCallback(async () => {
    const requestId = ++requestIdRef.current;
    const requestedRevision = accountRevision;
    setLoading(true);
    setLoadError("");
    setPermissionDenied(false);
    setViewRevision(requestedRevision);
    try {
      const projection = await listChatConversations();
      if (requestId === requestIdRef.current) {
        setConversations(projection.conversations ?? []);
      }
    } catch (error) {
      if (requestId === requestIdRef.current) {
        setPermissionDenied(
          error instanceof ApiError && (error.status === 401 || error.status === 403)
        );
        setLoadError(error instanceof Error ? error.message : "对话列表加载失败。");
      }
    } finally {
      if (requestId === requestIdRef.current) setLoading(false);
    }
  }, [accountRevision]);

  useEffect(() => {
    // 账户切换与退出登录时先清空旧账户快照，避免异步请求返回后短暂泄漏
    // 上一个账户的列表；新账户认证完成后再加载其真实投影。
    setConversations([]);
    setViewRevision(null);
    if (authState === "authenticated") void load();
    else if (authState === "unauthenticated" || authState === "error") setLoading(false);
    window.addEventListener(CHAT_LIST_CHANGED_EVENT, load);
    return () => window.removeEventListener(CHAT_LIST_CHANGED_EVENT, load);
  }, [accountRevision, authState, load, pathname]);

  const hasCurrentAccountView =
    authState === "authenticated" && viewRevision === accountRevision;
  return {
    conversations: hasCurrentAccountView ? conversations : [],
    loading: loading || (authState === "authenticated" && !hasCurrentAccountView),
    loadError: hasCurrentAccountView ? loadError : "",
    permissionDenied: hasCurrentAccountView && permissionDenied,
    reload: load,
  };
}
