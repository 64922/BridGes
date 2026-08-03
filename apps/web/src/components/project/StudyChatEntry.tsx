"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { Icon } from "@/components/design-system/Icon";
import { ApiError, createChatConversation } from "@/lib/api";

/**
 * 学习项目 → 新建学习对话入口（Issue 14，ADR-0022）。
 *
 * 从学习项目创建的新对话默认「学习模式」；创建后跳转到对话页，走正常
 * 授权、审计与对话保存流程，并保留学习项目归属，供最近对话恢复上下文。
 */
export function StudyChatEntry({ projectId }: { projectId: string }) {
  const router = useRouter();
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");

  const startStudyChat = async () => {
    if (creating) return;
    setCreating(true);
    setError("");
    try {
      const conversation = await createChatConversation(undefined, "study", projectId);
      router.push(`/chat/${conversation.conversation_id}`);
    } catch (exc) {
      setError(
        exc instanceof Error ? exc.message : "创建学习对话失败，请稍后重试。"
      );
      setCreating(false);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
      <button
        type="button"
        data-testid="study-chat-entry"
        onClick={() => void startStudyChat()}
        disabled={creating}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: "var(--space-2)",
          padding: "var(--space-2) var(--space-4)",
          borderRadius: "var(--radius-md)",
          border: "1px solid var(--color-border-strong)",
          backgroundColor: "var(--color-surface)",
          color: "var(--color-accent-primary)",
          fontSize: "var(--text-sm)",
          fontWeight: 600,
          cursor: creating ? "default" : "pointer",
          opacity: creating ? 0.7 : 1,
          minHeight: "var(--target-size)",
        }}
      >
        <Icon name="learningProject" size={16} aria-hidden />
        {creating ? "正在创建学习对话…" : "进入学习对话"}
      </button>
      {error && (
        <p role="alert" style={{ margin: 0, fontSize: "var(--text-sm)", color: "var(--color-status-error)" }}>
          {error}
        </p>
      )}
      <p
        style={{
          margin: 0,
          fontSize: "var(--text-xs)",
          color: "var(--color-text-tertiary)",
          maxWidth: "46ch",
        }}
      >
        学习对话默认「学习模式」，后续学习编排能力将在该模式下接入。
      </p>
    </div>
  );
}
