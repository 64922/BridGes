"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";

import { StateBlock } from "@/components/bridges/StateBlock";
import { ChatSendErrorBanner } from "@/components/bridges/chat/ChatSendErrorBanner";
import { ChatThread } from "@/components/bridges/chat/ChatThread";
import { Composer } from "@/components/bridges/Composer";
import { AppShell } from "@/components/layout/AppShell";
import { CHAT_LIST_CHANGED_EVENT } from "@/lib/recent-conversations";
import {
  ApiError,
  getChatConversation,
  retryChatMessage,
  stopChatMessage,
  streamChatMessage,
  type ChatConversationProjection,
  type ChatStreamEvent,
} from "@/lib/api";
import { chatPromptKey } from "@/lib/chat-flow";
import { buildThreadMessages } from "@/lib/chat-thread";

import styles from "@/components/bridges/chat/chat.module.css";

interface ActiveRun {
  messageId: string;
  content: string;
  kind: "send" | "retry";
}

/**
 * 对话页：历史消息 + 流式回答 + 停止/重试 + 能力预检错误提示。
 *
 * 状态覆盖：loading（加载中）/ error（加载失败可重试）/ 恢复后正常（重启
 * 后重新打开同一对话）/ 生成中（streaming + 停止入口）/ 失败（保留正文 +
 * 重试）。键盘可达：跳转链接 → 侧栏最近对话 → 消息 → 输入区，Enter 发送、
 * Shift+Enter 换行、Esc 停止；流式更新不移动焦点、不逐 token 朗读。
 */
