"use client";

import type { components } from "@bridges/contracts";

export type HealthProjection = components["schemas"]["HealthProjection"];
export type HealthStatus = components["schemas"]["HealthStatus"];
export type ProfileStatusProjection = components["schemas"]["ProfileStatusProjection"];
export type Account = components["schemas"]["Account"];
export type AuthResponse = components["schemas"]["AuthResponse"];
export type SessionResponse = components["schemas"]["SessionResponse"];
export type AuthError = components["schemas"]["AuthError"];
export type AvatarChoice = components["schemas"]["AvatarChoice"];
export type AccountProfileUpdate = components["schemas"]["AccountProfileUpdate"];
export type CredentialStatus = components["schemas"]["CredentialStatus"];
export type CredentialSettings = components["schemas"]["CredentialSettingsResponse"];
export type ModelSettings = components["schemas"]["ModelSettingsResponse"];
export type ModelCapabilities = components["schemas"]["ModelCapabilities"];
export type ModelCapabilityCheck = components["schemas"]["ModelCapabilityCheck"];
export type ModelValidationReport = components["schemas"]["ModelValidationReport"];
export type DeviceAccountProjection = components["schemas"]["DeviceAccountProjection"];
export type DeviceAccountsResponse = components["schemas"]["DeviceAccountsResponse"];
export type DeviceLogoutResponse = components["schemas"]["DeviceLogoutResponse"];
export type ChatMessageProjection = components["schemas"]["ChatMessageProjection"];
export type ChatAttachmentProjection = components["schemas"]["ChatAttachmentProjection"];
// Issue 05：聊天照片草稿（账户域，发送时原子绑定到消息）。
export type ChatAttachmentDraftProjection = components["schemas"]["ChatAttachmentDraftProjection"];
export type ChatMessageRole = components["schemas"]["ChatMessageRole"];
export type ChatMessageStatus = components["schemas"]["ChatMessageStatus"];
export type ChatConversationProjection = components["schemas"]["ChatConversationProjection"];
export type ChatConversationSummary = components["schemas"]["ChatConversationSummary"];
export type ChatConversationListProjection = components["schemas"]["ChatConversationListProjection"];
export type ChatStopResponse = components["schemas"]["ChatStopResponse"];
export type ChatStreamEvent = components["schemas"]["ChatStreamEvent"];
export type ChatStreamEventKind = components["schemas"]["ChatStreamEventKind"];
export type ChatRunStartedResponse = components["schemas"]["ChatRunStartedResponse"];
export type ChatFirstTurnResponse = components["schemas"]["ChatFirstTurnResponse"];
export type ChatRunView = components["schemas"]["ChatRunView"];
export type ChatStreamStartedData = components["schemas"]["ChatStreamStartedData"];
export type ChatStreamStageData = components["schemas"]["ChatStreamStageData"];
// V2 Issue 02：日常父图节点进度事件（只映射真实开始/完成的节点）。
export type ChatStreamNodeData = components["schemas"]["ChatStreamNodeData"];
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
// Issue 08：版本化文章结果投影（正文优先交付界面）。
export type HumanizerArticleProjection = components["schemas"]["HumanizerArticleProjection"];
export type ArticleDeliveryStatus = components["schemas"]["ArticleDeliveryStatus"];
export type ArticleMaterialState = components["schemas"]["ArticleMaterialState"];
export type ArticleFidelitySummary = components["schemas"]["ArticleFidelitySummary"];
export type ArticleStyleReviewSummary = components["schemas"]["ArticleStyleReviewSummary"];
export type ArticleRevisionSummary = components["schemas"]["ArticleRevisionSummary"];
// Issue 02 第八次改进：确定性剔除摘要（已剔除交付的如实披露）。
export type ArticleExcisionSummary = components["schemas"]["ArticleExcisionSummary"];
export type ArticleExcisionItem = components["schemas"]["ArticleExcisionItem"];
export type ArticleEvidenceItem = components["schemas"]["ArticleEvidenceItem"];
export type ArticleConfirmationItem = components["schemas"]["ArticleConfirmationItem"];
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
// Issue 32：文生视频契约（生成类型来自 openapi.json；Wan 固定绑定）。
export type VideoTaskProjection = components["schemas"]["VideoTaskProjection"];
export type VideoTaskStatus = components["schemas"]["VideoTaskStatus"];
export type VideoAssetProjection = components["schemas"]["VideoAssetProjection"];
export type VideoDescriptionSource = components["schemas"]["VideoDescriptionSource"];
export type VideoDeletionProjection = components["schemas"]["VideoDeletionProjection"];
export type VideoRequestPayload = components["schemas"]["VideoRequestPayload"];
export type ChatStreamVideoData = components["schemas"]["ChatStreamVideoData"];
// Issue 29：生涯规划助手契约（生成类型来自 openapi.json）。
export type CareerPlanningRouteContract = components["schemas"]["CareerPlanningRouteContract"];
export type CapabilityRoute = components["schemas"]["CapabilityRoute"];
export type MainCapability = components["schemas"]["MainCapability"];
export type RouteStatus = components["schemas"]["RouteStatus"];
export type RouteDecision = components["schemas"]["RouteDecision"];
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
export type ChatThinkingSummary = components["schemas"]["ChatThinkingSummary"];
export type ArxivSearchProjection = components["schemas"]["ArxivSearchProjection"];
export type ArxivPaperProjection = components["schemas"]["ArxivPaperProjection"];
export type ArxivSearchStatus = components["schemas"]["ArxivSearchStatus"];
// V2 Issue 11：日常显式模块（逐消息持久化）与论文模块投影。
export type ChatModuleId = components["schemas"]["ChatModuleId"];
export type ModuleSuggestionProjection = components["schemas"]["ModuleSuggestionProjection"];
export type ModuleQueryRecord = components["schemas"]["ModuleQueryRecord"];
export type ModuleQueryStatus = components["schemas"]["ModuleQueryStatus"];
export type PaperSearchProjection = components["schemas"]["PaperSearchProjection"];
export type PaperSearchStatus = components["schemas"]["PaperSearchStatus"];
export type PaperRecommendation = components["schemas"]["PaperRecommendation"];
// V2 Issue 14：贴吧信息搜集模块投影（真实读到的帖子/楼层、帖链降级与官方核验）。
export type TiebaResearchProjection = components["schemas"]["TiebaResearchProjection"];
export type TiebaResearchStatus = components["schemas"]["TiebaResearchStatus"];
export type TiebaTimeFilter = components["schemas"]["TiebaTimeFilter"];
export type TiebaPostProjection = components["schemas"]["TiebaPostProjection"];
export type TiebaReply = components["schemas"]["TiebaReply"];
export type TiebaCandidateLink = components["schemas"]["TiebaCandidateLink"];
export type TiebaRejectedCandidate = components["schemas"]["TiebaRejectedCandidate"];
export type TiebaOfficialCheck = components["schemas"]["TiebaOfficialCheck"];
// V2 Issue 16：GitHub 项目推荐模块投影（逐仓库的功能匹配、维护与许可证据）。
export type GithubProjectsProjection = components["schemas"]["GithubProjectsProjection"];
export type GithubProjectStatus = components["schemas"]["GithubProjectStatus"];
export type GithubCoverage = components["schemas"]["GithubCoverage"];
export type GithubEvidenceKind = components["schemas"]["GithubEvidenceKind"];
export type GithubReadmeStatus = components["schemas"]["GithubReadmeStatus"];
export type GithubContextSource = components["schemas"]["GithubContextSource"];
export type GithubFeatureMatch = components["schemas"]["GithubFeatureMatch"];
export type GithubRecommendation = components["schemas"]["GithubRecommendation"];
export type GithubRejectedRepository = components["schemas"]["GithubRejectedRepository"];
export type GithubMaintenanceEvidence = components["schemas"]["GithubMaintenanceEvidence"];
export type GithubLicenseCheck = components["schemas"]["GithubLicenseCheck"];
export type GithubImplementationCheck = components["schemas"]["GithubImplementationCheck"];
export type GithubFileRead = components["schemas"]["GithubFileRead"];
export type GithubRateLimitState = components["schemas"]["GithubRateLimitState"];
// V2 Issue 13：学习资料推荐模块投影（图书与哔哩哔哩视频清单）。
export type LearningResourcesProjection = components["schemas"]["LearningResourcesProjection"];
export type ResourcesStatus = components["schemas"]["ResourcesStatus"];
export type ResourceItem = components["schemas"]["ResourceItem"];
export type ResourceKind = components["schemas"]["ResourceKind"];
// V2 Issue 12：校园通勤投影（起终点 POI、方式、路径点与文字路段、课间缓冲）
// 与浏览器地图运行时配置（只含 JS API Key 与同源代理路径，不含安全密钥）。
export type CommuteRouteProjection = components["schemas"]["CommuteRouteProjection"];
export type CommuteRouteStatus = components["schemas"]["CommuteRouteStatus"];
export type CommuteMode = components["schemas"]["CommuteMode"];
export type CommutePlace = components["schemas"]["CommutePlace"];
export type CommutePlaceCandidate = components["schemas"]["CommutePlaceCandidate"];
export type CommutePlaceRole = components["schemas"]["CommutePlaceRole"];
export type CommuteRouteStep = components["schemas"]["CommuteRouteStep"];
export type CommuteBreakBuffer = components["schemas"]["CommuteBreakBuffer"];
export type CommuteMapConfig = components["schemas"]["MapConfigResponse"];
export type WebSearchProjection = components["schemas"]["WebSearchProjection"];
export type WebSearchResult = components["schemas"]["WebSearchResult"];
export type WebSearchStatus = components["schemas"]["WebSearchStatus"];
export type RetrievalRoundProjection = components["schemas"]["RetrievalRoundProjection"];
export type RetrievalLayerResult = components["schemas"]["RetrievalLayerResult"];
export type RetrievalLayerStatus = components["schemas"]["RetrievalLayerStatus"];
export type RetrievalSourceLayer = components["schemas"]["RetrievalSourceLayer"];
export type RetrievalSufficiency = components["schemas"]["RetrievalSufficiency"];
export type RetrievalDecisionProjection = components["schemas"]["RetrievalDecisionProjection"];
export type RetrievalDecisionAction = components["schemas"]["RetrievalDecisionAction"];
export type RetrievalDecisionReason = components["schemas"]["RetrievalDecisionReason"];
export type RetrievalCandidateFile = components["schemas"]["RetrievalCandidateFile"];
export type TeachingTurnProjection = components["schemas"]["TeachingTurnProjection"];
export type LearningProgressProjection = components["schemas"]["LearningProgressProjection"];
export type TeachingEvidenceGate = components["schemas"]["TeachingEvidenceGate"];
export type TeachingEvidenceSource = components["schemas"]["TeachingEvidenceSource"];
export type TeachingQuiz = components["schemas"]["TeachingQuiz"];
export type TeachingAnswerEvidence = components["schemas"]["TeachingAnswerEvidence"];
export type ContextNoteProjection = components["schemas"]["ContextNoteProjection"];
export type ContextNoteState = components["schemas"]["ContextNoteState"];
export type AnswerFeedback = components["schemas"]["AnswerFeedback"];
export type FeedbackKind = components["schemas"]["FeedbackKind"];
export type FeedbackStatus = components["schemas"]["FeedbackStatus"];
export type FeedbackResolveRequest = components["schemas"]["FeedbackResolveRequest"];
export type CitationProjection = components["schemas"]["CitationProjection"];
export type CitationDetailProjection = components["schemas"]["CitationDetailProjection"];
export type CitationAccessStatus = components["schemas"]["CitationAccessStatus"];
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

