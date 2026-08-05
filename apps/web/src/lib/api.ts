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
// Issue 28：内置 bridges-humanizer SKILL 契约（生成类型来自 openapi.json）。
export type HumanizerResultProjection = components["schemas"]["HumanizerResultProjection"];
export type HumanizerProcessState = components["schemas"]["HumanizerProcessState"];
export type ChatStreamHumanizerData = components["schemas"]["ChatStreamHumanizerData"];
export type HumanizerFactCheckItem = components["schemas"]["HumanizerFactCheckItem"];
export type HumanizerEdit = components["schemas"]["HumanizerEdit"];
export type HumanizerSkillInput = components["schemas"]["HumanizerSkillInput"];
export type HumanizerTaskContract = components["schemas"]["HumanizerTaskContract"];
export type HumanizerPath = components["schemas"]["HumanizerPath"];
// Issue 31：图片生成与编辑契约（生成类型来自 openapi.json）。
export type ImageTaskProjection = components["schemas"]["ImageTaskProjection"];
export type ImageTaskKind = components["schemas"]["ImageTaskKind"];
export type ImageTaskStatus = components["schemas"]["ImageTaskStatus"];
export type ImageAssetProjection = components["schemas"]["ImageAssetProjection"];
export type ImageVersionProjection = components["schemas"]["ImageVersionProjection"];
export type ImageAltTextSource = components["schemas"]["ImageAltTextSource"];
export type ImageDeletionProjection = components["schemas"]["ImageDeletionProjection"];
export type ImageRequestPayload = components["schemas"]["ImageRequestPayload"];
export type ChatStreamImageData = components["schemas"]["ChatStreamImageData"];
// Issue 29：生涯规划助手契约（生成类型来自 openapi.json）。
export type CareerPlanningProjection = components["schemas"]["CareerPlanningProjection"];
export type CareerPlanningProcessState = components["schemas"]["CareerPlanningProcessState"];
export type CareerPlanningStatus = components["schemas"]["CareerPlanningStatus"];
export type CareerPlanningOutputContract = components["schemas"]["CareerPlanningOutputContract"];
export type CareerEvidenceSource = components["schemas"]["CareerEvidenceSource"];
export type CareerEvidenceKind = components["schemas"]["CareerEvidenceKind"];
export type CareerItemState = components["schemas"]["CareerItemState"];
export type CareerFact = components["schemas"]["CareerFact"];
export type CareerAssumption = components["schemas"]["CareerAssumption"];
export type CareerOption = components["schemas"]["CareerOption"];
export type CareerRisk = components["schemas"]["CareerRisk"];
export type CareerStage = components["schemas"]["CareerStage"];
export type CareerSuggestion = components["schemas"]["CareerSuggestion"];
export type CareerReviewResult = components["schemas"]["CareerReviewResult"];
export type ChatStreamCareerData = components["schemas"]["ChatStreamCareerData"];
// Issue 30：听写与单条回答朗读契约（生成类型来自 openapi.json）。
export type DictationProjection = components["schemas"]["DictationProjection"];
export type DictationStatus = components["schemas"]["DictationStatus"];
export type ReadAloudProjection = components["schemas"]["ReadAloudProjection"];
export type ReadAloudState = components["schemas"]["ReadAloudState"];
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
export type ArxivSearchProjection = components["schemas"]["ArxivSearchProjection"];
export type ArxivPaperProjection = components["schemas"]["ArxivPaperProjection"];
export type ArxivSearchStatus = components["schemas"]["ArxivSearchStatus"];
export type WebSearchProjection = components["schemas"]["WebSearchProjection"];
export type WebSearchResult = components["schemas"]["WebSearchResult"];
export type WebSearchStatus = components["schemas"]["WebSearchStatus"];
export type RetrievalRoundProjection = components["schemas"]["RetrievalRoundProjection"];
export type RetrievalLayerResult = components["schemas"]["RetrievalLayerResult"];
export type RetrievalLayerStatus = components["schemas"]["RetrievalLayerStatus"];
export type RetrievalSourceLayer = components["schemas"]["RetrievalSourceLayer"];
export type RetrievalSufficiency = components["schemas"]["RetrievalSufficiency"];
export type TeachingTurnProjection = components["schemas"]["TeachingTurnProjection"];
export type TeachingEvidenceGate = components["schemas"]["TeachingEvidenceGate"];
export type TeachingEvidenceSource = components["schemas"]["TeachingEvidenceSource"];
export type TeachingQuiz = components["schemas"]["TeachingQuiz"];
export type TeachingAnswerEvidence = components["schemas"]["TeachingAnswerEvidence"];
export type ContextNoteProjection = components["schemas"]["ContextNoteProjection"];
export type ContextNoteState = components["schemas"]["ContextNoteState"];
export type ContextNoteProfileItem = components["schemas"]["ContextNoteProfileItem"];
export type AnswerFeedback = components["schemas"]["AnswerFeedback"];
export type FeedbackKind = components["schemas"]["FeedbackKind"];
export type FeedbackStatus = components["schemas"]["FeedbackStatus"];
export type FeedbackResolveRequest = components["schemas"]["FeedbackResolveRequest"];
export type CitationProjection = components["schemas"]["CitationProjection"];
export type CitationDetailProjection = components["schemas"]["CitationDetailProjection"];
export type CitationAccessStatus = components["schemas"]["CitationAccessStatus"];
export type LearningProjectSummary = components["schemas"]["LearningProjectSummary"];
export type LearningProjectDetail = components["schemas"]["LearningProjectDetail"];
export type LearningProjectConversation = components["schemas"]["LearningProjectConversation"];
export type LearningProjectFile = components["schemas"]["LearningProjectFile"];
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
export type SearchResponse = components["schemas"]["SearchResponse"];
export type SearchResultItem = components["schemas"]["SearchResultItem"];
export type SearchSegment = components["schemas"]["SearchSegment"];
export type ProfileAssertion = components["schemas"]["ProfileAssertion"];
export type ProfileAssertionVersion = components["schemas"]["ProfileAssertionVersion"];
export type ProfileAssertionHistory = components["schemas"]["ProfileAssertionHistory"];
export type ProfileCandidate = components["schemas"]["ProfileCandidate"];
export type ProfileCandidateCreateRequest = components["schemas"]["ProfileCandidateCreateRequest"];
export type ProfileExport = components["schemas"]["ProfileExport"];
export type ProfileDimension = components["schemas"]["ProfileDimension"];
export type ProfileSensitivityClass = components["schemas"]["ProfileSensitivityClass"];
export type AssertionStatus = components["schemas"]["AssertionStatus"];
export type CandidateReviewStatus = components["schemas"]["CandidateReviewStatus"];
export type ManualAssertionCreateRequest = components["schemas"]["ManualAssertionCreateRequest"];
export type ProfileAssertionModifyRequest = components["schemas"]["ProfileAssertionModifyRequest"];
export type ProfileError = components["schemas"]["ProfileError"];
export type ProfileObservation = components["schemas"]["ProfileObservation"];
export type ProfilePermission = components["schemas"]["ProfilePermission"];
export type ProfilePermissionUpdateRequest = components["schemas"]["ProfilePermissionUpdateRequest"];
export type ProfileNotification = components["schemas"]["ProfileNotification"];
export type ProfileNotificationKind = components["schemas"]["ProfileNotificationKind"];
export type ProfileBatchCandidateDecisionRequest = components["schemas"]["ProfileBatchCandidateDecisionRequest"];
export type ProfileBatchCandidateResult = components["schemas"]["ProfileBatchCandidateResult"];

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
 * ``useKnowledgeBase``（Issue 20）：本轮是否启用全局知识库层；关闭后
 * 本轮检索记录与引用均不包含知识库候选。
 * ``useProfile``（Issue 27）：本轮是否使用画像切片；关闭后模型请求、
 * 审计与上下文说明均不含任何画像内容。
 */
