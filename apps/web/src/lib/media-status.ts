import type {
  ImageTaskProjection,
  ImageTaskStatus,
  VideoTaskProjection,
  VideoTaskStatus,
} from "@/lib/api";

/**
 * 媒体任务状态的中文呈现。
 *
 * 任务表是权威、消息投影是快照：页面只按服务端真实状态展示，
 * 不根据“已完成”推断资产可访问，也不把已退役能力描述成可恢复。
 */

export const IMAGE_STATUS_LABELS: Record<ImageTaskStatus, string> = {
  queued: "排队中",
  running: "生成中",
  recovery: "恢复中",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已停止",
};

export const VIDEO_STATUS_LABELS: Record<VideoTaskStatus, string> = {
  queued: "排队中",
  submitting: "提交中",
  generating: "生成中",
  recovery: "恢复中",
  succeeded: "已完成",
  failed: "失败",
  cancelling: "正在停止",
  cancelled: "已停止",
};

export const IMAGE_ACTIVE_STATUSES: readonly ImageTaskStatus[] = [
  "queued",
  "running",
  "recovery",
];

export const VIDEO_ACTIVE_STATUSES: readonly VideoTaskStatus[] = [
  "queued",
  "submitting",
  "generating",
  "recovery",
  "cancelling",
];

export function isImageTaskActive(task: ImageTaskProjection): boolean {
  return IMAGE_ACTIVE_STATUSES.includes(task.status);
}

export function isVideoTaskActive(task: VideoTaskProjection): boolean {
  return VIDEO_ACTIVE_STATUSES.includes(task.status);
}

/** 任务本身仍可继续在后台推进时的真实说明。 */
export function activeMediaHint(kind: "image" | "video"): string {
  return kind === "image"
    ? "图片任务在后台独立进行；可离开本页，返回会话后会继续刷新真实状态。"
    : "视频任务在后台独立进行；可离开本页，返回会话后会继续刷新真实状态。";
}
