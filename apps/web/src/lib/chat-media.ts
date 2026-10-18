/**
 * 右侧媒体详情的选择身份：固定到具体会话、消息、任务与资产。
 *
 * 不使用“当前选中的媒体”之类全局状态；切换会话、关闭面板或账户变化
 * 时由页面卸载/选择重置来中止请求与释放预览资源。
 */
export interface ChatMediaSelection {
  conversationId: string;
  messageId: string;
  kind: "image" | "video";
  taskId: string;
  assetId: string;
  resultVersionId: string | null;
}

/** 消息内媒体入口上报的最小目标；页面再补上当前会话身份。 */
export interface ChatMediaTarget {
  kind: "image" | "video";
  taskId: string;
  assetId: string;
  resultVersionId: string | null;
}

export function mediaSelectionKey(selection: ChatMediaSelection): string {
  return [
    selection.conversationId,
    selection.messageId,
    selection.kind,
    selection.taskId,
    selection.assetId,
    selection.resultVersionId ?? "",
  ].join(":");
}