export default function ChatConversationPage() {
  const params = useParams<{ conversationId: string }>();
  const conversationId = params.conversationId;

  const [conversation, setConversation] = useState<ChatConversationProjection | null>(null);
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">("loading");
  const [loadError, setLoadError] = useState("");
  const [activeRun, setActiveRun] = useState<ActiveRun | null>(null);
  const [pendingUser, setPendingUser] = useState<{ id: string; text: string } | null>(null);
  const [sendError, setSendError] = useState<{ message: string; code?: string } | null>(null);
  const [announcement, setAnnouncement] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const sendingRef = useRef(false);

  const load = useCallback(async () => {
    setLoadState("loading");
    setLoadError("");
    try {
      const projection = await getChatConversation(conversationId);
      setConversation(projection);
      setLoadState("ready");
    } catch (error) {
      setLoadState("error");
      setLoadError(error instanceof Error ? error.message : "对话加载失败。");
    }
  }, [conversationId]);

  useEffect(() => {
    setConversation(null);
    setActiveRun(null);
    setPendingUser(null);
    setSendError(null);
    void load();
  }, [load]);

  // 新对话首页跳转带来的待发送消息：同步消费防 StrictMode 双发
  useEffect(() => {
    if (loadState !== "ready" || conversation === null || (conversation.messages ?? []).length > 0) {
      return;
    }
    if (sendingRef.current) return;
    const prompt = sessionStorage.getItem(chatPromptKey(conversationId));
    if (prompt) {
      sessionStorage.removeItem(chatPromptKey(conversationId));
      sendingRef.current = true;
      void sendMessage(prompt);
    }
  }, [loadState, conversation, conversationId]);

  const handleStreamEvent = useCallback(
    (kind: ActiveRun["kind"], text: string) =>
      (event: ChatStreamEvent) => {
        if (event.event === "started") {
          // 同步写入 ref：SSE 事件可能在同一块内连续到达（started 后紧跟
          // delta），异步 setState 尚未刷新时 delta 处理器依赖 ref 判断归属
          const run = { messageId: event.data.message_id, content: "", kind };
          activeRunRef.current = run;
          if (kind === "send") {
            setPendingUser({ id: event.data.user_message_id, text });
          }
          setActiveRun(run);
          setAnnouncement("正在生成回答");
        } else if (event.event === "delta" && activeRunRef.current?.messageId === event.data.message_id) {
          activeRunRef.current = {
            ...activeRunRef.current,
            content: activeRunRef.current.content + event.data.delta,
          };
          setActiveRun((run) => (run ? { ...run, content: run.content + event.data.delta } : run));
        } else if (event.event === "done" || event.event === "error") {
          // 收敛：清除进行中状态，重新加载服务端权威历史
          activeRunRef.current = null;
          setActiveRun(null);
          setPendingUser(null);
          setAnnouncement(
            event.event === "done" ? "回答已生成" : `生成失败：${event.data.error.message}`
          );
          void load();
        }
      },
    [load]
  );
  const activeRunRef = useRef<ActiveRun | null>(null);
  activeRunRef.current = activeRun;

  const sendMessage = useCallback(
    async (text: string) => {
      setSendError(null);
      setAnnouncement("正在生成回答");
      const controller = new AbortController();
      abortRef.current = controller;
      try {
        await streamChatMessage(conversationId, text, handleStreamEvent("send", text), controller.signal);
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return; // 停止：状态由停止接口收敛
        }
        const message = error instanceof Error ? error.message : "发送失败，请稍后重试。";
        setSendError({ message, code: error instanceof ApiError ? error.code : undefined });
        setAnnouncement(`生成失败：${message}`);
        // 断流/内部错误时服务端已收敛消息状态：刷新展示可重试错误
        void load();
      } finally {
        abortRef.current = null;
        sendingRef.current = false;
        window.dispatchEvent(new Event(CHAT_LIST_CHANGED_EVENT));
      }
    },
    [conversationId, handleStreamEvent, load]
  );

  const stop = useCallback(async () => {
    const run = activeRunRef.current;
    if (!run) return;
    // 先收敛服务端状态，再中断客户端流：保证终态为"已停止"而非断流错误
    try {
      await stopChatMessage(conversationId, run.messageId);
    } catch {
      // 停止失败不阻塞中断；重新加载后以服务端状态为准
    }
    abortRef.current?.abort();
    // 中断后清除进行中状态：否则输入区会一直停留在「停止」无法恢复发送
    activeRunRef.current = null;
    setActiveRun(null);
    setPendingUser(null);
    setAnnouncement("已停止生成");
    await load();
  }, [conversationId, load]);

  const retry = useCallback(
    async (messageId: string) => {
      setSendError(null);
      setAnnouncement("正在重试生成");
      const controller = new AbortController();
      abortRef.current = controller;
      try {
        await retryChatMessage(conversationId, messageId, handleStreamEvent("retry", ""), controller.signal);
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        const message = error instanceof Error ? error.message : "重试失败，请稍后再试。";
        setSendError({ message, code: error instanceof ApiError ? error.code : undefined });
        setAnnouncement(`重试失败：${message}`);
        void load();
      } finally {
        abortRef.current = null;
        window.dispatchEvent(new Event(CHAT_LIST_CHANGED_EVENT));
      }
    },
    [conversationId, handleStreamEvent, load]
  );

  // 组装渲染消息：服务端历史 + 进行中的乐观消息
  const baseMessages = conversation ? buildThreadMessages(conversation.messages ?? []) : [];
  const threadMessages = [...baseMessages];
  if (pendingUser && activeRun && activeRun.kind === "send") {
    threadMessages.push({
      id: pendingUser.id,
      role: "user",
      plainText: pendingUser.text,
      content: (
        <p style={{ whiteSpace: "pre-wrap", overflowWrap: "break-word" }}>{pendingUser.text}</p>
      ),
    });
    threadMessages.push({
      id: activeRun.messageId,
      role: "assistant",
      plainText: activeRun.content,
      content: (
        <p style={{ whiteSpace: "pre-wrap", overflowWrap: "break-word" }}>
          {activeRun.content}
          <span className={styles.streamCursor} aria-hidden="true" />
        </p>
      ),
      status: "streaming",
    });
  } else if (activeRun && activeRun.kind === "retry" && conversation) {
    // 重试流：在最新尝试（可能失败）之后追加新的流式尝试
    threadMessages.push({
      id: activeRun.messageId,
      role: "assistant",
      plainText: activeRun.content,
      content: (
        <p style={{ whiteSpace: "pre-wrap", overflowWrap: "break-word" }}>
          {activeRun.content}
          <span className={styles.streamCursor} aria-hidden="true" />
        </p>
      ),
      status: "streaming",
    });
  }

  const generating = activeRun !== null;

  return (
    <AppShell mode="account">
      <div className={styles.chatShell}>
        <main
          id="main-content"
          tabIndex={-1}
          data-testid="main-content"
          className={styles.chatMain}
        >
          {loadState === "loading" ? (
            <StateBlock kind="loading" title="正在加载对话" description="读取消息历史与生成状态。" />
          ) : loadState === "error" ? (
            <StateBlock
              kind="error"
              title="对话加载失败"
              description={loadError || "请检查连接后重试。"}
              actionLabel="重新加载"
              onAction={() => void load()}
            />
          ) : (
            <>
              <ChatThread
                messages={threadMessages}
                onRetry={(messageId) => void retry(messageId)}
                announcement={announcement}
              />
              {sendError && (
                <ChatSendErrorBanner message={sendError.message} code={sendError.code} />
              )}
              <div className={styles.composerWrap}>
                <div className={styles.composerInner}>
                  <Composer
                    onSend={(text) => void sendMessage(text)}
                    generating={generating}
                    onStop={() => void stop()}
                  />
                </div>
              </div>
            </>
          )}
        </main>
      </div>
    </AppShell>
  );
}