export async function streamChatMessage(
  conversationId: string,
  content: string,
  onEvent: (event: ChatStreamEvent) => void,
  signal?: AbortSignal,
  attachmentIds: string[] = [],
  useKnowledgeBase: boolean = true,
  useProfile: boolean = true,
  skillId?: string,
  skillInput?: unknown,
  image?: ImageRequestPayload
): Promise<void> {
  const res = await fetch(`${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({
      content,
      attachment_ids: attachmentIds,
      use_knowledge_base: useKnowledgeBase,
      use_profile: useProfile,
      // Issue 28：内置 SKILL 载荷（bridges-humanizer 走真实消息流程）
      ...(skillId !== undefined ? { skill_id: skillId } : {}),
      ...(skillInput !== undefined ? { skill_input: skillInput } : {}),
      // Issue 31：图片生成/编辑载荷（图片对话框走真实消息流程，任务异步执行）
      ...(image !== undefined ? { image } : {}),
    }),
    signal,
  });
  if (!res.ok) throw await parseApiError(res);
  await readSseStream(res, onEvent);
}

/** 提交一条回答反馈（Issue 27）：回答不合适或画像有误（幂等，不丢反馈）。
 *  Issue 29：``career_item_ref`` 把反馈定位到生涯规划结果的具体条目。 */
export async function submitAnswerFeedback(
  conversationId: string,
  messageId: string,
  request: {
    kind: FeedbackKind;
    feedback_text: string;
    preference?: string | null;
    assertion_id?: string | null;
    career_item_ref?: string | null;
  }
): Promise<AnswerFeedback> {
  const res = await fetch(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}/feedback`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(request),
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/** 列出对话内反馈（最新在前，供前端恢复与闭环查看）。 */
export async function listConversationFeedback(
  conversationId: string
): Promise<AnswerFeedback[]> {
  const res = await fetch(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/feedback`,
    { credentials: "same-origin", cache: "no-store" }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/** 把一条反馈标记为已处理并记录修正说明（幂等）。 */
export async function resolveAnswerFeedback(
  conversationId: string,
  feedbackId: string,
  resolutionNote: string
): Promise<AnswerFeedback> {
  const res = await fetch(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/feedback/${encodeURIComponent(feedbackId)}/resolve`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ resolution_note: resolutionNote }),
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
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

/**
 * 单条引用的证据详情（Issue 20）：点击引用时实时校验授权并给出
 * 打开原文入口；原文已删除或权限变化时返回安全中文状态。
 */
export async function getCitationDetail(
  conversationId: string,
  messageId: string,
  citationId: string
): Promise<CitationDetailProjection> {
  const res = await fetch(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}/citations/${encodeURIComponent(citationId)}`,
    { credentials: "same-origin", cache: "no-store" }
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

// ---------------------------------------------------------------------------
// Issue 19：文件夹式学习项目
// ---------------------------------------------------------------------------

/** 当前账户的学习项目列表（按最近更新倒序）。 */
export async function listLearningProjects(
  options?: { signal?: AbortSignal }
): Promise<LearningProjectSummary[]> {
  const res = await fetch(`${API_BASE}/learning-projects`, {
    credentials: "same-origin",
    cache: "no-store",
    signal: options?.signal,
  });
  if (!res.ok) throw await parseApiError(res);
  const projection: { projects?: LearningProjectSummary[] } = await res.json();
  return projection.projects ?? [];
}

/** 新建学习项目；名称必填，描述可选。 */
export async function createLearningProject(
  name: string,
  description?: string
): Promise<LearningProjectSummary> {
  const res = await fetch(`${API_BASE}/learning-projects`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ name, description: description ?? null }),
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/** 单个学习项目详情（含归属对话列表）。 */
export async function getLearningProject(projectId: string): Promise<LearningProjectDetail> {
  const res = await fetch(
    `${API_BASE}/learning-projects/${encodeURIComponent(projectId)}`,
    { credentials: "same-origin", cache: "no-store" }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/**
 * 更新学习项目名称或描述；字段缺省保持不变，
 * ``description`` 显式传 null 表示清空描述。
 */
export async function updateLearningProject(
  projectId: string,
  update: { name?: string; description?: string | null }
): Promise<LearningProjectSummary> {
  const body: Record<string, unknown> = {};
  if (update.name !== undefined) body.name = update.name;
  if (update.description !== undefined) body.description = update.description;
  const res = await fetch(
    `${API_BASE}/learning-projects/${encodeURIComponent(projectId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(body),
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/**
 * 删除学习项目：``keep`` 保留对话为独立对话（项目文件随项目删除），
 * ``delete`` 一并删除对话（含附件）与项目文件；
 * 生成中返回 409 generation_in_progress。
 */
export async function deleteLearningProject(
  projectId: string,
  contents: "keep" | "delete"
): Promise<void> {
  const res = await fetch(
    `${API_BASE}/learning-projects/${encodeURIComponent(projectId)}?contents=${contents}`,
    { method: "DELETE", credentials: "same-origin" }
  );
  if (!res.ok) throw await parseApiError(res);
}

/** 项目文件列表（最新上传在前）。 */
export async function listLearningProjectFiles(
  projectId: string,
  options?: { signal?: AbortSignal }
): Promise<LearningProjectFile[]> {
  const res = await fetch(
    `${API_BASE}/learning-projects/${encodeURIComponent(projectId)}/files`,
    { credentials: "same-origin", cache: "no-store", signal: options?.signal }
  );
  if (!res.ok) throw await parseApiError(res);
  const projection: { files?: LearningProjectFile[] } = await res.json();
  return projection.files ?? [];
}

/**
 * 上传项目文件：与知识库材料共用 uploadRawBytes（XHR 进度 + 取消）；
 * 201 新建 / 200 幂等复用，均按成功处理。
 */
export function uploadLearningProjectFile(
  projectId: string,
  file: File,
  uploadId: string,
  onProgress?: (loaded: number, total: number) => void,
  signal?: AbortSignal
): Promise<LearningProjectFile> {
  return uploadRawBytes<LearningProjectFile>(
    `${API_BASE}/learning-projects/${encodeURIComponent(projectId)}/files`,
    file,
    uploadId,
    onProgress,
    signal
  );
}

/** 下载项目文件原始内容（与知识库材料下载同一模式）。 */
export async function downloadLearningProjectFile(
  projectId: string,
  objectId: string,
  originalFilename: string
): Promise<void> {
  const res = await fetch(
    `${API_BASE}/learning-projects/${encodeURIComponent(projectId)}/files/${encodeURIComponent(objectId)}/download`,
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

/** 删除项目文件（处理中返回 409 material_processing）。 */
export async function deleteLearningProjectFile(
  projectId: string,
  objectId: string
): Promise<void> {
  const res = await fetch(
    `${API_BASE}/learning-projects/${encodeURIComponent(projectId)}/files/${encodeURIComponent(objectId)}`,
    { method: "DELETE", credentials: "same-origin" }
  );
  if (!res.ok) throw await parseApiError(res);
}

/**
 * 变更对话的学习项目归属：传入项目标识移入项目，
 * 显式传 null 移出项目（保留为独立对话，历史消息与附件不变）。
 */
export async function updateChatConversationProject(
  conversationId: string,
  projectId: string | null
): Promise<ChatConversationProjection> {
  const res = await fetch(`${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ project_id: projectId }),
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

// ---------------------------------------------------------------------------
// Issue 24：跨内容统一桌面搜索
// ---------------------------------------------------------------------------

/** 统一搜索的结果类型筛选值（与契约 SearchResultItem.result_type 一致）。 */
export type SearchResultType = SearchResultItem["result_type"];

export interface UnifiedSearchParams {
  q: string;
  types?: SearchResultType[];
  projectId?: string;
  /** ISO 8601 起止时间（updated_at 过滤）。 */
  from?: string;
  to?: string;
  limit?: number;
  signal?: AbortSignal;
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
  if (params.projectId) query.set("project_id", params.projectId);
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

// ---------- 画像中心（Issue 25） ----------

export async function listProfileAssertions(): Promise<ProfileAssertion[]> {
  const res = await fetch(`${API_BASE}/profiles/assertions`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function listProfileCandidates(): Promise<ProfileCandidate[]> {
  const res = await fetch(`${API_BASE}/profiles/candidates`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function createManualAssertion(
  request: ManualAssertionCreateRequest
): Promise<ProfileAssertion> {
  const res = await fetch(`${API_BASE}/profiles/assertions/manual`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(request),
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

async function postAssertionAction(
  assertionId: string,
  action: string,
  reason: string
): Promise<ProfileAssertion> {
  const res = await fetch(
    `${API_BASE}/profiles/assertions/${encodeURIComponent(assertionId)}/${action}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ reason }),
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export function withdrawProfileAssertion(assertionId: string, reason: string): Promise<ProfileAssertion> {
  return postAssertionAction(assertionId, "withdraw", reason);
}

export function freezeProfileAssertion(assertionId: string, reason: string): Promise<ProfileAssertion> {
  return postAssertionAction(assertionId, "freeze", reason);
}

export function unfreezeProfileAssertion(assertionId: string, reason: string): Promise<ProfileAssertion> {
  return postAssertionAction(assertionId, "unfreeze", reason);
}

export function deleteProfileAssertion(assertionId: string, reason: string): Promise<ProfileAssertion> {
  return postAssertionAction(assertionId, "delete", reason);
}

export async function modifyProfileAssertion(
  assertionId: string,
  request: ProfileAssertionModifyRequest
): Promise<ProfileAssertion> {
  const res = await fetch(
    `${API_BASE}/profiles/assertions/${encodeURIComponent(assertionId)}/modify`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(request),
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function getProfileAssertionHistory(
  assertionId: string
): Promise<ProfileAssertionHistory> {
  const res = await fetch(
    `${API_BASE}/profiles/assertions/${encodeURIComponent(assertionId)}/history`,
    { credentials: "same-origin", cache: "no-store" }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function exportProfile(): Promise<ProfileExport> {
  const res = await fetch(`${API_BASE}/profiles/export`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function decideCandidate(
  candidateId: string,
  decision: "accept" | "reject" | "modify",
  reason: string,
  modifiedValue?: string,
  modifiedScenes?: string[]
): Promise<ProfileCandidate> {
  const res = await fetch(
    `${API_BASE}/profiles/candidates/${encodeURIComponent(candidateId)}/decision`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({
        decision,
        reason,
        modified_value_or_rule: modifiedValue,
        modified_applicable_scenes: modifiedScenes,
      }),
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function removeAvatar(): Promise<Account> {
  const res = await fetch(`${API_BASE}/auth/profile/avatar`, {
    method: "DELETE",
    credentials: "same-origin",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function rollbackProfileAssertion(
  assertionId: string,
  toVersion: number,
  reason: string
): Promise<ProfileAssertion> {
  const res = await fetch(
    `${API_BASE}/profiles/assertions/${encodeURIComponent(assertionId)}/rollback`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ to_version: toVersion, reason }),
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function listProfileObservations(): Promise<
  components["schemas"]["ProfileObservation"][]
> {
  const res = await fetch(`${API_BASE}/profiles/observations`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

// ---------- 画像记忆中心（Issue 26：许可、通知、批量决策） ----------

export async function listProfilePermissions(): Promise<ProfilePermission[]> {
  const res = await fetch(`${API_BASE}/profiles/permissions`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function updateProfilePermission(
  request: ProfilePermissionUpdateRequest
): Promise<ProfilePermission> {
  const res = await fetch(`${API_BASE}/profiles/permissions`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(request),
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function listProfileNotifications(): Promise<ProfileNotification[]> {
  const res = await fetch(`${API_BASE}/profiles/notifications`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function markProfileNotificationRead(
  notificationId: string
): Promise<ProfileNotification> {
  const res = await fetch(
    `${API_BASE}/profiles/notifications/${encodeURIComponent(notificationId)}/read`,
    { method: "POST", credentials: "same-origin" }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function recallProfileNotification(
  notificationId: string
): Promise<ProfileNotification> {
  const res = await fetch(
    `${API_BASE}/profiles/notifications/${encodeURIComponent(notificationId)}/recall`,
    { method: "POST", credentials: "same-origin" }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function decideCandidatesBatch(
  request: ProfileBatchCandidateDecisionRequest
): Promise<ProfileBatchCandidateResult> {
  const res = await fetch(`${API_BASE}/profiles/candidates/batch-decision`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(request),
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

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

/** 为一条已完成的助手回答生成朗读（固定 TTS 快照；失败返回 failed 投影）。 */
export async function generateReadAloud(
  conversationId: string,
  messageId: string
): Promise<ReadAloudProjection> {
  const res = await fetch(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}/read-aloud`,
    { method: "POST", credentials: "same-origin" }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/** 删除朗读音频并复位状态（幂等；同源会话 Cookie 授权）。 */
export async function deleteReadAloud(
  conversationId: string,
  messageId: string
): Promise<ReadAloudProjection> {
  const res = await fetch(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}/read-aloud`,
    { method: "DELETE", credentials: "same-origin" }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/** 朗读音频的同源播放地址（经账户授权校验后流式返回）。 */
export function readAloudAudioUrl(
  conversationId: string,
  messageId: string
): string {
  return `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}/read-aloud/audio`;
}

// ---------------------------------------------------------------------------
// Issue 31：图片生成与编辑（任务操作面 + 资产操作面）
// ---------------------------------------------------------------------------

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

/** 取消任务：本地标记为权威；迟到结果不会发布为成功资产。 */
export async function cancelImageTask(
  conversationId: string,
  taskId: string
): Promise<ImageTaskProjection> {
  const res = await fetch(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/image-tasks/${encodeURIComponent(taskId)}/cancel`,
    { method: "POST", credentials: "same-origin" }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/** 重试失败任务：同输入（提示/来源不变）重新入队，固定同一模型快照。 */
export async function retryImageTask(
  conversationId: string,
  taskId: string
): Promise<ImageTaskProjection> {
  const res = await fetch(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/image-tasks/${encodeURIComponent(taskId)}/retry`,
    { method: "POST", credentials: "same-origin" }
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

/** 修改替代文本（来源标记为 manual）。 */
export async function updateImageAltText(
  conversationId: string,
  assetId: string,
  altText: string
): Promise<ImageAssetProjection> {
  const res = await fetch(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/image-assets/${encodeURIComponent(assetId)}/alt-text`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ alt_text: altText }),
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/** 删除资产并返回影响说明（版本数/消息引用/对象处置）；幂等。 */
export async function deleteImageAsset(
  conversationId: string,
  assetId: string
): Promise<ImageDeletionProjection> {
  const res = await fetch(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/image-assets/${encodeURIComponent(assetId)}`,
    { method: "DELETE", credentials: "same-origin" }
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
