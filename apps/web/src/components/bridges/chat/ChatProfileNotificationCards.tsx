"use client";

import { useState } from "react";

import { Button } from "@/components/design-system/Button";
import { Icon } from "@/components/design-system/Icon";
import type { ProfileNotification } from "@/lib/api";

import styles from "./chat.module.css";

const KIND_META: Record<
  ProfileNotification["kind"],
  { label: string; tone: "auto" | "candidate" | "intent" | "emotion" }
> = {
  auto_write: { label: "已自动记录", tone: "auto" },
  candidate_proposed: { label: "已生成候选", tone: "candidate" },
  intent_recorded: { label: "记忆指令", tone: "intent" },
  transient_emotion: { label: "情绪情境", tone: "emotion" },
};

interface ChatProfileNotificationCardsProps {
  notifications: ProfileNotification[];
  onRecall: (notification: ProfileNotification) => Promise<void>;
  onDismiss: (notificationId: string) => void;
}

/**
 * 聊天内的画像通知卡片（Issue 26）。
 *
 * 明确记忆、许可内自动写入、敏感候选与单次情绪提示在 SSE profile 事件
 * 到达时即时展示，每条携带来源消息；自动写入记录提供一键撤回（撤回后
 * 卡片保留并显示"已撤回"状态，不显示假成功）。关闭即标记已读。
 */
export function ChatProfileNotificationCards({
  notifications,
  onRecall,
  onDismiss,
}: ChatProfileNotificationCardsProps) {
  const [recallingId, setRecallingId] = useState<string | null>(null);
  const [recallError, setRecallError] = useState<string | null>(null);

  const handleRecall = async (notification: ProfileNotification) => {
    setRecallError(null);
    setRecallingId(notification.notification_id);
    try {
      await onRecall(notification);
    } catch (cause) {
      setRecallError(
        cause instanceof Error ? cause.message : "撤回失败，请稍后重试。"
      );
    } finally {
      setRecallingId(null);
    }
  };

  return (
    <div className={styles.profileNoticeStack} data-testid="chat-profile-notifications">
      {recallError && (
        <p role="alert" className={styles.profileNoticeError}>
          {recallError}
        </p>
      )}
      {notifications.map((notification) => {
        const meta = KIND_META[notification.kind] ?? KIND_META.intent_recorded;
        const recalled = notification.recalled_at !== null;
        return (
          <section
            key={notification.notification_id}
            className={`${styles.profileNotice} ${styles[`profileNoticeTone_${meta.tone}`]}`}
            aria-label={`画像通知：${meta.label}`}
          >
            <div className={styles.profileNoticeHeader}>
              <Icon name="info" size={16} />
              <h3 className={styles.profileNoticeTitle}>{notification.title}</h3>
              <button
                type="button"
                className={styles.profileNoticeClose}
                onClick={() => onDismiss(notification.notification_id)}
                aria-label="关闭并标记已读"
              >
                <Icon name="close" size={16} />
              </button>
            </div>
            <p className={styles.profileNoticeMessage}>{notification.message}</p>
            {notification.source_text && (
              <p className={styles.profileNoticeSource}>
                来源消息：“{notification.source_text}”
              </p>
            )}
            {notification.recallable && (
              <div className={styles.profileNoticeAction}>
                {recalled ? (
                  <p className={styles.profileNoticeRecalled} role="status">
                    已撤回，该记录不再用于回答且不会再被自动写入。
                  </p>
                ) : (
                  <Button
                    variant="secondary"
                    size="sm"
                    isLoading={recallingId === notification.notification_id}
                    disabled={recallingId !== null}
                    onClick={() => void handleRecall(notification)}
                  >
                    一键撤回
                  </Button>
                )}
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}
