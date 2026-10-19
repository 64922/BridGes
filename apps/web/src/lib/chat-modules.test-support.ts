import type { CareerPlanProjection, ChatMessageProjection, CommuteRouteProjection, GithubProjectsProjection, LearningResourcesProjection, PaperSearchProjection, TiebaResearchProjection } from "@/lib/api";

/** 论文投影：只填本测试关心的字段，其余用真实的完整形态。 */
export function paperSearch(overrides: Partial<PaperSearchProjection> = {}): PaperSearchProjection {
  return {
    status: "success",
    original_phrase: "Transformer",
    normalized_term: "Transformer",
    expansions: [],
    confidence: 0.9,
    context_label: null,
    research_question: "Transformer 的研究问题",
    queries: [],
    final_query: "transformer",
    papers: [],
    requested_count: 3,
    evidence_notes: [],
    pending: null,
    searched_at: null,
    error_code: null,
    error_message: null,
    retryable: false,
    ...overrides,
  };
}

/** 资料推荐投影（V2 Issue 13）：同样只填本测试关心的字段。 */
export function learningResources(
  overrides: Partial<LearningResourcesProjection> = {}
): LearningResourcesProjection {
  return {
    status: "success",
    original_phrase: "深度学习",
    normalized_term: "深度学习",
    expansions: ["deep learning"],
    confidence: 0.7,
    goal: null,
    level_label: "零基础入门",
    level_basis: "你说了「零基础」",
    queries: [],
    final_query: "深度学习 deep learning",
    items: [],
    requested_books: 2,
    requested_videos: 3,
    needs_practice_project: false,
    path_verified: false,
    parallel_limit: 2,
    evidence_notes: [],
    pending: null,
    searched_at: null,
    error_code: null,
    error_message: null,
    retryable: false,
    ...overrides,
  };
}

/** 通勤投影：只填本测试关心的字段，其余用真实的完整形态。 */
export function commuteRoute(
  overrides: Partial<CommuteRouteProjection> = {}
): CommuteRouteProjection {
  return {
    status: "success",
    mode: "walking",
    mode_label: "步行",
    mode_phrase: null,
    origin: null,
    destination: null,
    origin_candidates: [],
    destination_candidates: [],
    distance_m: 1800,
    base_duration_seconds: 1400,
    suggested_total_seconds: 1400,
    steps: [],
    polyline: [],
    path_verified: false,
    buffer: null,
    queries: [],
    evidence_notes: [],
    pending: null,
    resolved_at: null,
    error_code: null,
    error_message: null,
    retryable: false,
    ...overrides,
  };
}

/** 历史消息投影：只填本测试关心的字段。 */
export function message(
  id: string,
  search: PaperSearchProjection | null,
  tieba: TiebaResearchProjection | null = null,
  resources: LearningResourcesProjection | null = null,
  career: CareerPlanProjection | null = null,
  github: GithubProjectsProjection | null = null
): ChatMessageProjection {
  return {
    message_id: id,
    conversation_id: "conversation-1",
    role: "assistant",
    attempt_number: 1,
    status: "done",
    content: "回答",
    paper_search: search,
    tieba_research: tieba,
    learning_resources: resources,
    career_plan: career,
    github_projects: github,
    created_at: "2026-09-20T10:00:00Z",
    updated_at: "2026-09-20T10:00:01Z",
  };
}

/** GitHub 项目推荐投影（V2 Issue 16）：只填本测试关心的字段。 */
export function githubProjects(
  overrides: Partial<GithubProjectsProjection> = {}
): GithubProjectsProjection {
  return {
    status: "success",
    scenario: "校园二手书交换平台",
    original_request: "我想做一个校园二手书交换平台",
    features: ["发布想卖的书", "线下交换"],
    tech_terms: [],
    whole_idea: true,
    component_terms: [],
    context_source: null,
    queries: [],
    recommendations: [],
    rejected: [],
    rate_limit: {
      limited: false,
      note: null,
    },
    evidence_boundary: [],
    empty_reason: null,
    retryable: false,
    error_code: null,
    error_message: null,
    completed_at: null,
    pending: null,
    ...overrides,
  };
}

/** 贴吧投影：只填本测试关心的字段。 */
export function tiebaResearch(
  overrides: Partial<TiebaResearchProjection> = {}
): TiebaResearchProjection {
  return {
    status: "success",
    topic: "宿舍条件",
    original_question: "华东交通大学吧里最近的宿舍条件怎么样",
    topic_terms: ["宿舍", "条件"],
    place_or_event: [],
    question_kind: "experience",
    parallel_evidence: false,
    time_filter: { requirement: null, year: null, applied: false, mode: "unavailable", eligible_count: 0, context_only_count: 0, excluded_count: 0, history_supplemented: false, recent_count: 0, historical_count: 0, unknown_count: 0, note: "未提出时间条件。" },
    queries: [],
    forum: "华东交通大学吧",
    confirmed_posts: [],
    candidate_links: [],
    rejected_candidates: [],
    official_check_requested: false,
    official_checks: [],
    sections: [],
    evidence_boundary: [],
    empty_reason: null,
    retryable: false,
    error_code: null,
    error_message: null,
    completed_at: null,
    pending: null,
    recovery: null,
    ...overrides,
  };
}

/** 通勤历史消息投影（模块标识随用户消息持久化）。 */
export function commuteMessage(
  id: string,
  route: CommuteRouteProjection | null
): ChatMessageProjection {
  return {
    ...message(id, null),
    commute_route: route,
  };
}

/** 职业规划投影：只填本测试关心的字段，其余用真实的完整形态。 */
export function careerPlan(overrides: Partial<CareerPlanProjection> = {}): CareerPlanProjection {
  return {
    status: "success",
    topic: "后端开发",
    original_request: "我想找 Java 后端开发的工作，城市南昌",
    branch: "job_intel",
    job_terms: ["Java 后端开发"],
    family_title: "后端开发",
    stage: null,
    graduation_year: null,
    cities: ["南昌"],
    constraints: [],
    plan: [],
    queries: [],
    samples: [],
    candidate_links: [],
    rejected: [],
    analysis: null,
    advices: [],
    adjacent_suggestions: [],
    evidence_boundary: [],
    empty_reason: null,
    retryable: false,
    error_code: null,
    error_message: null,
    completed_at: null,
    pending: null,
    ...overrides,
  };
}

export const pendingPaperClarification: PaperSearchProjection = paperSearch({
  status: "clarification",
  pending: {
    module_id: "paper",
    kind: "clarification",
    question: "你想找哪一个？",
    origin_message_id: "a-1",
    context: {},
    created_at: "2026-09-20T10:00:00Z",
  },
})

export const pendingResourcesClarification: LearningResourcesProjection = learningResources({
  status: "clarification",
  pending: {
    module_id: "resources",
    kind: "clarification",
    question: "你现在的学习层次是哪一档？",
    origin_message_id: "a-2",
    context: {},
    created_at: "2026-09-20T10:00:00Z",
  },
})
