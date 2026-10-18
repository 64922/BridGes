import type { DictationProjection, ImageAssetProjection, ImageTaskProjection, VideoAssetProjection, VideoTaskProjection } from "./types";
import { API_BASE, parseApiError } from "./core";

// ---------------------------------------------------------------------------
// Issue 30：听写与单条回答朗读
// ---------------------------------------------------------------------------

/** 提交一段完整录音到固定 ASR 快照，返回可编辑转写文本（不自动发送）。 */
export async function transcribeDictation(
  conversationId: string,
  audioBlob: Blob,
  durationSeconds: number,
  signal?: AbortSignal
): Promise<DictationProjection> {
  const res = await fetch(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/dictation`,
    {
      method: "POST",
      headers: {
        "Content-Type": audioBlob.type || "audio/webm",
        "X-Bridges-Audio-Duration": String(durationSeconds),
      },
      credentials: "same-origin",
      body: audioBlob,
      signal,
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/** 查询任务投影（刷新/重登/重启后恢复任务状态；呈现态含 recovery）。 */
export async function getImageTask(
  conversationId: string,
  taskId: string
): Promise<ImageTaskProjection> {
  const res = await fetch(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/image-tasks/${encodeURIComponent(taskId)}`,
    { credentials: "same-origin" }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/** 查询资产投影：版本链、替代文本与当前版本指针。 */
export async function getImageAsset(
  conversationId: string,
  assetId: string
): Promise<ImageAssetProjection> {
  const res = await fetch(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/image-assets/${encodeURIComponent(assetId)}`,
    { credentials: "same-origin" }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/**
 * 指定版本图片的同源地址（经账户授权校验 + 私有缓存头流式返回）。
 * ``download=1`` 附加附件下载头；默认内联显示。
 */
export function imageVersionUrl(
  conversationId: string,
  assetId: string,
  versionId: string,
  download = false
): string {
  const base = `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/image-assets/${encodeURIComponent(assetId)}/versions/${encodeURIComponent(versionId)}/image`;
  return download ? `${base}?download=1` : base;
}

/**
 * 读取指定版本图片字节（账户授权校验由服务端完成）。
 * 供右侧预览以 object URL 展示，调用方负责中止请求与释放 URL。
 */
export async function fetchImageVersionContent(
  conversationId: string,
  assetId: string,
  versionId: string,
  signal?: AbortSignal
): Promise<Response> {
  const res = await fetch(
    imageVersionUrl(conversationId, assetId, versionId),
    { credentials: "same-origin", signal }
  );
  if (!res.ok) throw await parseApiError(res);
  return res;
}

// ---------------------------------------------------------------------------
// Issue 32：文生视频（任务操作面 + 资产操作面；Wan 固定绑定）
// ---------------------------------------------------------------------------

/** 查询任务投影（刷新/重登/重启后恢复任务状态；呈现态含 recovery/cancelling）。 */
export async function getVideoTask(
  conversationId: string,
  taskId: string
): Promise<VideoTaskProjection> {
  const res = await fetch(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/video-tasks/${encodeURIComponent(taskId)}`,
    { credentials: "same-origin" }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/** 查询资产投影：可访问文字说明、提示、模型、供应商任务标识与时间。 */
export async function getVideoAsset(
  conversationId: string,
  assetId: string
): Promise<VideoAssetProjection> {
  const res = await fetch(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/video-assets/${encodeURIComponent(assetId)}`,
    { credentials: "same-origin" }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/**
 * 视频的同源地址（经账户授权校验 + 私有缓存头流式返回）。
 * ``download=1`` 附加附件下载头；默认内联预览。
 */
export function videoUrl(
  conversationId: string,
  assetId: string,
  download = false
): string {
  const base = `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/video-assets/${encodeURIComponent(assetId)}/video`;
  return download ? `${base}?download=1` : base;
}

/**
 * 读取视频字节（账户授权校验由服务端完成）。
 * 供右侧预览以 object URL 展示，调用方负责中止请求与释放 URL。
 */
export async function fetchVideoContent(
  conversationId: string,
  assetId: string,
  signal?: AbortSignal
): Promise<Response> {
  const res = await fetch(videoUrl(conversationId, assetId), {
    credentials: "same-origin",
    signal,
  });
  if (!res.ok) throw await parseApiError(res);
  return res;
}