/** Issue 14：展开期四维画像的最小用户投影。内部来源、哈希和迁移字段不在页面渲染。 */
export type FourDimension = components["schemas"]["FourDimension"];
export type FourDimensionRecordStatus = components["schemas"]["FourDimensionRecordStatus"];
export type FourDimensionConfidence = components["schemas"]["FourDimensionConfidence"];
export type FourDimensionProfileRecord =
  components["schemas"]["FourDimensionProfileProjection"];
export type FourDimensionProfileModifyRequest =
  components["schemas"]["FourDimensionProfileModifyRequest"];
export type FourDimensionProfileDeleteRequest =
  components["schemas"]["FourDimensionProfileDeleteRequest"];
// V2 Issue 08：无固定类别的原子长期信息列表；投影不含类别、去重键与账户字段。
export type AtomicProfileItemProjection =
  components["schemas"]["AtomicProfileItemProjection"];
export type AtomicProfileItemModifyRequest =
  components["schemas"]["AtomicProfileItemModifyRequest"];
export type AtomicProfileWriteOrigin =
  components["schemas"]["AtomicProfileWriteOrigin"];

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

/** 验证并原子激活手填的主模型 ID；失败保留原配置。 */
export async function replaceModelConfiguration(modelId: string): Promise<ModelSettings> {
  const res = await fetch(`${API_BASE}/settings/models`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ model_id: modelId }),
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

