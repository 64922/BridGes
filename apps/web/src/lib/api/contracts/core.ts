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

// 改进工单 38：公开回合结果投影（交付分类/可信状态/真实恢复与等待语义）。
export type TurnResultProjection = components["schemas"]["TurnResultProjection"];

export type TurnResultBlock = components["schemas"]["TurnResultBlock"];

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

export type ImageRequestPayload = components["schemas"]["ImageRequestPayload"];

export type ChatStreamImageData = components["schemas"]["ChatStreamImageData"];

// Issue 32：文生视频契约（生成类型来自 openapi.json；Wan 固定绑定）。
export type VideoTaskProjection = components["schemas"]["VideoTaskProjection"];

export type VideoTaskStatus = components["schemas"]["VideoTaskStatus"];

export type VideoAssetProjection = components["schemas"]["VideoAssetProjection"];

export type VideoDescriptionSource = components["schemas"]["VideoDescriptionSource"];

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

export type PaperRequirementEvidence = components["schemas"]["PaperRequirementEvidence"];

export type PaperConditionSource = components["schemas"]["PaperConditionSource"];

export type PaperCandidateProjection = components["schemas"]["PaperCandidateProjection"];

export type PaperCitationEdge = components["schemas"]["PaperCitationEdge"];

// V2 Issue 14：贴吧信息搜集模块投影（真实读到的帖子/楼层、帖链降级与官方核验）。
export type TiebaResearchProjection = components["schemas"]["TiebaResearchProjection"];

// 工单 12：旧学习会话主动续学（本人目标候选、仍可访问旧材料与请求）。
export type StudyRestartOptionsProjection = components["schemas"]["StudyRestartOptionsProjection"];

export type StudyRestartRequest = components["schemas"]["StudyRestartRequest"];

export type StudyGoalSummaryProjection = components["schemas"]["StudyGoalSummaryProjection"];
