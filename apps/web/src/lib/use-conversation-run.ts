"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  createChatRun,
  isChatStreamEventOf,
  retryChatRun,
  startFirstTurn,
  stopChatMessage,
  type ChatConversationProjection,
  type ChatMessageProjection,
  type ChatModuleId,
  type ChatStreamEvent,
} from "@/lib/api";
import type { ChatModuleSelectionId } from "@/lib/chat-modules";
import { CHAT_LIST_CHANGED_EVENT } from "@/lib/recent-conversations";

import {
  activeRunFromAssistant,
  activeRunFromError,
  activeRunFromStarted,
  applyRunProgress,
  type ActiveRun,
  type ActiveRunKind,
  type PendingUserMessage,
} from "./chat-run";
import { subscribeChatRunWithReconnect } from "./chat-run-subscription";

export interface UseConversationRunOptions {
  conversationId: string;
  conversation: ChatConversationProjection | null;
  ready: boolean;
  revision: number;
  load: (keepContent?: boolean) => Promise<ChatConversationProjection | null>;
  setConversation: (conversation: ChatConversationProjection) => void;
}

/**
 * 对话运行的唯一状态所有者：发送、重试、停止、页面恢复与事件订阅。
 *
 * 订阅释放只中断本地读取，不调用停止接口；切换会话后旧令牌失效，
 * 迟到事件和迟到的历史响应都不能写入新会话。终态投影由权威历史收敛。
 */
