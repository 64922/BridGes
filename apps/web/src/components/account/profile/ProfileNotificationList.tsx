"use client";

import { useCallback, useEffect, useState } from "react";

import { StateBlock } from "@/components/bridges/StateBlock";
import { Button } from "@/components/design-system/Button";
import { Icon } from "@/components/design-system/Icon";
import {
  listProfileNotifications,
  markProfileNotificationRead,
  recallProfileNotification,
  type ProfileNotification,
} from "@/lib/api";

import styles from "./ProfileCenter.module.css";

const KIND_META: Record<
  ProfileNotification["kind"],
  { label: string; tone: string }
> = {
  auto_write: { label: "自动写入", tone: "auto" },
  candidate_proposed: { label: "候选待确认", tone: "candidate" },
  intent_recorded: { label: "记忆指令", tone: "intent" },
  transient_emotion: { label: "情绪情境", tone: "emotion" },
};

/** 相对时间：分钟/小时/天内的简洁中文描述。 */
function relativeTime(iso: string): string {
  const created = new Date(iso).getTime();
  const diffMinutes = Math.max(1, Math.round((Date.now() - created) / 60000));
  if (diffMinutes < 60) return `${diffMinutes} 分钟前`;
  const diffHours = Math.round(diffMinutes / 60);
  if (diffHours < 24) return `${diffHours} 小时前`;
  return `${Math.round(diffHours / 24)} 天前`;
}

/**
 * 画像通知列表（Issue 26）：明确记忆、自动写入、敏感候选与单次情绪提示。
 *
 * 每条通知展示来源消息与时间；自动写入记录带"一键撤回"，撤回即停用该
 * 记录并阻止同一事实再次自动写入。点击即标记已读；失败可安全重试，
 * 状态只在成功后收敛。
 */
export function ProfileNotificationList() {
  const [notifications, setNotifications] = useState<ProfileNotification[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [recallingId, setRecallingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setNotifications(await listProfileNotifications());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "通知加载失败，请稍后重试。");
      setNotifications(null);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const unreadCount = (notifications || []).filter(
    (notification) => notification.read_at === null
  ).length;

  const markRead = (notification: ProfileNotification) => {
    if (notification.read_at !== null) return;
    setNotifications((current) =>
      (current || []).map((item) =>
        item.notification_id === notification.notification_id
          ? { ...item, read_at: new Date().toISOString() }
          : item
      )
    );
    markProfileNotificationRead(notification.notification_id).catch(() => {
      // 标记已读失败不阻塞浏览；下次打开仍会显示为未读。
    });
  };

  const recall = async (notification: ProfileNotification) => {
    setError(null);
    setRecallingId(notification.notification_id);
    try {
      const recalled = await recallProfileNotification(notification.notification_id);
      setNotifications((current) =>
        (current || []).map((item) =>
          item.notification_id === recalled.notification_id ? recalled : item
        )
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "撤回失败，请稍后重试。");
    } finally {
      setRecallingId(null);
    }
  };

  if (notifications === null && !error) {
    return (
      <StateBlock
        kind="loading"
        title="正在加载画像通知…"
        description="读取记忆写入与候选提示。"
      />
    );
  }

  if (notifications === null && error) {
    return (
      <StateBlock
        kind="error"
        title="通知加载失败"
        description={error}
        actionLabel="重新加载"
        onAction={() => void load()}
      />
    );
  }

  if (notifications?.length === 0) {
    return (
      <StateBlock
        kind="empty"
        title="暂无画像通知"
        description="明确说“记住…”、许可内自动写入或生成候选时，会在这里通知你。"
      />
    );
  }

  return (
    <div className={styles.notificationList} data-testid="profile-notifications">
      <p className={styles.notificationHeader}>
        {unreadCount > 0 ? `${unreadCount} 条未读` : "已全部阅读"}
      </p>
      {error && (
        <p role="alert" className={styles.errorText}>
          {error}
        </p>
      )}
      {notifications?.map((notification) => {
        const meta = KIND_META[notification.kind] ?? KIND_META.intent_recorded;
        const recalled = notification.recalled_at !== null;
        return (
          <section
            key={notification.notification_id}
            className={`${styles.notificationItem} ${
              notification.read_at === null ? styles.notificationItemUnread : ""
            }`}
            data-tone={meta.tone}
            data-testid="profile-notification-item"
          >
            <div className={styles.notificationItemHeader}>
              <span className={styles.notificationKind}>{meta.label}</span>
              <span className={styles.notificationTime}>
                {relativeTime(notification.created_at)}
              </span>
            </div>
            <h3 className={styles.notificationTitle}>{notification.title}</h3>
            <p className={styles.notificationMessage}>{notification.message}</p>
            {notification.source_text && (
              <p className={styles.notificationSource}>
                来源消息：“{notification.source_text}”
              </p>
            )}
            <div className={styles.notificationItemActions}>
              {notification.recallable &&
                (recalled ? (
                  <span role="status" className={styles.notificationRecalled}>
                    已撤回
                  </span>
                ) : (
                  <Button
                    variant="secondary"
                    size="sm"
                    isLoading={recallingId === notification.notification_id}
                    disabled={recallingId !== null}
                    onClick={() => void recall(notification)}
                  >
                    一键撤回
                  </Button>
                ))}
              {notification.read_at === null && (
                <button
                  type="button"
                  className={styles.notificationReadButton}
                  onClick={() => markRead(notification)}
                >
                  <Icon name="check" size={16} />
                  标记已读
                </button>
              )}
            </div>
          </section>
        );
      })}
    </div>
  );
}
