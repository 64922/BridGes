"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  getChatConversation,
  type ChatConversationProjection,
} from "@/lib/api";

export type ConversationLoadState = "loading" | "ready" | "error";

/**
 * 会话权威历史的唯一读取入口。
 *
 * 每次读取带递增请求号；切换会话后迟到的响应不会写入新会话。
 * ``setConversation`` 供首轮创建响应直接替换投影，并作废在途读取。
 */
export function useConversationHistory(conversationId: string) {
  const [conversation, setConversationState] =
    useState<ChatConversationProjection | null>(null);
  const [loadState, setLoadState] = useState<ConversationLoadState>("loading");
  const [loadError, setLoadError] = useState("");
  const [revision, setRevision] = useState(0);
  const requestRef = useRef(0);
  const conversationIdRef = useRef(conversationId);
  conversationIdRef.current = conversationId;

  const setConversation = useCallback(
    (next: ChatConversationProjection) => {
      requestRef.current += 1;
      setConversationState(next);
      setLoadState("ready");
      setLoadError("");
      setRevision((current) => current + 1);
    },
    []
  );

  const load = useCallback(
    async (keepContent = false): Promise<ChatConversationProjection | null> => {
      const requestId = ++requestRef.current;
      const targetConversationId = conversationId;
      if (!keepContent) setLoadState("loading");
      setLoadError("");
      try {
        const projection = await getChatConversation(targetConversationId);
        if (
          requestId !== requestRef.current ||
          conversationIdRef.current !== targetConversationId
        ) {
          return null;
        }
        setConversationState(projection);
        setLoadState("ready");
        setRevision((current) => current + 1);
        return projection;
      } catch (error) {
        if (
          requestId !== requestRef.current ||
          conversationIdRef.current !== targetConversationId
        ) {
          return null;
        }
        setLoadState("error");
        setLoadError(error instanceof Error ? error.message : "对话加载失败。");
        return null;
      }
    },
    [conversationId]
  );

  useEffect(() => {
    requestRef.current += 1;
    setConversationState(null);
    setLoadState("loading");
    setLoadError("");
    void load();
  }, [load]);

  return {
    conversation,
    setConversation,
    loadState,
    loadError,
    load,
    revision,
  };
}
