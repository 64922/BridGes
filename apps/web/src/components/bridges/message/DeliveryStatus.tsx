"use client";

import { TurnResultCard } from "@/components/bridges/chat/TurnResultCard";

import type { ChatMessageView } from "./types";

/** 已提交回合的公开结果投影（完成/待输入/部分/阻塞/取消 + 可信状态 + 真实恢复）。 */
export function DeliveryStatus({
  message,
  onRetry,
}: {
  message: ChatMessageView;
  onRetry?: (id: string) => void;
}) {
  if (!message.turnResult) return null;
  return (
    <TurnResultCard
      result={message.turnResult}
      onContinue={onRetry ? () => onRetry(message.id) : undefined}
    />
  );
}
