import type {
  ArxivSearchProjection,
  CareerPlanProjection,
  ChatAttachmentProjection,
  CommuteRouteProjection,
  ContextNoteProjection,
  GithubProjectsProjection,
  LearningResourcesProjection,
  ModuleSuggestionProjection,
  PaperSearchProjection,
  RetrievalRoundProjection,
  TeachingTurnProjection,
  TiebaResearchProjection,
  TurnResultProjection,
  WebSearchProjection,
} from "@/lib/api";
import type {
  CareerPlanningProjection,
  ChatStreamCareerData,
  ChatStreamHumanizerData,
  ChatStreamNodeData,
  ChatStreamStageData,
  HumanizerResultProjection,
  ImageTaskProjection,
  VideoTaskProjection,
} from "@/lib/api";

/**
 * 消息呈现协议：只描述一条消息在界面上的可读数据与来源投影，
 * 不包含 React 节点或组件状态。渲染容器由呈现层组装。
 */
export interface ChatThinking {
  /** 可公开的处理步骤（生成中会增长） */
  steps: string[];
  /** 回答采用的证据/来源说明（可空） */
  evidence: string[];
  /** 工具调用进度说明（可空） */
  tools: string[];
  /** 质量检查结论（完成/失败/停止的中文状态，可空） */
  quality: string[];
  /** 生成耗时（秒，来自真实生命周期 duration_ms）；流式中为空 */
  seconds: number | null;
}

export interface ChatPreviousAttempt {
  attemptNumber: number;
  status: "done" | "streaming" | "error" | "stopped";
  errorMessage?: string | null;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  /** 该轮助手尝试序号（从 1 开始）；用户消息或缺省时为空 */
  attemptNumber?: number;
  /** 消息的纯文本（用于复制与朗读） */
  plainText: string;
  thinking?: ChatThinking;
  status?: "done" | "streaming" | "error" | "stopped";
  errorText?: string;
  /** 已提交的公开回合结果投影（交付/阻塞/恢复/等待） */
  turnResult?: TurnResultProjection | null;
  /** 本条助手消息绑定的分层检索轮次（含引用），无轮次为 null */
  retrieval?: RetrievalRoundProjection | null;
  /** 本条助手消息绑定的公网搜索状态与真实引用 */
  webSearch?: WebSearchProjection | null;
  /** 本条助手消息绑定的 arXiv 搜索状态与真实论文引用 */
  arxivSearch?: ArxivSearchProjection | null;
  /** 学习模式的教学目标、证据门与理解检查记录 */
  teaching?: TeachingTurnProjection | null;
  /** 本次上下文说明披露（画像切片/材料类别/用途）；无披露为 null */
  contextNote?: ContextNoteProjection | null;
  /** 流式中的统一阶段状态（检索/生成/检查/收尾，脱敏） */
  stage?: ChatStreamStageData | null;
  /** 进行中的父图节点（started 时非空，completed 即清空） */
  node?: ChatStreamNodeData | null;
  /** 文章人味化结果投影（助手消息）；非人味化消息为 null */
  humanizer?: HumanizerResultProjection | null;
  /** 用户消息的 SKILL 载荷快照（任务摘要展示）；普通消息为 null */
  skill?: unknown;
  /** 流式中的文章人味化过程卡状态（五态中文） */
  humanizerProcess?: ChatStreamHumanizerData | null;
  /** 生涯规划结果投影（助手消息）；非规划消息为 null */
  careerPlanning?: CareerPlanningProjection | null;
  /** 流式中的生涯规划过程卡状态（五态中文） */
  careerProcess?: ChatStreamCareerData | null;
  /** 本条助手消息的图片任务/资产状态快照（历史只读卡） */
  image?: ImageTaskProjection | null;
  video?: VideoTaskProjection | null;
  /** 本轮用户消息绑定的照片附件（按页序）；纯文字消息为空 */
  attachments?: ChatAttachmentProjection[] | null;
  /** 用户消息的逐条显式模块标识（重开后不随新选择改变） */
  moduleId?: string | null;
  /** 本条助手消息的论文模块状态（查询/来源/等待/失败/停止） */
  paperSearch?: PaperSearchProjection | null;
  /** 本条助手消息的贴吧信息搜集状态（已读帖子/帖链降级/官方核验） */
  tiebaResearch?: TiebaResearchProjection | null;
  /** 本条助手消息的职业规划状态（检索计划/岗位样本/剔除依据/统计口径） */
  careerPlan?: CareerPlanProjection | null;
  /** 本条助手消息的学习资料推荐状态（原词/层次/图书与视频清单） */
  learningResources?: LearningResourcesProjection | null;
  /** 本条助手消息的 GitHub 项目推荐状态（候选仓库/功能匹配/维护与许可） */
  githubProjects?: GithubProjectsProjection | null;
  /** 本条助手消息的校园通勤状态（起终点/路线/缓冲/等待/失败） */
  commuteRoute?: CommuteRouteProjection | null;
  /** 普通聊天中的一键模块建议（只建议，未检索） */
  moduleSuggestion?: ModuleSuggestionProjection | null;
  /** 该轮用户消息之下的历史助手尝试（重试保留审计，不静默改写） */
  previousAttempts?: ChatPreviousAttempt[];
}
