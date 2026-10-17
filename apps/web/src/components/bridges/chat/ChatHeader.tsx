import {
  CHAT_MODE_LABELS,
  type ChatMode,
} from "@/components/bridges/ModeToggle";

import styles from "./ChatHeader.module.css";

interface ChatHeaderProps {
  title: string;
  /** 已有会话的固定模式；新对话尚未锁定时不显示。 */
  mode?: ChatMode | null;
}

/**
 * 对话页顶部栏：左侧是当前会话标题，右侧是服务端锁定的模式。
 *
 * 新对话只在此处显示标题，模式入口在输入区；首轮锁定后模式只读展示，
 * 刷新或重新打开仍由权威历史决定。
 */
export function ChatHeader({ title, mode }: ChatHeaderProps) {
  return (
    <header className={styles.chatHeader} data-testid="chat-header">
      <h1 className={styles.chatHeaderTitle}>{title}</h1>
      {mode && (
        <span className={styles.chatHeaderMode} data-testid="conversation-mode">
          当前模式：{CHAT_MODE_LABELS[mode]}
        </span>
      )}
    </header>
  );
}
