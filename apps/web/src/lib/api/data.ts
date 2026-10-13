import type { AccountDeletionProjection, ExportPreviewProjection, RestorePreview } from "./types";
import { API_BASE, parseApiError } from "./core";

/** 返回当前账户导出范围与预计大小（确认前可见，不含数据正文）。 */
export async function fetchExportPreview(): Promise<ExportPreviewProjection> {
  const res = await fetch(`${API_BASE}/data/export-preview`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/** 触发浏览器下载响应字节（解析 Content-Disposition 文件名）。 */
function downloadBlob(blob: Blob, disposition: string, fallback: string): void {
  const filename =
    disposition.match(/filename="([^"]+)"/)?.[1] ?? fallback;
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

/** 生成当前账户导出 JSON 并触发浏览器下载（敏感操作，需近期密码再认证）。 */
export async function exportAccountData(): Promise<void> {
  const res = await fetch(`${API_BASE}/data/export`, {
    method: "POST",
    credentials: "same-origin",
  });
  if (!res.ok) throw await parseApiError(res);
  downloadBlob(
    await res.blob(),
    res.headers.get("Content-Disposition") ?? "",
    "bridges-export.json"
  );
}

/** 删除当前账户及其全部本地数据（强确认 + 近期密码再认证）。 */
export async function deleteAccount(confirmation: string): Promise<void> {
  const res = await fetch(`${API_BASE}/data/account/delete`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirmation }),
  });
  if (!res.ok) throw await parseApiError(res);
}

/** 返回当前账户删除状态（部分失败时可观察、可重试）。 */
export async function fetchDeletionStatus(): Promise<AccountDeletionProjection> {
  const res = await fetch(`${API_BASE}/data/account/delete-status`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/** 重试失败的账户删除清理（敏感操作，需近期密码再认证）。 */
export async function retryDeletion(): Promise<AccountDeletionProjection> {
  const res = await fetch(`${API_BASE}/data/account/delete/retry`, {
    method: "POST",
    credentials: "same-origin",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/** 创建本地加密备份并触发浏览器下载（敏感操作，需近期密码再认证）。 */
export async function createBackup(passphrase: string): Promise<void> {
  const body = new FormData();
  body.append("passphrase", passphrase);
  const res = await fetch(`${API_BASE}/data/backups`, {
    method: "POST",
    credentials: "same-origin",
    body,
  });
  if (!res.ok) throw await parseApiError(res);
  downloadBlob(
    await res.blob(),
    res.headers.get("Content-Disposition") ?? "",
    "bridges-backup.bridgesbackup"
  );
}

/** 预检并恢复备份（强确认 + 近期密码再认证）；失败不破坏现有数据。 */
export async function restoreBackup(
  file: File,
  passphrase: string,
  confirmation: string
): Promise<RestorePreview> {
  const body = new FormData();
  body.append("file", file);
  body.append("passphrase", passphrase);
  body.append("confirmation", confirmation);
  const res = await fetch(`${API_BASE}/data/restore`, {
    method: "POST",
    credentials: "same-origin",
    body,
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}
