import type { components } from "@bridges/contracts";

export type TiebaResearchStatus = components["schemas"]["TiebaResearchStatus"];

export type TiebaTimeFilter = components["schemas"]["TiebaTimeFilter"];

export type TiebaPostProjection = components["schemas"]["TiebaPostProjection"];

export type TiebaSummaryCitation = components["schemas"]["TiebaSummaryCitation"];

export type TiebaReply = components["schemas"]["TiebaReply"];

export type TiebaCandidateLink = components["schemas"]["TiebaCandidateLink"];

export type TiebaRejectedCandidate = components["schemas"]["TiebaRejectedCandidate"];

export type TiebaOfficialCheck = components["schemas"]["TiebaOfficialCheck"];

// V2 Issue 15：职业规划模块投影（检索计划、公开岗位样本、剔除依据、统计口径与建议）。
export type CareerPlanProjection = components["schemas"]["CareerPlanProjection"];

export type CareerPlanStatus = components["schemas"]["CareerPlanStatus"];

export type CareerQueryPlanItem = components["schemas"]["CareerQueryPlanItem"];

export type JobSample = components["schemas"]["JobSample"];

export type JobReadStatus = components["schemas"]["JobReadStatus"];

export type CareerRejectedSample = components["schemas"]["CareerRejectedSample"];

export type CareerCandidateLink = components["schemas"]["CareerCandidateLink"];

export type CareerAnalysis = components["schemas"]["CareerAnalysis"];

export type SalaryInterval = components["schemas"]["SalaryInterval"];

export type SkillStat = components["schemas"]["SkillStat"];

export type CityCount = components["schemas"]["CityCount"];

export type CareerAdviceItem = components["schemas"]["CareerAdviceItem"];

export type AdjacentJobSuggestion = components["schemas"]["AdjacentJobSuggestion"];

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