export async function updateChatConversation(
  conversationId: string,
  update: { title?: string; pinned?: boolean }
): Promise<ChatConversationProjection> {
  const body: Record<string, unknown> = {};
  if (update.title !== undefined) body.title = update.title;
  if (update.pinned !== undefined) body.pinned = update.pinned;
  const res = await fetch(`${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(body),
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

/** 返回当前账户的索引状态（向量可用性、活跃版本与可回滚版本链）。 */
export async function getIngestionIndexStatus(): Promise<IndexStatusProjection> {
  const res = await fetch(`${API_BASE}/chat/ingestion/index`, {
    credentials: "same-origin",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/**
 * 发送消息：同事务创建用户消息、助手占位与 queued 生成运行，立即返回。
 *
 * Issue 02：HTTP 不再持有生成生命周期——生成由后台执行器领取执行，
 * 事件持久化到运行游标；随后以返回的 ``run_id``/``cursor`` 订阅
 * ``subscribeChatRunEvents`` 恢复进度。断开/刷新/切换会话都不改变运行。
 */
export async function createChatRun(
  conversationId: string,
  content: string,
  idempotencyKey?: string,
  attachmentIds: string[] = [],
  moduleId?: ChatModuleId
): Promise<ChatRunStartedResponse> {
  const res = await fetch(`${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    // V2 Issue 02：携带幂等键——网络重试复用同一运行，不重复写消息。
    // Issue 05：照片草稿与文字同请求原子绑定（服务端保持顺序）。
    // V2 Issue 11：显式模块随消息持久化；未选择时不提交该字段。
    body: JSON.stringify({
      content,
      ...(idempotencyKey ? { idempotency_key: idempotencyKey } : {}),
      ...(attachmentIds.length > 0 ? { attachment_ids: attachmentIds } : {}),
      ...(moduleId ? { module_id: moduleId } : {}),
    }),
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/**
 * 原子创建新会话首轮（Issue 03）。
 *
 * 首页发送第一条消息或调用任一功能时使用：服务端在同一事务内创建会话、
 * 用户消息、助手占位与 queued 运行，返回完整投影。客户端收到成功响应后
 * 再导航，``sessionStorage`` 不再承担业务真相。``idempotencyKey`` 抵御
 * 双击与网络重放（同键并发只产生一份数据）；``conversationId`` 可选指定
 * 已存在的空会话，缺省在事务内新建。
 * 同键重放返回 200 与既有数据（``idempotent_replay`` 为 true），调用方
 * 无须区分即可导航到同一会话。
 */
export interface ChatFirstTurnInput {
  content: string;
  idempotencyKey: string;
  conversationId?: string;
  mode?: ChatMode;
  // Issue 05：新聊天页直发照片（账户域草稿，首轮事务内原子绑定）。
  attachmentIds?: string[];
  // V2 Issue 11：首轮显式模块（选中 chip 时随首条用户消息持久化）。
  moduleId?: ChatModuleId;
}

export async function startFirstTurn(
  input: ChatFirstTurnInput
): Promise<ChatFirstTurnResponse> {
  const attachmentIds = input.attachmentIds ?? [];
  const body = {
    content: input.content,
    idempotency_key: input.idempotencyKey,
    ...(input.conversationId !== undefined
      ? { conversation_id: input.conversationId }
      : {}),
    ...(attachmentIds.length > 0 ? { attachment_ids: attachmentIds } : {}),
    ...(input.moduleId ? { module_id: input.moduleId } : {}),
    mode: input.mode ?? "companion",
  };
  const res = await fetch(`${API_BASE}/chat/first-turn`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(body),
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

// ---------------------------------------------------------------------------
// Issue 05：聊天照片附件草稿（账户域，发送时随消息原子绑定）
// ---------------------------------------------------------------------------

/**
 * 上传一张照片草稿（幂等：同 upload_id 重放返回既有草稿）。
 *
 * 原始字节直传，文件名经 ``X-Bridges-Filename`` 头传递（URL 编码，
 * 服务端解码）；类型与体积由服务端嗅探校验，中文原因在 ``detail.message``。
 */
export async function uploadChatAttachmentDraft(
  content: Blob,
  filename: string,
  uploadId: string,
  signal?: AbortSignal
): Promise<ChatAttachmentDraftProjection> {
  const res = await fetch(`${API_BASE}/chat/attachment-drafts`, {
    method: "POST",
    headers: {
      "Content-Type": content.type || "application/octet-stream",
      "X-Bridges-Filename": encodeURIComponent(filename),
      "X-Bridges-Upload-Id": uploadId,
    },
    credentials: "same-origin",
    body: content,
    signal,
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/** 列出当前账户全部未发送照片草稿（刷新/重开后恢复草稿列表）。 */
export async function listChatAttachmentDrafts(): Promise<
  ChatAttachmentDraftProjection[]
> {
  const res = await fetch(`${API_BASE}/chat/attachment-drafts`, {
    credentials: "same-origin",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/** 草稿图片的同源预览地址（经账户授权后返回；跨账户 404 不泄漏）。 */
export function chatAttachmentDraftContentUrl(objectId: string): string {
  return `${API_BASE}/chat/attachment-drafts/${encodeURIComponent(objectId)}/content`;
}

/** 已绑定附件的同源下载地址（经账户与会话授权；用于消息内缩略图）。 */
export function chatAttachmentContentUrl(
  conversationId: string,
  objectId: string
): string {
  return `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/attachments/${encodeURIComponent(objectId)}/download`;
}

/** 移除一条草稿（幂等删除；发送成功后草稿随绑定自动消失）。 */
export async function removeChatAttachmentDraft(objectId: string): Promise<void> {
  const res = await fetch(
    `${API_BASE}/chat/attachment-drafts/${encodeURIComponent(objectId)}`,
    { method: "DELETE", credentials: "same-origin" }
  );
  if (!res.ok) throw await parseApiError(res);
}

/**
 * 订阅生成运行的持久化事件（游标续读，断线由调用方重连）。
 *
 * Issue 02：事件由后台执行器持久化，本订阅只回放与等待——客户端断开
 * 只移除订阅者，不改变运行状态。运行终态时全部事件（含终态事件）已
 * 可读，回放完即结束；进行中由心跳保持连接。``signal`` 用于停止/卸载
 * 时中断订阅（不影响运行）。
 */
export async function subscribeChatRunEvents(
  conversationId: string,
  messageId: string,
  cursor: number,
  onEvent: (event: ChatStreamEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  const res = await fetch(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}/events?cursor=${cursor}`,
    {
      credentials: "same-origin",
      cache: "no-store",
      signal,
    }
  );
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

/**
 * 重试失败的助手消息：创建新尝试与 queued 运行（与发送同一外壳）。
 *
 * V2 Issue 11：``moduleId`` 用于「点击建议一键以原文启动模块」——服务端
 * 只在该轮用户消息本身没有模块时生效，绝不改写历史的逐消息标识。
 */
export async function retryChatRun(
  conversationId: string,
  messageId: string,
  idempotencyKey?: string,
  moduleId?: ChatModuleId
): Promise<ChatRunStartedResponse> {
  const res = await fetch(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}/retry`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      // V2 Issue 02：携带幂等键——网络重试复用同一运行，不重复创建尝试。
      body: JSON.stringify({
        ...(idempotencyKey ? { idempotency_key: idempotencyKey } : {}),
        ...(moduleId ? { module_id: moduleId } : {}),
      }),
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
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

// ---------------------------------------------------------------------------
// Issue 24：跨内容统一桌面搜索
// ---------------------------------------------------------------------------

/** 统一搜索的结果类型筛选值（与契约 SearchResultItem.result_type 一致）。 */
export type SearchResultType = SearchResultItem["result_type"];

export interface UnifiedSearchParams {
  q: string;
  types?: SearchResultType[];
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

export async function listFourDimensionProfileRecords(): Promise<FourDimensionProfileRecord[]> {
  const res = await fetch(`${API_BASE}/profiles/four-dimensions`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function getFourDimensionProfileStatus(): Promise<ProfileStatusProjection> {
  const res = await fetch(`${API_BASE}/profiles/status`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function modifyFourDimensionProfileRecord(
  recordId: string,
  request: FourDimensionProfileModifyRequest
): Promise<FourDimensionProfileRecord> {
  const res = await fetch(
    `${API_BASE}/profiles/four-dimensions/${encodeURIComponent(recordId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(request),
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function withdrawFourDimensionProfileRecord(
  recordId: string,
  version: number
): Promise<FourDimensionProfileRecord> {
  const res = await fetch(
    `${API_BASE}/profiles/four-dimensions/${encodeURIComponent(recordId)}/withdraw`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ version }),
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function deleteFourDimensionProfileRecord(
  recordId: string,
  version: number
): Promise<void> {
  const res = await fetch(
    `${API_BASE}/profiles/four-dimensions/${encodeURIComponent(recordId)}`,
    {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ version }),
    }
  );
  if (!res.ok) throw await parseApiError(res);
}

// ---------- 原子长期信息列表（V2 Issue 08） ----------

export async function listAtomicProfileItems(): Promise<
  AtomicProfileItemProjection[]
> {
  const res = await fetch(`${API_BASE}/profiles/items`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function modifyAtomicProfileItem(
  itemId: string,
  request: AtomicProfileItemModifyRequest
): Promise<AtomicProfileItemProjection> {
  const res = await fetch(
    `${API_BASE}/profiles/items/${encodeURIComponent(itemId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(request),
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function deleteAtomicProfileItem(
  itemId: string,
  version: number
): Promise<void> {
  const res = await fetch(
    `${API_BASE}/profiles/items/${encodeURIComponent(itemId)}`,
    {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ version }),
    }
  );
  if (!res.ok) throw await parseApiError(res);
}

export async function removeAvatar(): Promise<Account> {
  const res = await fetch(`${API_BASE}/auth/profile/avatar`, {
    method: "DELETE",
    credentials: "same-origin",
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

/** 取消任务：本地标记为「取消中」，worker 收敛为已取消；迟到结果不发布。 */
export async function cancelVideoTask(
  conversationId: string,
  taskId: string
): Promise<VideoTaskProjection> {
  const res = await fetch(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/video-tasks/${encodeURIComponent(taskId)}/cancel`,
    { method: "POST", credentials: "same-origin" }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/** 重试失败任务：同输入（提示不变）重新入队，固定同一模型快照。 */
export async function retryVideoTask(
  conversationId: string,
  taskId: string
): Promise<VideoTaskProjection> {
  const res = await fetch(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/video-tasks/${encodeURIComponent(taskId)}/retry`,
    { method: "POST", credentials: "same-origin" }
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

/** 修改可访问文字说明（来源标记为 manual）。 */
export async function updateVideoDescription(
  conversationId: string,
  assetId: string,
  description: string
): Promise<VideoAssetProjection> {
  const res = await fetch(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/video-assets/${encodeURIComponent(assetId)}/description`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ description }),
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

/** 删除资产并返回影响说明（对象数/消息引用/对象处置）；幂等。 */
export async function deleteVideoAsset(
  conversationId: string,
  assetId: string
): Promise<VideoDeletionProjection> {
  const res = await fetch(
    `${API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/video-assets/${encodeURIComponent(assetId)}`,
    { method: "DELETE", credentials: "same-origin" }
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

// ---------------------------------------------------------------------------
// Issue 37: 数据生命周期（导出/删除/备份/恢复）
// ---------------------------------------------------------------------------

export type ExportCategoryProjection = components["schemas"]["ExportCategoryProjection"];
export type ExportPreviewProjection = components["schemas"]["ExportPreviewProjection"];
export type AccountDeletionProjection = components["schemas"]["AccountDeletionProjection"];
export type RestorePreview = components["schemas"]["RestorePreview"];

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
