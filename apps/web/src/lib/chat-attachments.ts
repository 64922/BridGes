import type { IconName } from "@/components/design-system/Icon";

/**
 * V2 Issue 06：聊天附件的客户端事实（类型、体积、解析状态文案）。
 *
 * 与服务端 ``bridges.chat.attachments`` 保持同一份事实：支持的照片与文件
 * 类型、10 MB 上限、解析状态取值。客户端只做提前拦截与展示，最终判定
 * 一律以服务端内容嗅探与摄取投影为准。
 */

/** 照片媒体类型（本轮以多模态图片部件直读）。 */
export const CHAT_PHOTO_MEDIA_TYPES: readonly string[] = [
  "image/png",
  "image/jpeg",
  "image/gif",
  "image/webp",
];

/**
 * 可解析文件媒体类型（本轮经解析、分块与检索引用）。
 * 与 ``ingestion.SUPPORTED_MEDIA_TYPES`` 一致：CSV/XLSX/PPTX 等不在其中。
 */
export const CHAT_FILE_MEDIA_TYPES: readonly string[] = [
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "text/plain",
  "text/markdown",
];

const PHOTO_MEDIA_TYPE_SET = new Set(CHAT_PHOTO_MEDIA_TYPES);

/** 允许选择的扩展名（照片 + 可解析文件；不接受知识库之外的表格/演示类型）。 */
export const CHAT_ATTACHMENT_EXTENSIONS: readonly string[] = [
  ".png",
  ".jpg",
  ".jpeg",
  ".gif",
  ".webp",
  ".pdf",
  ".docx",
  ".txt",
  ".md",
  ".markdown",
];

/** 文件选择框的 accept 取值（照片 MIME + 文件扩展名）。 */
export const CHAT_ATTACHMENT_ACCEPT = [
  ...CHAT_PHOTO_MEDIA_TYPES,
  ...CHAT_ATTACHMENT_EXTENSIONS,
].join(",");

export const CHAT_ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024;
export const CHAT_ATTACHMENT_MAX_COUNT = 10;

// 中文原因与服务端同一份措辞：无论客户端还是服务端拦截，用户看到同一句话。
export const CHAT_UNSUPPORTED_TYPE_MESSAGE =
  "暂不支持该文件类型，聊天附件目前支持 PDF、DOCX、TXT、Markdown 与 PNG、JPEG、GIF、WebP 图片。";
export const CHAT_TOO_LARGE_MESSAGE = "文件超过 10 MB 大小限制，请压缩后重试。";
export const CHAT_DUPLICATE_MESSAGE = "同一附件不能重复添加。";
export const CHAT_TOO_MANY_MESSAGE = "一条消息最多添加 10 个附件。";

const PHOTO_TYPE_LABELS: Record<string, string> = {
  "image/png": "PNG",
  "image/jpeg": "JPEG",
  "image/gif": "GIF",
  "image/webp": "WebP",
};

const FILE_TYPE_LABELS: Record<string, string> = {
  "application/pdf": "PDF",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "DOCX",
  "text/plain": "TXT",
  "text/markdown": "Markdown",
};

/** 是否按照片渲染（照片展示缩略图，文件展示文档卡片）。 */
export function isPhotoAttachment(mediaType: string): boolean {
  return PHOTO_MEDIA_TYPE_SET.has(mediaType);
}

/** 候选文件是否在客户端白名单内（按扩展名，服务端仍按内容判定）。 */
export function isPickedFileAcceptable(filename: string): boolean {
  const extension = filename.includes(".")
    ? `.${filename.split(".").pop()!.toLowerCase()}`
    : "";
  return CHAT_ATTACHMENT_EXTENSIONS.includes(extension);
}

/** 类型中文标签（照片按 MIME，文件按 MIME；未知取值回落到扩展名）。 */
export function attachmentTypeLabel(mediaType: string, filename: string): string {
  const known = PHOTO_TYPE_LABELS[mediaType] ?? FILE_TYPE_LABELS[mediaType];
  if (known) return known;
  const extension = filename.includes(".") ? filename.split(".").pop()! : "";
  return extension ? extension.toUpperCase() : "文件";
}

/** 文件大小中文表述（KB/MB，一位小数）。 */
export function formatAttachmentSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}

/** 附件卡片图标（照片与文件用不同图标区分，不只靠文字）。 */
export function attachmentIcon(mediaType: string): IconName {
  return isPhotoAttachment(mediaType) ? "imagePicture" : "documentPage";
}

/**
 * 聊天上下文里的摄取状态中文表述（覆盖知识库用词）。
 *
 * 与 ``IngestionStatusChip`` 的默认文案同义，但更贴合「本轮能不能引用
 * 这份文件」：``ready`` 表示内容已可检索引用，``empty`` 表示解析不出正文
 * （例如扫描件），``error`` 表示解析失败。
 */
export const CHAT_INGESTION_LABELS: Record<string, string> = {
  queued: "排队解析中",
  processing: "解析中…",
  ready: "已解析，可引用",
  empty: "无法识别正文",
  error: "解析失败",
  recovery: "解析中断，恢复中",
  none: "未解析",
  loading: "加载中…",
  permission: "无访问权限",
};

/** 状态是否已终态（终态不再轮询刷新；none 表示本类型不参与解析）。 */
export function isIngestionSettled(status: string): boolean {
  return !["queued", "processing", "recovery", "loading"].includes(status);
}
