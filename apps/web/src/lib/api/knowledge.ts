import type { HealthStatus, KnowledgeBaseMaterialProjection, SearchResponse, UnifiedSearchParams } from "./types";
import { API_BASE, parseApiError, uploadRawBytes } from "./core";

// ---------------------------------------------------------------------------
// Issue 18：全局知识库
// ---------------------------------------------------------------------------

/** 当前账户的全局知识库材料列表（最新在前）。 */
export async function listKnowledgeBaseMaterials(
  options?: { signal?: AbortSignal }
): Promise<KnowledgeBaseMaterialProjection[]> {
  const res = await fetch(`${API_BASE}/knowledge-base/materials`, {
    credentials: "same-origin",
    cache: "no-store",
    signal: options?.signal,
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/**
 * 上传一份知识库材料：与对话附件共用 uploadRawBytes；
 * 201 新建 / 200 幂等复用，均按成功处理。
 */
export function uploadKnowledgeBaseMaterial(
  file: File,
  uploadId: string,
  onProgress?: (loaded: number, total: number) => void,
  signal?: AbortSignal
): Promise<KnowledgeBaseMaterialProjection> {
  return uploadRawBytes<KnowledgeBaseMaterialProjection>(
    `${API_BASE}/knowledge-base/materials`,
    file,
    uploadId,
    onProgress,
    signal
  );
}

/** 下载材料原始文件（与对话附件下载同一模式）。 */
export async function downloadKnowledgeBaseMaterial(
  objectId: string,
  originalFilename: string
): Promise<void> {
  const res = await fetch(
    `${API_BASE}/knowledge-base/materials/${encodeURIComponent(objectId)}/download`,
    { credentials: "same-origin" }
  );
  if (!res.ok) throw await parseApiError(res);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = originalFilename;
  anchor.click();
  URL.revokeObjectURL(url);
}

/** 把失败/恢复中的材料重新入队；幂等返回当前投影。 */
export async function retryKnowledgeBaseMaterial(
  objectId: string
): Promise<KnowledgeBaseMaterialProjection> {
  const res = await fetch(
    `${API_BASE}/knowledge-base/materials/${encodeURIComponent(objectId)}/retry`,
    { method: "POST", credentials: "same-origin" }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/** 以当前索引配置重建该材料的索引（处理中返回 409 material_processing）。 */
export async function rebuildKnowledgeBaseMaterial(
  objectId: string
): Promise<KnowledgeBaseMaterialProjection> {
  const res = await fetch(
    `${API_BASE}/knowledge-base/materials/${encodeURIComponent(objectId)}/rebuild`,
    { method: "POST", credentials: "same-origin" }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/** 删除材料本体、解析分块与派生索引（处理中返回 409 material_processing）。 */
export async function deleteKnowledgeBaseMaterial(objectId: string): Promise<void> {
  const res = await fetch(
    `${API_BASE}/knowledge-base/materials/${encodeURIComponent(objectId)}`,
    { method: "DELETE", credentials: "same-origin" }
  );
  if (!res.ok) throw await parseApiError(res);
}

/**
 * 统一桌面搜索（GET /search）：``types`` 以重复查询参数传递，
 * 错误统一走 parseApiError（401/403 由调用方经 classifyApiError 映射）。
 */
export async function searchUnified(params: UnifiedSearchParams): Promise<SearchResponse> {
  const query = new URLSearchParams();
  query.set("q", params.q);
  for (const type of params.types ?? []) {
    query.append("types", type);
  }
  if (params.from) query.set("from", params.from);
  if (params.to) query.set("to", params.to);
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  const res = await fetch(`${API_BASE}/search?${query.toString()}`, {
    credentials: "same-origin",
    cache: "no-store",
    signal: params.signal,
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/**
 * 以 Blob 读取知识库材料原始字节（不触发浏览器下载）：
 * 图片搜索结果的就地预览用它构造 object URL。
 */
export async function fetchKnowledgeBaseMaterialBlob(objectId: string): Promise<Blob> {
  const res = await fetch(
    `${API_BASE}/knowledge-base/materials/${encodeURIComponent(objectId)}/download`,
    { credentials: "same-origin" }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.blob();
}

export function statusText(status: HealthStatus): string {
  switch (status) {
    case "pass":
      return "正常";
    case "fail":
      return "异常";
    case "unknown":
      return "未知";
    default:
      return String(status);
  }
}
