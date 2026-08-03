"use client";

import type { components } from "@bridges/contracts";

export type HealthProjection = components["schemas"]["HealthProjection"];
export type HealthStatus = components["schemas"]["HealthStatus"];
export type Account = components["schemas"]["Account"];
export type AuthResponse = components["schemas"]["AuthResponse"];
export type SessionResponse = components["schemas"]["SessionResponse"];
export type AuthError = components["schemas"]["AuthError"];
export type AvatarChoice = components["schemas"]["AvatarChoice"];
export type AccountProfileUpdate = components["schemas"]["AccountProfileUpdate"];
export type DeviceAccountProjection = components["schemas"]["DeviceAccountProjection"];
export type DeviceAccountsResponse = components["schemas"]["DeviceAccountsResponse"];
export type DeviceLogoutResponse = components["schemas"]["DeviceLogoutResponse"];
export type KeySettingsProjection = components["schemas"]["KeySettingsProjection"];
export type CapabilityProbeSummary = components["schemas"]["CapabilityProbeSummary"];
export type ChatMessageProjection = components["schemas"]["ChatMessageProjection"];
export type ChatAttachmentProjection = components["schemas"]["ChatAttachmentProjection"];
export type ChatMessageRole = components["schemas"]["ChatMessageRole"];
export type ChatMessageStatus = components["schemas"]["ChatMessageStatus"];
export type ChatConversationProjection = components["schemas"]["ChatConversationProjection"];
export type ChatConversationSummary = components["schemas"]["ChatConversationSummary"];
export type ChatConversationListProjection = components["schemas"]["ChatConversationListProjection"];
export type ChatStopResponse = components["schemas"]["ChatStopResponse"];
export type ChatStreamEvent = components["schemas"]["ChatStreamEvent"];
export type ChatStreamEventKind = components["schemas"]["ChatStreamEventKind"];
export type ChatStreamStartedData = components["schemas"]["ChatStreamStartedData"];
export type ChatStreamDeltaData = components["schemas"]["ChatStreamDeltaData"];
export type ChatStreamErrorData = components["schemas"]["ChatStreamErrorData"];
export type DocumentIngestionProjection = components["schemas"]["DocumentIngestionProjection"];
export type KnowledgeBaseMaterialProjection = components["schemas"]["KnowledgeBaseMaterialProjection"];
export type IngestionStatus = components["schemas"]["IngestionStatus"];
export type IndexStatusProjection = components["schemas"]["IndexStatusProjection"];
export type IndexVersionProjection = components["schemas"]["IndexVersionProjection"];
export type IndexContractProjection = components["schemas"]["IndexContractProjection"];
export type ChatStreamDoneData = components["schemas"]["ChatStreamDoneData"];
export type ChatMode = components["schemas"]["ChatMode"];
export type ChatModeEventProjection = components["schemas"]["ChatModeEventProjection"];
export type ChatModeSwitchResponse = components["schemas"]["ChatModeSwitchResponse"];
export type ChatThinkingSummary = components["schemas"]["ChatThinkingSummary"];
export type Project = components["schemas"]["Project"];
export type ProjectCreateRequest = components["schemas"]["ProjectCreateRequest"];
export type ProjectListProjection = components["schemas"]["ProjectListProjection"];
export type ProjectSummary = components["schemas"]["ProjectSummary"];
export type ProjectError = components["schemas"]["ProjectError"];
export type WorkbenchPackRecord = components["schemas"]["WorkbenchPackRecord"];
export type ReviewAttestation = components["schemas"]["ReviewAttestation"];
export type SemanticDiff = components["schemas"]["SemanticDiff"];
export type SemanticDiffEntry = components["schemas"]["SemanticDiffEntry"];
export type GrayReleaseCandidate = components["schemas"]["GrayReleaseCandidate"];
export type PackRelease = components["schemas"]["PackRelease"];
export type QualificationRecord = components["schemas"]["QualificationRecord"];
export type ConflictOfInterestDeclaration = components["schemas"]["ConflictOfInterestDeclaration"];
export type PackInvalidationEvent = components["schemas"]["PackInvalidationEvent"];
export type PackInvalidationStage = components["schemas"]["PackInvalidationStage"];
export type PackInvalidationTrigger = components["schemas"]["PackInvalidationTrigger"];
export type PackImpactSet = components["schemas"]["PackImpactSet"];
export type PackImpactItem = components["schemas"]["PackImpactItem"];
export type PackImpactCategory = components["schemas"]["PackImpactCategory"];
export type RevocationEvent = components["schemas"]["RevocationEvent"];
export type RevalidationReport = components["schemas"]["RevalidationReport"];
export type PackRollbackRecord = components["schemas"]["PackRollbackRecord"];
export type PackRollbackStatus = components["schemas"]["PackRollbackStatus"];

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "/api";

