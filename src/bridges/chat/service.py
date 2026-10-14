"""持久化聊天服务的公开聚合入口。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bridges.ai import ModelGateway
from bridges.ai.run_model_config import RunModelConfigProvider
from bridges.arxiv_mcp.service import ArxivSearchService
from bridges.career_plan.service import CareerPlanService
from bridges.chat.attachments import (
    ChatAttachmentService,
)
from bridges.chat.global_writing_policy import GlobalWritingPolicyCompiler
from bridges.chat.lifecycle import GenerationLifecycle
from bridges.chat.repository import (
    ConversationRepository,
)
from bridges.chat.selections import (
    ChatSelectionsService,
)
from bridges.chat.service_context import _ServiceContextMixin
from bridges.chat.service_contract import ChatDomainError as ChatDomainError
from bridges.chat.service_conversation_read import _ServiceConversationReadMixin
from bridges.chat.service_conversation_write import _ServiceConversationWriteMixin
from bridges.chat.service_feedback import _ServiceFeedbackMixin
from bridges.chat.service_generation_admission import _ServiceGenerationAdmissionMixin
from bridges.chat.service_generation_records import _ServiceGenerationRecordsMixin
from bridges.chat.service_mcp import _ServiceMcpMixin
from bridges.chat.service_profiles import _ServiceProfilesMixin
from bridges.chat.service_projections import _ServiceProjectionsMixin
from bridges.chat.service_retry import _ServiceRetryMixin
from bridges.chat.service_run_budget import _ServiceRunBudgetMixin
from bridges.chat.service_stream import _ServiceStreamMixin
from bridges.chat.service_study_restart import _ServiceStudyRestartMixin
from bridges.chat.service_turn_routing import _ServiceTurnRoutingMixin
from bridges.chat.summary import ChatSummaryService
from bridges.chat.terminal import GenerationTerminal
from bridges.chat.turn import (
    STREAM_INTERRUPTED_MESSAGE,  # noqa: F401 - re-export
    CareerPlannerOrchestrator,
    TurnOrchestrator,
    error_is_retryable,  # noqa: F401 - re-export
    user_facing_error,  # noqa: F401 - re-export
)
from bridges.chat.understanding import MainAgentUnderstanding
from bridges.commute.service import CommuteService
from bridges.github.service import GithubProjectsService
from bridges.learning.progress import TeachingProgressService
from bridges.learning.teaching_gate import TeachingTurnService
from bridges.mcp.service import McpService
from bridges.observability.service import ObservabilityService
from bridges.paper.service import PaperSearchService
from bridges.profiles.atomic import AtomicProfileService
from bridges.profiles.automatic import AutomaticProfileService
from bridges.profiles.four_dimensions import FourDimensionProfileService
from bridges.profiles.service import ProfileService
from bridges.resources.service import LearningResourcesService
from bridges.retrieval.service import LayeredRetrievalService
from bridges.routing import (
    NaturalLanguageRouter,
)
from bridges.study.history import StudyHistoryReader
from bridges.tieba.service import TiebaResearchService
from bridges.web_search.service import WebSearchService

if TYPE_CHECKING:
    from bridges.study.diagnosis import StudyDiagnosisService
    from bridges.tasks.service import TaskService


class ChatService(
    _ServiceConversationWriteMixin,
    _ServiceConversationReadMixin,
    _ServiceGenerationAdmissionMixin,
    _ServiceTurnRoutingMixin,
    _ServiceGenerationRecordsMixin,
    _ServiceProfilesMixin,
    _ServiceRunBudgetMixin,
    _ServiceStreamMixin,
    _ServiceRetryMixin,
    _ServiceContextMixin,
    _ServiceMcpMixin,
    _ServiceFeedbackMixin,
    _ServiceProjectionsMixin,
    _ServiceStudyRestartMixin
):
    """对话与生成编排服务；所有操作都限定在传入的账户 ID 内。"""

    def __init__(
        self,
        repository: ConversationRepository,
        gateway: ModelGateway,
        attachment_service: ChatAttachmentService | None = None,
        retrieval_service: LayeredRetrievalService | None = None,
        web_search_service: WebSearchService | None = None,
        arxiv_search_service: ArxivSearchService | None = None,
        teaching_service: TeachingTurnService | None = None,
        teaching_progress_service: TeachingProgressService | None = None,
        profile_service: ProfileService | None = None,
        automatic_profile_service: AutomaticProfileService | None = None,
        four_dimension_profile_service: FourDimensionProfileService | None = None,
        atomic_profile_service: AtomicProfileService | None = None,
        observability_service: ObservabilityService | None = None,
        career_planner_service: CareerPlannerOrchestrator | None = None,
        selections_service: ChatSelectionsService | None = None,
        mcp_service: McpService | None = None,
        writing_policy_compiler: GlobalWritingPolicyCompiler | None = None,
        model_config_provider: RunModelConfigProvider | None = None,
        paper_search_service: PaperSearchService | None = None,
        tieba_research_service: TiebaResearchService | None = None,
        career_plan_service: CareerPlanService | None = None,
        learning_resources_service: LearningResourcesService | None = None,
        commute_service: CommuteService | None = None,
        github_projects_service: GithubProjectsService | None = None,
        task_service: TaskService | None = None,
        summary_service: ChatSummaryService | None = None,
        study_diagnosis_service: StudyDiagnosisService | None = None,
    ) -> None:
        self._repo = repository
        self._gateway = gateway
        #: V2 Issue 09：主模型运行配置（手填并验证通过的主模型 ID）。运行创建
        #: 时解析一次并随运行配置持久化，进行中的轮次不因换配置而切换模型。
        self._model_config_provider = model_config_provider
        self._attachments = attachment_service
        self._study_history = StudyHistoryReader(
            repository.database,
            material_status=(
                self._study_material_status if attachment_service is not None else None
            ),
        )
        #: 分层本地检索（Issue 20）；未挂载时生成不检索、不产生引用。
        self._retrieval = retrieval_service
        #: 明确联网/时效/核查请求的固定 Tavily 搜索（Issue 01/21）。
        self._web_search = web_search_service
        #: 受限内置 arXiv MCP（Issue 22）；结果失败时不调用模型兜底。
        self._arxiv_search = arxiv_search_service
        #: V2 Issue 11：论文搜索模块子图（显式 module_id=paper 时派发；
        #: 未装配时该模块如实报不可用，绝不降级为普通对话）。
        self._paper_search = paper_search_service
        #: V2 Issue 14：贴吧信息搜集模块子图（显式 module_id=tieba 时派发；
        #: 未装配时该模块如实报不可用，绝不降级为普通对话）。
        self._tieba_research = tieba_research_service
        #: V2 Issue 15：职业规划模块子图（显式 module_id=career 时派发；
        #: 未装配时该模块如实报不可用，绝不降级为普通对话）。
        self._career_plan = career_plan_service
        #: V2 Issue 13：学习资料推荐模块子图（显式 module_id=resources 时派发；
        #: 未装配时该模块如实报不可用，绝不降级为普通对话）。
        self._learning_resources = learning_resources_service
        #: V2 Issue 12：校园通勤模块子图（显式 module_id=commute 时派发；
        #: 未装配或未配置高德凭据时如实降级，绝不改走普通对话或编造路线）。
        self._commute = commute_service
        #: V2 Issue 16：GitHub 项目推荐模块子图（显式 module_id=github 时派发；
        #: 未装配时该模块如实报不可用，绝不降级为普通对话）。
        self._github_projects = github_projects_service
        #: 改进工单 11：任务查询接缝（工单 08 的任务领域）。编译上下文时
        #: 读取当前任务快照供指代解析定位条件与纠正关系；未装配时解析只用
        #: 会话原文（任务条件仍由 Issue 08 的写模型拥有）。
        self._tasks = task_service
        #: 改进工单 13：有界历史摘要缓存与后台准备（按来源片段复用/失效）。
        #: 未装配时编译器只用有界确定性回退行，不长出摘要调用。
        self._summaries = summary_service
        if self._attachments is not None:
            self._attachments.set_summary_service(summary_service)
        #: 学习模式教学证据门与统一聊天教学轮次（Issue 23）。
        self._teaching = teaching_service or TeachingTurnService()
        self._teaching_progress = teaching_progress_service or TeachingProgressService(
            repository.database
        )
        #: 画像记忆意图处理（Issue 26）；未挂载时聊天不产生画像通知。
        self._profiles = profile_service
        #: Issue 15：默认自动抽取；启用后不再走旧的画像写入通知路径。
        self._automatic_profiles = automatic_profile_service
        #: Issue 05：后台独立诊断与学习档案；未挂载时作答只写证据，
        #: 不登记诊断任务（测试内存环境与旧装配路径保持可用）。
        self._study_diagnosis = study_diagnosis_service
        self._four_dimension_profiles = four_dimension_profile_service
        #: V2 Issue 08：用户可见的无类别原子画像列表。
        self._atomic_profiles = atomic_profile_service
        #: 云端披露审计（Issue 27）；未挂载时跳过审计，不阻断生成。
        self._observability = observability_service
        #: 生涯规划编排（Issue 29）；未挂载时规划意图按普通消息处理。
        self._career_planner = career_planner_service
        #: 对话级插件选择域（Issue 36）：选择校验、失效清洗与工具上下文
        #: 编译；未挂载时不注入工具集合、不校验选择（内存测试环境）。
        self._selections = selections_service
        #: MCP 服务器服务（Issue 36）：聊天内对选中 MCP 的真实调用与
        #: 敏感确认；未挂载时携带 mcp_call 载荷的消息按错误收敛。
        self._mcp = mcp_service
        #: Issue 17：普通自然语言回复共享的版本化轻量表达策略。
        self._writing_policy = writing_policy_compiler or GlobalWritingPolicyCompiler()
        #: 新聊天自然语言主能力路由；结果在消息上持久化后才允许外部调用。
        self._router = NaturalLanguageRouter()
        #: 改进工单 12：确定性主理解（一次判定轻量/任务关系/硬条件/实际
        #: 路由来源），不调用模型、不执行检索，普通聊天保持一次生成。
        self._understanding = MainAgentUnderstanding()
        #: 进行中生成的停止信号与活跃度跟踪（注册/续期/TTL/停止唯一入口）。
        self._lifecycle = GenerationLifecycle()
        #: 回合编排深模块（Issue 42）：生成管线（模式路由/检索/切片编译/
        #: 提示词组装/流式收敛）收敛于此，本服务只保留对话/消息持久化与投影。
        self._turn = TurnOrchestrator(
            repository=self._repo,
            lifecycle=self._lifecycle,
            retrieval_service=self._retrieval,
            web_search_service=self._web_search,
            arxiv_search_service=self._arxiv_search,
            teaching_service=self._teaching,
            teaching_progress_service=self._teaching_progress,
            profile_service=self._profiles,
            automatic_profile_service=self._automatic_profiles,
            four_dimension_profile_service=self._four_dimension_profiles,
            atomic_profile_service=self._atomic_profiles,
            observability_service=self._observability,
            career_planner_service=self._career_planner,
            selections_service=self._selections,
            mcp_service=self._mcp,
            writing_policy_compiler=self._writing_policy,
            attachment_service=self._attachments,
        )
        #: 生成终态 module（Issue 01/02）：图边界提前终止、排队期停止与
        #: 停止接口兜底共用同一实例；跨对象提交顺序、判重与重放都在
        #: module 内部，调用者只提供标识与兜底结果。
        self.terminal = GenerationTerminal(
            self._repo, projection=self.message_projection
        )

    def _study_material_status(self, account_id: str, object_id: str) -> str | None:
        """经附件域读取学习历史引用材料的账户范围内存储状态。"""
        if self._attachments is None:
            return None
        return self._attachments.object_status(account_id, object_id)
