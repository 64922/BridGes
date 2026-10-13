import type { Account, AccountProfileUpdate, AuthResponse, CommuteMapConfig, CredentialSettings, CredentialStatus, DeviceAccountsResponse, DeviceLogoutResponse, HealthProjection, ModelSettings, SessionResponse } from "./types";
import { API_BASE, ApiError, deviceOperationHeaders, parseApiError } from "./core";

/**
 * 会话相关错误统一分类（各组件共用，删除逐处自写的 reauth/401 判断）：
 * - ``reauth``：敏感操作需近期密码确认（403 reauth_required）；
 * - ``session``：会话过期（401）；
 * - ``other``：其余错误（保留原错误处理）。
 */
export function classifyApiError(error: unknown): "reauth" | "session" | "other" {
  if (error instanceof ApiError) {
    if (error.code === "reauth_required") return "reauth";
    if (error.status === 401) return "session";
  }
  return "other";
}

export async function fetchHealthSummary(options?: { signal?: AbortSignal }): Promise<HealthProjection> {
  const res = await fetch(`${API_BASE}/health`, {
    cache: "no-store",
    signal: options?.signal,
  });
  if (!res.ok) {
    throw new Error(`Health fetch failed: ${res.status}`);
  }
  return res.json();
}

export async function fetchSession(): Promise<SessionResponse> {
  const res = await fetch(`${API_BASE}/auth/session`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) {
    throw await parseApiError(res);
  }
  return res.json();
}

