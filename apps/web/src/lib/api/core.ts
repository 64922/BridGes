export const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "/api";

export function deviceOperationHeaders(operationId?: number): Record<string, string> {
  return operationId
    ? { "X-Bridges-Account-Operation": String(operationId) }
    : {};
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function errorFromDetail(status: number, detail: unknown): ApiError {
  // detail 形状统一折叠：AuthError {error, message}、纯 {message}、
  // 字符串或校验错误数组 → ApiError（含稳定错误码）。
  if (typeof detail === "string" && detail) {
    return new ApiError(detail, status);
  }
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const body = detail as { message?: unknown; error?: unknown };
    const message =
      typeof body.message === "string" && body.message
        ? body.message
        : `请求失败（${status}）`;
    return new ApiError(message, status, typeof body.error === "string" ? body.error : undefined);
  }
  return new ApiError(`请求失败（${status}）`, status);
}

/** 统一错误解析：认证、领域包与附件路由共用同一语义（单一实现）。 */
export async function parseApiError(res: Response): Promise<ApiError> {
  try {
    const body: unknown = await res.json();
    if (body && typeof body === "object" && "detail" in body) {
      return errorFromDetail(res.status, (body as { detail?: unknown }).detail);
    }
    return errorFromDetail(res.status, body);
  } catch {
    return new ApiError(`请求失败（${res.status}）`, res.status);
  }
}

export function attachmentApiError(status: number, body: unknown): ApiError {
  // XHR 路径复用统一错误折叠语义（detail 或顶层 {message} 均可解析）
  if (body && typeof body === "object" && "detail" in body) {
    return errorFromDetail(status, (body as { detail?: unknown }).detail);
  }
  return errorFromDetail(status, body);
}

/**
 * 原始字节上传共享实现：服务端负责内容嗅探，XHR 只用于提供可靠的
 * 上传进度与取消。对话附件与知识库材料上传仅 URL 与返回类型不同。
 */
export function uploadRawBytes<T>(
  url: string,
  file: File,
  uploadId: string,
  onProgress?: (loaded: number, total: number) => void,
  signal?: AbortSignal
): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    let settled = false;
    const cleanup = () => signal?.removeEventListener("abort", abort);
    const fail = (error: Error) => {
      if (settled) return;
      settled = true;
      cleanup();
      reject(error);
    };
    const abort = () => xhr.abort();

    if (signal?.aborted) {
      fail(new DOMException("上传已取消。", "AbortError"));
      return;
    }
    signal?.addEventListener("abort", abort, { once: true });
    xhr.open("POST", url);
    xhr.withCredentials = true;
    xhr.setRequestHeader("Content-Type", file.type || "application/octet-stream");
    xhr.setRequestHeader("X-Bridges-Filename", encodeURIComponent(file.name));
    xhr.setRequestHeader("X-Bridges-Upload-Id", uploadId);
    xhr.upload.onprogress = (event) => {
      onProgress?.(event.loaded, event.lengthComputable ? event.total : file.size);
    };
    xhr.onload = () => {
      cleanup();
      let body: unknown;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        body = undefined;
      }
      if (xhr.status < 200 || xhr.status >= 300) {
        fail(attachmentApiError(xhr.status, body));
        return;
      }
      settled = true;
      onProgress?.(file.size, file.size);
      resolve(body as T);
    };
    xhr.onerror = () => fail(new ApiError("上传失败，请检查网络后重试。", 0));
    xhr.onabort = () => fail(new DOMException("上传已取消。", "AbortError"));
    xhr.send(file);
  });
}