export function useConversationRun({
  conversationId,
  conversation,
  ready,
  revision,
  load,
  setConversation,
}: UseConversationRunOptions) {
  const [activeRun, setActiveRunState] = useState<ActiveRun | null>(null);
  const [pendingUser, setPendingUser] = useState<PendingUserMessage | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState<string | null>(null);

  const activeRunRef = useRef<ActiveRun | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const commandRef = useRef(false);
  const epochRef = useRef(0);
  const conversationIdRef = useRef(conversationId);
  const revisionRef = useRef(revision);
  const runStartRevisionRef = useRef(0);
  conversationIdRef.current = conversationId;
  revisionRef.current = revision;

  const firstTurnKeyRef = useRef<string | null>(null);
  const firstTurnRetryRef = useRef(false);
  const sendKeyRef = useRef<{
    text: string;
    attachments: string[];
    key: string;
  } | null>(null);
  const retryKeyRef = useRef<{
    messageId: string;
    moduleId: string | null;
    key: string;
  } | null>(null);

  const commitRun = useCallback((run: ActiveRun | null) => {
    activeRunRef.current = run;
    setActiveRunState(run);
  }, []);

  const reportError = useCallback((message: string | null) => {
    setSendError(message);
  }, []);

  const announce = useCallback((message: string | null) => {
    setAnnouncement(message);
  }, []);

  const isCurrent = useCallback(
    (token: number, targetConversationId: string) =>
      epochRef.current === token &&
      conversationIdRef.current === targetConversationId,
    []
  );

  const handleEvent = useCallback(
    (
      token: number,
      targetConversationId: string,
      kind: ActiveRunKind,
      text: string,
      event: ChatStreamEvent
    ) => {
      if (!isCurrent(token, targetConversationId)) return;
      const current = activeRunRef.current;

      if (isChatStreamEventOf(event, "started")) {
        if (current) return;
        const run = activeRunFromStarted(event.data, kind);
        runStartRevisionRef.current = revisionRef.current;
        commitRun(run);
        if (kind === "send") {
          setPendingUser((current) => ({
            id: event.data.user_message_id,
            text,
            attachments: current?.attachments ?? [],
          }));
        }
        setAnnouncement("正在生成回答");
        return;
      }

      if (isChatStreamEventOf(event, "done")) {
        if (!current || current.messageId !== event.data.message_id) return;
        if (current.status !== "streaming") return;
        commitRun({ ...current, status: "done" });
        setAnnouncement("回答已生成");
        void load(true);
        return;
      }

      if (isChatStreamEventOf(event, "error")) {
        if (!current || current.messageId !== event.data.message_id) return;
        if (current.status !== "streaming") return;
        commitRun(activeRunFromError(current, event.data, kind));
        setAnnouncement(`生成失败：${event.data.error.message}`);
        void load(true);
        return;
      }

      const next = applyRunProgress(current, event);
      if (next !== current) commitRun(next);
    },
    [commitRun, isCurrent, load]
  );

  const subscribeWithRetry = useCallback(
    (
      token: number,
      targetConversationId: string,
      messageId: string,
      cursor: number,
      kind: ActiveRunKind,
      text: string,
      signal: AbortSignal
    ): Promise<void> =>
      subscribeChatRunWithReconnect({
        conversationId: targetConversationId,
        messageId,
        cursor,
        signal,
        isActive: () => isCurrent(token, targetConversationId),
        onEvent: (event) =>
          handleEvent(token, targetConversationId, kind, text, event),
        onTerminalProjection: setConversation,
      }),
    [handleEvent, isCurrent, setConversation]
  );

  const sendMessage = useCallback(
    async (
      text: string,
      attachmentIds: string[] = [],
      module: ChatModuleSelectionId | null = null
    ): Promise<boolean> => {
      if (commandRef.current) return false;
      commandRef.current = true;
      setSendError(null);
      setAnnouncement("正在生成回答");
      const token = ++epochRef.current;
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      let assistantMessage: ChatMessageProjection | null = null;
      try {
        const keyEntry = sendKeyRef.current;
        const samePayload =
          keyEntry &&
          keyEntry.text === text &&
          keyEntry.attachments.join(",") === attachmentIds.join(",");
        const sendIdempotencyKey = samePayload ? keyEntry.key : crypto.randomUUID();
        sendKeyRef.current = {
          text,
          attachments: attachmentIds,
          key: sendIdempotencyKey,
        };

        const isFirstTurn =
          firstTurnRetryRef.current ||
          (conversation !== null && (conversation.messages?.length ?? 0) === 0);
        let userMessage: ChatMessageProjection;
        let cursor: number;
        if (isFirstTurn) {
          const idempotencyKey =
            firstTurnKeyRef.current ??
            (firstTurnKeyRef.current = crypto.randomUUID());
          const firstTurn = await startFirstTurn({
            content: text,
            idempotencyKey,
            conversationId,
            mode: conversation?.mode ?? "companion",
            attachmentIds,
            moduleId: module ?? undefined,
          });
          if (!isCurrent(token, conversationId)) return true;
          setConversation(firstTurn.conversation);
          firstTurnRetryRef.current = true;
          userMessage = firstTurn.user_message;
          assistantMessage = firstTurn.assistant_message;
          cursor = firstTurn.cursor;
        } else {
          const run = await createChatRun(
            conversationId,
            text,
            sendIdempotencyKey,
            attachmentIds,
            module ?? undefined
          );
          if (!isCurrent(token, conversationId)) return true;
          userMessage = run.user_message;
          assistantMessage = run.assistant_message;
          cursor = run.cursor;
        }

        sendKeyRef.current = null;
        const runState = activeRunFromAssistant(assistantMessage, "send");
        runStartRevisionRef.current = revisionRef.current;
        commitRun(runState);
        setPendingUser({
          id: userMessage.message_id,
          text,
          attachments: userMessage.attachments ?? [],
        });
        await subscribeWithRetry(
          token,
          conversationId,
          assistantMessage.message_id,
          cursor,
          "send",
          text,
          controller.signal
        );
        if (isCurrent(token, conversationId)) {
          firstTurnRetryRef.current = false;
        }
        return true;
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return true;
        }
        if (!isCurrent(token, conversationId)) return true;
        const current = activeRunRef.current;
        if (
          assistantMessage &&
          current?.messageId === assistantMessage.message_id &&
          current.status !== "streaming"
        ) {
          return true;
        }
        const message =
          error instanceof Error ? error.message : "发送失败，请稍后重试。";
        setSendError(message);
        setAnnouncement(`生成失败：${message}`);
        commitRun(null);
        setPendingUser(null);
        return false;
      } finally {
        if (isCurrent(token, conversationId)) {
          abortRef.current = null;
          commandRef.current = false;
        }
        window.dispatchEvent(new Event(CHAT_LIST_CHANGED_EVENT));
      }
    },
    [
      commitRun,
      conversation,
      conversationId,
      isCurrent,
      setConversation,
      subscribeWithRetry,
    ]
  );

  const retry = useCallback(
    async (messageId: string, moduleOverride?: ChatModuleId) => {
      if (commandRef.current) return;
      if (activeRunRef.current?.status === "streaming") return;
      commandRef.current = true;
      setSendError(null);
      setAnnouncement("正在重试生成");
      const token = ++epochRef.current;
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      let assistantMessage: ChatMessageProjection | null = null;
      try {
        const override = moduleOverride ?? null;
        const keyEntry = retryKeyRef.current;
        const retryIdempotencyKey =
          keyEntry &&
          keyEntry.messageId === messageId &&
          keyEntry.moduleId === override
            ? keyEntry.key
            : crypto.randomUUID();
        retryKeyRef.current = {
          messageId,
          moduleId: override,
          key: retryIdempotencyKey,
        };

        const run = await retryChatRun(
          conversationId,
          messageId,
          retryIdempotencyKey,
          moduleOverride
        );
        if (!isCurrent(token, conversationId)) return;
        retryKeyRef.current = null;
        assistantMessage = run.assistant_message;
        const runState = activeRunFromAssistant(run.assistant_message, "retry");
        runStartRevisionRef.current = revisionRef.current;
        commitRun(runState);
        await subscribeWithRetry(
          token,
          conversationId,
          run.assistant_message.message_id,
          run.cursor,
          "retry",
          "",
          controller.signal
        );
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return;
        if (!isCurrent(token, conversationId)) return;
        const current = activeRunRef.current;
        if (
          assistantMessage &&
          current?.messageId === assistantMessage.message_id &&
          current.status !== "streaming"
        ) {
          return;
        }
        const message =
          error instanceof Error ? error.message : "重试失败，请稍后再试。";
        setSendError(message);
        setAnnouncement(`重试失败：${message}`);
        void load(true);
      } finally {
        if (isCurrent(token, conversationId)) {
          abortRef.current = null;
          commandRef.current = false;
        }
        window.dispatchEvent(new Event(CHAT_LIST_CHANGED_EVENT));
      }
    },
    [commitRun, conversationId, isCurrent, load, subscribeWithRetry]
  );

  const stop = useCallback(async () => {
    const run = activeRunRef.current;
    if (!run || run.status !== "streaming") return;
    const token = epochRef.current;
    try {
      await stopChatMessage(conversationId, run.messageId);
    } catch {
      // 停止失败不阻塞中断；权威历史刷新后以服务端状态为准。
    }
    if (!isCurrent(token, conversationId)) return;
    const latest = activeRunRef.current;
    if (
      !latest ||
      latest.messageId !== run.messageId ||
      latest.status !== "streaming"
    ) {
      return;
    }
    abortRef.current?.abort();
    commitRun({ ...latest, status: "done" });
    setAnnouncement("已停止生成");
    void load(true);
  }, [commitRun, conversationId, isCurrent, load]);

  useEffect(() => {
    const run = activeRunRef.current;
    if (!run || revision <= runStartRevisionRef.current) return;
    const message = (conversation?.messages ?? []).find(
      (item) => item.message_id === run.messageId
    );
    if (!message) return;
    if (message.status === "streaming" && run.status === "streaming") return;
    commitRun(null);
    setPendingUser(null);
  }, [commitRun, conversation, revision]);

  useEffect(() => {
    if (!ready || !conversation || activeRunRef.current || commandRef.current) {
      return;
    }
    const streaming = (conversation.messages ?? []).find(
      (message) => message.status === "streaming" && message.active_run
    );
    if (!streaming) return;
    const token = ++epochRef.current;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const run = activeRunFromAssistant(streaming, "resume");
    run.content = streaming.content ?? "";
    runStartRevisionRef.current = revisionRef.current;
    commitRun(run);
    setAnnouncement("正在生成回答");
    const cursor = streaming.active_run?.cursor ?? 0;
    void subscribeWithRetry(
      token,
      conversationId,
      streaming.message_id,
      cursor,
      "resume",
      "",
      controller.signal
    ).catch((error: unknown) => {
      if (!isCurrent(token, conversationId)) return;
      const message =
        error instanceof Error ? error.message : "连接生成事件失败，请刷新后重试。";
      setSendError(message);
      setAnnouncement(`生成失败：${message}`);
      void load(true);
    });
  }, [
    commitRun,
    conversation,
    conversationId,
    isCurrent,
    load,
    ready,
    subscribeWithRetry,
  ]);

  useEffect(() => {
    epochRef.current += 1;
    abortRef.current?.abort();
    abortRef.current = null;
    activeRunRef.current = null;
    setActiveRunState(null);
    setPendingUser(null);
    setSendError(null);
    setAnnouncement(null);
    commandRef.current = false;
    runStartRevisionRef.current = 0;
    firstTurnKeyRef.current = null;
    firstTurnRetryRef.current = false;
    sendKeyRef.current = null;
    retryKeyRef.current = null;
  }, [conversationId]);

  useEffect(
    () => () => {
      epochRef.current += 1;
      abortRef.current?.abort();
      activeRunRef.current = null;
    },
    []
  );

  return {
    activeRun,
    pendingUser,
    generating: activeRun?.status === "streaming",
    sendError,
    announcement,
    sendMessage,
    retry,
    stop,
    reportError,
    announce,
  };
}
