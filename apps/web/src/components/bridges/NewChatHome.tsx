"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { BrandLogo } from "@/components/bridges/BrandLogo";
import { ChatSendErrorBanner } from "@/components/bridges/chat/ChatSendErrorBanner";
import { ChatHeader } from "@/components/bridges/chat/ChatHeader";
import { Composer } from "@/components/bridges/Composer";
import { type ChatMode } from "@/components/bridges/ModeToggle";
import { Icon, type IconName } from "@/components/design-system/Icon";
import { AppShell } from "@/components/layout/AppShell";
import { MAIN_CONTENT_ID } from "@/components/layout/MainContent";
import { ApiError, startFirstTurn } from "@/lib/api";
import { type ChatModuleSelectionId } from "@/lib/chat-modules";
import { CHAT_LIST_CHANGED_EVENT } from "@/lib/recent-conversations";
import type { ComposerPrefill } from "@/components/bridges/chat/composer/useComposerText";
import chatStyles from "@/components/bridges/chat/chat.module.css";

import styles from "./NewChatHome.module.css";

interface StarterSuggestion {
  label: string;
  prompt: string;
  icon: IconName;
  mode: ChatMode;
  moduleId?: ChatModuleSelectionId;
}

/** 轻量建议只预填真实首轮入口：论文模块、学习模式、贴吧模块。 */
const STARTER_SUGGESTIONS: readonly StarterSuggestion[] = [
  {
    label: "找几篇入门论文",
    prompt: "帮我找几篇入门论文",
    icon: "paperSearch",
    mode: "companion",
    moduleId: "paper",
  },
  {
    label: "把一个概念讲明白",
    prompt: "把一个概念讲明白",
    icon: "learningProject",
    mode: "study",
  },
  {
    label: "看看校园里的新消息",
    prompt: "华东交通大学吧最近有什么新消息？",
    icon: "tiebaThread",
    mode: "companion",
    moduleId: "tieba",
  },
];

/**
 * 登录后的新聊天首页。
 *
 * 静态欢迎与输入区共用限宽阅读列；模式选择在输入区内，首条消息通过
 * 原子首轮命令创建并锁定会话模式。首轮失败时保留草稿与附件，可直接重发。
 */
export function NewChatHome() {
  const router = useRouter();
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<{ message: string } | null>(null);
  const [mode, setMode] = useState<ChatMode>("companion");
  // V2 Issue 11：首页也可显式选择模块（随首条用户消息持久化）。
  const [moduleId, setModuleId] = useState<ChatModuleSelectionId | null>(null);
  const [prefill, setPrefill] = useState<ComposerPrefill | null>(null);
  const suggestionNonceRef = useRef(0);
  const firstTurnInFlightRef = useRef(false);
  const idempotencyKeyRef = useRef<string | null>(null);

  const submitFirstTurn = async (content: string, attachmentIds: string[] = []): Promise<boolean> => {
    if (firstTurnInFlightRef.current) return false;
    firstTurnInFlightRef.current = true;
    if (idempotencyKeyRef.current === null) {
      idempotencyKeyRef.current = crypto.randomUUID();
    }
    setSending(true);
    setSendError(null);

    try {
      const result = await startFirstTurn({
        content,
        idempotencyKey: idempotencyKeyRef.current,
        mode,
        // Issue 05：新聊天页直发照片——账户域草稿随首轮原子绑定。
        attachmentIds,
        // V2 Issue 11：显式模块随首轮消息保存（未选择时不提交）。
        moduleId: mode === "companion" ? moduleId ?? undefined : undefined,
      });
      idempotencyKeyRef.current = null;
      window.dispatchEvent(new Event(CHAT_LIST_CHANGED_EVENT));
      router.push(`/chat/${result.conversation.conversation_id}`);
      return true;
    } catch (error) {
      // 明确拒绝时允许下一次意图使用新键；网络/5xx 保留同键交给服务端
      // 幂等合同收敛，避免重试产生第二个会话。
      if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
        idempotencyKeyRef.current = null;
      }
      setSendError({
        message: error instanceof Error ? error.message : "发送失败，请稍后重试。",
      });
      setSending(false);
      requestAnimationFrame(() => document.getElementById("composer-input")?.focus());
      return false;
    } finally {
      firstTurnInFlightRef.current = false;
    }
  };

  const changeMode = (
    nextMode: ChatMode,
    nextModuleId?: ChatModuleSelectionId | null
  ) => {
    setMode(nextMode);
    if (nextMode === "study") {
      setModuleId(null);
      return;
    }
    if (nextModuleId !== undefined) {
      setModuleId(nextModuleId);
    }
  };

  const applySuggestion = (suggestion: StarterSuggestion) => {
    changeMode(suggestion.mode, suggestion.moduleId ?? null);
    suggestionNonceRef.current += 1;
    setPrefill({ text: suggestion.prompt, nonce: suggestionNonceRef.current });
  };

  return (
    <AppShell showSkipLink={false}>
      <div className={chatStyles.chatShell}>
        <main
          id={MAIN_CONTENT_ID}
          tabIndex={-1}
          data-testid="main-content"
          className={chatStyles.chatMain}
        >
          <ChatHeader title="新对话" />
          <div className={styles.blankState} data-testid="new-chat-home">
            <div className={styles.blankStateInner}>
              <div className={styles.welcomeMark} aria-hidden="true">
                <BrandLogo variant="icon" width={58} />
              </div>
              <p className={styles.welcomeEyebrow}>从一个好奇开始</p>
              <h2 className={styles.welcomeTitle}>今天，想弄懂什么？</h2>
              <p className={styles.welcomeSub}>一个问题、一段灵感，或者一份想一起读的资料。</p>

              <div className={styles.newChatComposer}>
                {sendError && (
                  <>
                    <ChatSendErrorBanner message={sendError.message} align="center" />
                    <p className={styles.sendRecoveryHint}>
                      草稿与附件已保留，可直接点击「发送」重试。
                    </p>
                  </>
                )}
                <Composer
                  variant="new-chat"
                  onSend={submitFirstTurn}
                  generating={sending}
                  moduleId={moduleId}
                  onModuleChange={setModuleId}
                  mode={mode}
                  onModeChange={changeMode}
                  modeDisabled={sending}
                  prefill={prefill}
                />
                <ul className={styles.starterPrompts} role="list" aria-label="建议提问">
                  {STARTER_SUGGESTIONS.map((suggestion) => (
                    <li key={suggestion.label}>
                      <button
                        type="button"
                        disabled={sending}
                        onClick={() => applySuggestion(suggestion)}
                      >
                        <Icon name={suggestion.icon} size={15} aria-hidden />
                        {suggestion.label}
                      </button>
                    </li>
                  ))}
                </ul>
                <p className={styles.blankStateNote}>
                  BridGes 的回答会标注依据与来源；重要内容请核对引用。
                </p>
              </div>
            </div>
          </div>
        </main>
      </div>
    </AppShell>
  );
}
