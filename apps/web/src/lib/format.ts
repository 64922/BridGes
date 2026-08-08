/** 时间与大小的中文格式化（学习项目页与知识库页共用同一呈现约定）。 */

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** 附件类型的中文短标签（聊天附件与技能弹窗共用；未知类型按文件名后缀兜底）。 */
export function formatFileType(mediaType: string, filename = ""): string {
  if (mediaType.startsWith("image/")) return "图片";
  if (mediaType === "application/pdf") return "PDF";
  if (mediaType === "application/vnd.openxmlformats-officedocument.wordprocessingml.document") {
    return "Word";
  }
  if (mediaType === "text/plain" || mediaType === "text/markdown") return "文本";
  if (mediaType === "text/csv") return "CSV";
  if (mediaType === "application/json") return "JSON";
  const extension = filename.includes(".") ? filename.split(".").pop()!.toUpperCase() : "";
  return extension ? `${extension} 文件` : "文件";
}

export function formatAbsoluteTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("zh-CN", { hour12: false });
}

/** 相对时间（7 天内），绝对时间通过 title 属性完整呈现。 */
export function formatRelativeTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const diffMs = Date.now() - date.getTime();
  if (diffMs < 60_000) return "刚刚";
  if (diffMs < 3_600_000) return `${Math.floor(diffMs / 60_000)} 分钟前`;
  if (diffMs < 86_400_000) return `${Math.floor(diffMs / 3_600_000)} 小时前`;
  if (diffMs < 7 * 86_400_000) return `${Math.floor(diffMs / 86_400_000)} 天前`;
  return formatAbsoluteTime(iso);
}