function deviceOperationHeaders(operationId?: number): Record<string, string> {
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

function errorFromDetail(status: number, detail: unknown): ApiError {
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
async function parseApiError(res: Response): Promise<ApiError> {
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

export async function fetchKeySettings(): Promise<KeySettingsProjection> {
  const res = await fetch(`${API_BASE}/auth/key-settings`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) {
    throw await parseApiError(res);
  }
  return res.json();
}

export async function saveKeySettings(key: string): Promise<KeySettingsProjection> {
  const res = await fetch(`${API_BASE}/auth/key-settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ key }),
  });
  if (!res.ok) {
    throw await parseApiError(res);
  }
  return res.json();
}

export async function deleteKeySettings(): Promise<KeySettingsProjection> {
  const res = await fetch(`${API_BASE}/auth/key-settings`, {
    method: "DELETE",
    credentials: "same-origin",
  });
  if (!res.ok) {
    throw await parseApiError(res);
  }
  return res.json();
}

export async function retryCapabilityProbe(
  capabilityId: string
): Promise<KeySettingsProjection> {
  const res = await fetch(
    `${API_BASE}/auth/key-settings/probes/${encodeURIComponent(capabilityId)}/retry`,
    { method: "POST", credentials: "same-origin" }
  );
  if (!res.ok) {
    throw await parseApiError(res);
  }
  return res.json();
}

export async function probeAllCapabilities(): Promise<KeySettingsProjection> {
  const res = await fetch(`${API_BASE}/auth/key-settings/probes`, {
    method: "POST",
    credentials: "same-origin",
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

export async function createProject(request: ProjectCreateRequest): Promise<Project> {
  const res = await fetch(`${API_BASE}/projects`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(request),
  });
  if (!res.ok) {
    throw await parseApiError(res);
  }
  return res.json();
}

export async function listProjects(): Promise<ProjectListProjection> {
  const res = await fetch(`${API_BASE}/projects`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) {
    throw await parseApiError(res);
  }
  return res.json();
}

export async function getProject(projectId: string): Promise<Project> {
  const res = await fetch(`${API_BASE}/projects/${encodeURIComponent(projectId)}`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) {
    throw await parseApiError(res);
  }
  return res.json();
}


export async function listWorkbenchPacks(): Promise<WorkbenchPackRecord[]> {
  const res = await fetch(`${API_BASE}/domain-packs/workbench`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function registerWorkbenchPack(
  packId: string,
  version: string
): Promise<WorkbenchPackRecord> {
  const res = await fetch(`${API_BASE}/domain-packs/workbench/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ pack_id: packId, version }),
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function getWorkbenchRecord(
  packId: string,
  version: string
): Promise<WorkbenchPackRecord> {
  const res = await fetch(
    `${API_BASE}/domain-packs/workbench/${encodeURIComponent(packId)}/${encodeURIComponent(version)}`,
    { credentials: "same-origin", cache: "no-store" }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function getSemanticDiff(
  packId: string,
  version: string
): Promise<SemanticDiff> {
  const res = await fetch(
    `${API_BASE}/domain-packs/workbench/${encodeURIComponent(packId)}/${encodeURIComponent(version)}/semantic-diff`,
    { credentials: "same-origin", cache: "no-store" }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function submitContentSignature(
  packId: string,
  version: string,
  opinion: string
): Promise<ReviewAttestation> {
  const res = await fetch(
    `${API_BASE}/domain-packs/workbench/${encodeURIComponent(packId)}/${encodeURIComponent(version)}/content-signature`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ opinion, conclusion: "approve" }),
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function assignReviewer(
  packId: string,
  version: string,
  personId: string
): Promise<WorkbenchPackRecord> {
  const res = await fetch(
    `${API_BASE}/domain-packs/workbench/${encodeURIComponent(packId)}/${encodeURIComponent(version)}/reviewer`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ person_id: personId }),
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function assignReleaser(
  packId: string,
  version: string,
  personId: string
): Promise<WorkbenchPackRecord> {
  const res = await fetch(
    `${API_BASE}/domain-packs/workbench/${encodeURIComponent(packId)}/${encodeURIComponent(version)}/releaser`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ person_id: personId }),
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export type AttestationConclusion = "approve" | "reject" | "needs_changes";

export async function submitIndependentSignature(
  packId: string,
  version: string,
  opinion: string,
  conclusion: AttestationConclusion = "approve"
): Promise<ReviewAttestation> {
  const res = await fetch(
    `${API_BASE}/domain-packs/workbench/${encodeURIComponent(packId)}/${encodeURIComponent(version)}/independent-signature`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ opinion, conclusion }),
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function prepareGrayRelease(
  packId: string,
  version: string
): Promise<GrayReleaseCandidate> {
  const res = await fetch(
    `${API_BASE}/domain-packs/workbench/${encodeURIComponent(packId)}/${encodeURIComponent(version)}/gray-release`,
    { method: "POST", credentials: "same-origin" }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function releasePack(
  packId: string,
  version: string
): Promise<PackRelease> {
  const res = await fetch(
    `${API_BASE}/domain-packs/workbench/${encodeURIComponent(packId)}/${encodeURIComponent(version)}/release`,
    { method: "POST", credentials: "same-origin" }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function declareConflictOfInterest(
  packId: string,
  version: string,
  disclosures: string[]
): Promise<ConflictOfInterestDeclaration> {
  const res = await fetch(
    `${API_BASE}/domain-packs/workbench/${encodeURIComponent(packId)}/${encodeURIComponent(version)}/conflict-of-interest`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ disclosures }),
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export type ConflictDisclosureInput = {
  item_ref: string;
  minority_opinion: string;
  basis?: string[];
  missing_evidence?: string[];
  decision?: string;
};

export async function addConflictDisclosure(
  packId: string,
  version: string,
  input: ConflictDisclosureInput
): Promise<ConflictDisclosure> {
  const res = await fetch(
    `${API_BASE}/domain-packs/workbench/${encodeURIComponent(packId)}/${encodeURIComponent(version)}/conflict-disclosure`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(input),
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export type ConflictDisclosure = {
  disclosure_id: string;
  pack_id: string;
  pack_version: string;
  item_ref: string;
  minority_opinion: string;
  basis?: string[];
  missing_evidence?: string[];
  decision?: string;
  created_at: string;
};

// ---------------------------------------------------------------------------
// T047：失效、撤销、重验证与受信回滚
// ---------------------------------------------------------------------------

export async function registerSecurityAdmin(): Promise<void> {
  const res = await fetch(`${API_BASE}/domain-packs/security-admins`, {
    method: "POST",
    credentials: "same-origin",
  });
  if (!res.ok) throw await parseApiError(res);
}

export async function fetchSecurityAdminStatus(): Promise<{ is_security_admin: boolean }> {
  const res = await fetch(`${API_BASE}/domain-packs/security-admins`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function listInvalidations(packId?: string): Promise<PackInvalidationEvent[]> {
  const query = packId ? `?pack_id=${encodeURIComponent(packId)}` : "";
  const res = await fetch(`${API_BASE}/domain-packs/invalidations${query}`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function getInvalidation(eventId: string): Promise<PackInvalidationEvent> {
  const res = await fetch(`${API_BASE}/domain-packs/invalidations/${encodeURIComponent(eventId)}`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function recordInvalidation(input: {
  pack_id: string;
  version: string;
  trigger: PackInvalidationTrigger;
  reason: string;
  emergency?: boolean;
}): Promise<PackInvalidationEvent> {
  const res = await fetch(`${API_BASE}/domain-packs/invalidations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(input),
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function advanceInvalidation(
  eventId: string,
  toStage: PackInvalidationStage,
  note = ""
): Promise<PackInvalidationEvent> {
  const res = await fetch(
    `${API_BASE}/domain-packs/invalidations/${encodeURIComponent(eventId)}/advance`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ to_stage: toStage, note }),
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function resolveImpact(eventId: string): Promise<PackImpactSet> {
  const res = await fetch(
    `${API_BASE}/domain-packs/invalidations/${encodeURIComponent(eventId)}/resolve-impact`,
    {
      method: "POST",
      credentials: "same-origin",
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function getImpactSet(eventId: string): Promise<PackImpactSet> {
  const res = await fetch(
    `${API_BASE}/domain-packs/invalidations/${encodeURIComponent(eventId)}/impact`,
    {
      credentials: "same-origin",
      cache: "no-store",
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function reportRevalidated(
  eventId: string,
  area: PackImpactCategory,
  refIds: string[],
  failed: string[] = []
): Promise<RevalidationReport> {
  const res = await fetch(
    `${API_BASE}/domain-packs/invalidations/${encodeURIComponent(eventId)}/revalidate`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ area, ref_ids: refIds, failed }),
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function getRevalidationReport(
  eventId: string
): Promise<RevalidationReport | null> {
  const res = await fetch(
    `${API_BASE}/domain-packs/invalidations/${encodeURIComponent(eventId)}/revalidation`,
    {
      credentials: "same-origin",
      cache: "no-store",
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function emergencyRevoke(input: {
  pack_id: string;
  version: string;
  trigger: PackInvalidationTrigger;
  reason: string;
  second_factor: string;
}): Promise<{ event: PackInvalidationEvent; revocation: RevocationEvent }> {
  const res = await fetch(`${API_BASE}/domain-packs/revocations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(input),
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function listRevocations(): Promise<RevocationEvent[]> {
  const res = await fetch(`${API_BASE}/domain-packs/revocations`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function proposeRollback(input: {
  pack_id: string;
  from_version: string;
  reason: string;
}): Promise<PackRollbackRecord> {
  const res = await fetch(`${API_BASE}/domain-packs/rollbacks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(input),
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function listRollbacks(): Promise<PackRollbackRecord[]> {
  const res = await fetch(`${API_BASE}/domain-packs/rollbacks`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function confirmRollback(
  rollbackId: string,
  role: "independent" | "platform",
  conclusion: "approve" | "reject" | "needs_changes",
  opinion = ""
): Promise<PackRollbackRecord> {
  const res = await fetch(
    `${API_BASE}/domain-packs/rollbacks/${encodeURIComponent(rollbackId)}/confirm`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ role, conclusion, opinion }),
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function executeRollback(rollbackId: string): Promise<PackRollbackRecord> {
  const res = await fetch(
    `${API_BASE}/domain-packs/rollbacks/${encodeURIComponent(rollbackId)}/execute`,
    {
      method: "POST",
      credentials: "same-origin",
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

// ---------------------------------------------------------------------------
// Issue 11：持久化流式聊天
// ---------------------------------------------------------------------------

/**
 * 判别式类型守卫：从事件载荷的 kind 字段收窄流事件类型。
 * 事件名枚举与载荷形状均来自 OpenAPI 契约（ChatStreamEvent），
 * 不再与后端生成器手写镜像。
 */
export function isChatStreamEventOf<K extends ChatStreamEvent["data"]["kind"]>(
  event: ChatStreamEvent,
  kind: K
): event is ChatStreamEvent & {
  data: Extract<ChatStreamEvent["data"], { kind: K }>;
} {
  return event.data.kind === kind;
}

/**
 * 消费一次 SSE 流式响应，把每个事件回调给调用方。
 * 不在此处抛出网络错误以外的异常——服务端错误事件通过 error 事件回调。
 */
async function readSseStream(
  response: Response,
  onEvent: (event: ChatStreamEvent) => void
): Promise<void> {
  if (!response.body) {
    throw new Error("流式响应没有可读内容。");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let separator = buffer.indexOf("\n\n");
    while (separator !== -1) {
      const block = buffer.slice(0, separator);
      buffer = buffer.slice(separator + 2);
      let eventName = "message";
      let dataText = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) {
          eventName = line.slice("event:".length).trim();
        } else if (line.startsWith("data:")) {
          dataText += line.slice("data:".length).trim();
        }
      }
      if (dataText) {
        onEvent({ event: eventName, data: JSON.parse(dataText) } as ChatStreamEvent);
      }
      separator = buffer.indexOf("\n\n");
    }
  }
}

export async function listChatConversations(): Promise<ChatConversationListProjection> {
  const res = await fetch(`${API_BASE}/chat/conversations`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function createChatConversation(
  title?: string,
  mode: ChatMode = "companion",
  projectId?: string
): Promise<ChatConversationProjection> {
  const res = await fetch(`${API_BASE}/chat/conversations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ title: title ?? null, mode, project_id: projectId ?? null }),
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function updateChatConversation(
  conversationId: string,
  update: { title?: string; pinned?: boolean }
): Promise<ChatConversationProjection> {
  const res = await fetch(`${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(update),
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function deleteChatConversation(conversationId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}`, {
    method: "DELETE",
    credentials: "same-origin",
  });
  if (!res.ok) throw await parseApiError(res);
}

/** 切换对话模式（日常陪伴/学习模式）；返回切换后的对话与本次可见事件。 */
export async function switchChatMode(
  conversationId: string,
  mode: ChatMode
): Promise<ChatModeSwitchResponse> {
  const res = await fetch(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/mode`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ mode }),
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function getChatConversation(
  conversationId: string
): Promise<ChatConversationProjection> {
  const res = await fetch(`${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

function attachmentApiError(status: number, body: unknown): ApiError {
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
function uploadRawBytes<T>(
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

/** 原始字节上传：服务端负责内容嗅探，XHR 只用于提供可靠的上传进度与取消。 */
export function uploadChatAttachment(
  conversationId: string,
  file: File,
  uploadId: string,
  onProgress?: (loaded: number, total: number) => void,
  signal?: AbortSignal
): Promise<ChatAttachmentProjection> {
  return uploadRawBytes<ChatAttachmentProjection>(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/attachments`,
    file,
    uploadId,
    onProgress,
    signal
  );
}

export async function cancelChatAttachment(
  conversationId: string,
  objectId: string
): Promise<void> {
  const res = await fetch(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/attachments/${encodeURIComponent(objectId)}`,
    { method: "DELETE", credentials: "same-origin" }
  );
  if (!res.ok) throw await parseApiError(res);
}

export async function cancelChatAttachmentUpload(
  conversationId: string,
  uploadId: string
): Promise<void> {
  const res = await fetch(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/attachments/by-upload/${encodeURIComponent(uploadId)}`,
    { method: "DELETE", credentials: "same-origin" }
  );
  if (!res.ok) throw await parseApiError(res);
}

export async function deleteChatMessageAttachment(
  conversationId: string,
  messageId: string,
  objectId: string
): Promise<void> {
  const res = await fetch(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}/attachments/${encodeURIComponent(objectId)}`,
    { method: "DELETE", credentials: "same-origin" }
  );
  if (!res.ok) throw await parseApiError(res);
}

export async function downloadChatAttachment(
  conversationId: string,
  objectId: string,
  originalFilename: string
): Promise<void> {
  const res = await fetch(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/attachments/${encodeURIComponent(objectId)}/download`,
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

/** 读取附件摄取的完整详情（状态、失败阶段、页码/章节、向量可用性）。 */
export async function getAttachmentIngestion(
  conversationId: string,
  objectId: string
): Promise<DocumentIngestionProjection> {
  const res = await fetch(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/attachments/${encodeURIComponent(objectId)}/ingestion`,
    { credentials: "same-origin" }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/** 把失败文档重新入队；非失败状态幂等返回当前投影。 */
export async function retryAttachmentIngestion(
  conversationId: string,
  objectId: string
): Promise<DocumentIngestionProjection> {
  const res = await fetch(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/attachments/${encodeURIComponent(objectId)}/ingestion/retry`,
    { method: "POST", credentials: "same-origin" }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/** 返回当前账户的索引状态（向量可用性、活跃版本与可回滚版本链）。 */
export async function getIngestionIndexStatus(): Promise<IndexStatusProjection> {
  const res = await fetch(`${API_BASE}/chat/ingestion/index`, {
    credentials: "same-origin",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/**
 * 发送消息并流式接收回答；AbortController 用于停止/切换账户时中断。
 * 触发回调序列：started → delta* → done | error。
 */
export async function streamChatMessage(
  conversationId: string,
  content: string,
  onEvent: (event: ChatStreamEvent) => void,
  signal?: AbortSignal,
  attachmentIds: string[] = []
): Promise<void> {
  const res = await fetch(`${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ content, attachment_ids: attachmentIds }),
    signal,
  });
  if (!res.ok) throw await parseApiError(res);
  await readSseStream(res, onEvent);
}

/** 重试失败的助手消息：创建新的助手尝试并流式生成。 */
export async function retryChatMessage(
  conversationId: string,
  messageId: string,
  onEvent: (event: ChatStreamEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  const res = await fetch(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}/retry`,
    { method: "POST", credentials: "same-origin", signal }
  );
  if (!res.ok) throw await parseApiError(res);
  await readSseStream(res, onEvent);
}

export async function stopChatMessage(
  conversationId: string,
  messageId: string
): Promise<ChatStopResponse> {
  const res = await fetch(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}/stop`,
    { method: "POST", credentials: "same-origin" }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

// ---------------------------------------------------------------------------
// Issue 18：全局本地知识库
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