/** 查询当前安装的搜索与地图凭据状态，不返回任何密钥正文。 */
export async function fetchCredentialSettings(): Promise<CredentialSettings> {
  const res = await fetch(`${API_BASE}/settings/credentials`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/**
 * 浏览器地图运行时配置（V2 Issue 12）：只含 JS API Key 与同源代理路径。
 * 安全密钥永不下发，地图数据服务请求由后端代理追加。
 */
export async function fetchCommuteMapConfig(): Promise<CommuteMapConfig> {
  const res = await fetch(`${API_BASE}/commute/map-config`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/**
 * 把后端下发的代理路径拼成高德 JS API 的 ``serviceHost``（绝对地址）。
 *
 * 后端只知道自己在 API 前缀下的位置，浏览器侧要按当前 API 基地址解析：
 * 默认同源 ``/api`` 时结果是当前源；``NEXT_PUBLIC_API_BASE_URL`` 为绝对地址
 * （另一端口/域名的 API）时也要指向那一侧，否则地图数据请求会打到网页源。
 */
export function commuteMapServiceHost(serviceHostPath: string): string {
  const base = new URL(`${API_BASE}/`, window.location.origin);
  return new URL(serviceHostPath.replace(/^\/+/, ""), base).toString().replace(/\/$/, "");
}

/** 验证并保存 Tavily API Key；成功后清空输入框，不返回密钥。 */
export async function replaceTavilyCredential(apiKey: string): Promise<CredentialStatus> {
  const res = await fetch(`${API_BASE}/settings/credentials/tavily`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ api_key: apiKey }),
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/** 验证并保存高德 Web Service Key；成功后清空输入框，不返回密钥。 */
export async function replaceAmapWebServiceCredential(
  apiKey: string
): Promise<CredentialStatus> {
  const res = await fetch(`${API_BASE}/settings/credentials/amap/web-service`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ api_key: apiKey }),
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/** 验证 JS API Key 后与安全码作为一个服务端凭据原子保存。 */
export async function replaceAmapBrowserMapCredential(
  apiKey: string,
  securityJsCode: string
): Promise<CredentialStatus> {
  const res = await fetch(`${API_BASE}/settings/credentials/amap/browser-map`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ api_key: apiKey, security_js_code: securityJsCode }),
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/** 验证并保存全局 Qwen API Key；成功后清空输入框，不返回密钥。 */
export async function replaceQwenCredential(apiKey: string): Promise<CredentialStatus> {
  const res = await fetch(`${API_BASE}/settings/credentials/qwen`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ api_key: apiKey }),
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/** 读取当前生效的主模型运行配置与最近一次验证结论（不含任何密钥）。 */
export async function fetchModelSettings(): Promise<ModelSettings> {
  const res = await fetch(`${API_BASE}/settings/models`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/**
 * 验证并原子激活手填的主模型 ID；失败保留原配置。
 *
 * 可选地同时提交候选密钥（工单 01）：旧密钥失效、新密钥只覆盖另一批模型时，
 * 同一次操作就能完成「换密钥 + 换主模型」，不必在两张卡之间来回切换。
 */
export async function replaceModelConfiguration(
  modelId: string,
  apiKey?: string
): Promise<ModelSettings> {
  const res = await fetch(`${API_BASE}/settings/models`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(
      apiKey ? { model_id: modelId, api_key: apiKey } : { model_id: modelId }
    ),
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function login(identifier: string, password: string): Promise<AuthResponse> {
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ identifier, password }),
  });
  if (!res.ok) {
    throw await parseApiError(res);
  }
  return res.json();
}

export async function listDeviceAccounts(): Promise<DeviceAccountsResponse> {
  const res = await fetch(`${API_BASE}/auth/device/accounts`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function addDeviceAccount(
  identifier: string,
  password: string,
  operationId?: number
): Promise<DeviceAccountsResponse> {
  const res = await fetch(`${API_BASE}/auth/device/accounts/add`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...deviceOperationHeaders(operationId),
    },
    credentials: "same-origin",
    body: JSON.stringify({ identifier, password }),
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function switchDeviceAccount(
  sessionId: string,
  operationId?: number
): Promise<DeviceAccountsResponse> {
  const res = await fetch(`${API_BASE}/auth/device/switch`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...deviceOperationHeaders(operationId),
    },
    credentials: "same-origin",
    body: JSON.stringify({ session_id: sessionId }),
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function reauthenticateDeviceAccount(
  sessionId: string,
  password: string,
  operationId?: number
): Promise<DeviceAccountsResponse> {
  const res = await fetch(`${API_BASE}/auth/device/reauthenticate`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...deviceOperationHeaders(operationId),
    },
    credentials: "same-origin",
    body: JSON.stringify({ session_id: sessionId, password }),
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function logoutCurrentDeviceAccount(
  operationId?: number
): Promise<DeviceLogoutResponse> {
  const res = await fetch(`${API_BASE}/auth/device/logout`, {
    method: "POST",
    headers: deviceOperationHeaders(operationId),
    credentials: "same-origin",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function logoutAllDeviceAccounts(operationId?: number): Promise<void> {
  const res = await fetch(`${API_BASE}/auth/device/logout-all`, {
    method: "POST",
    headers: deviceOperationHeaders(operationId),
    credentials: "same-origin",
  });
  if (!res.ok) throw await parseApiError(res);
}

export async function register(
  username: string,
  qqEmail: string,
  password: string
): Promise<AuthResponse> {
  const res = await fetch(`${API_BASE}/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ username, qq_email: qqEmail, password }),
  });
  if (!res.ok) {
    throw await parseApiError(res);
  }
  return res.json();
}

export async function logout(): Promise<void> {
  const res = await fetch(`${API_BASE}/auth/logout`, {
    method: "POST",
    credentials: "same-origin",
  });
  if (!res.ok) {
    throw await parseApiError(res);
  }
}

export async function updateProfile(update: AccountProfileUpdate): Promise<Account> {
  const res = await fetch(`${API_BASE}/auth/profile`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(update),
  });
  if (!res.ok) {
    throw await parseApiError(res);
  }
  return res.json();
}

export async function uploadAvatar(file: File): Promise<Account> {
  const res = await fetch(`${API_BASE}/auth/profile/avatar`, {
    method: "PUT",
    headers: { "Content-Type": file.type },
    credentials: "same-origin",
    body: file,
  });
  if (!res.ok) {
    throw await parseApiError(res);
  }
  return res.json();
}

export async function reauthenticate(password: string): Promise<void> {
  const res = await fetch(`${API_BASE}/auth/reauthenticate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ password }),
  });
  if (!res.ok) {
    throw await parseApiError(res);
  }
}
