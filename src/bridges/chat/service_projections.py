# mypy: disable-error-code="misc"

"""聊天服务的消息与会话投影方法。"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import ValidationError

from bridges.arxiv_mcp.contracts import ArxivSearchProjection
from bridges.career_plan.contracts import CareerPlanProjection
from bridges.chat.repository import (
    GenerationRunRecord,
    MessageRecord,
    ModeEventRecord,
)
from bridges.chat.service_helpers import (
    _humanizer_projection,
    _route_projection,
)
from bridges.chat.turn_result import derive_turn_result
from bridges.commute.contracts import CommuteRouteProjection
from bridges.contracts.career import CareerPlanningProjection
from bridges.contracts.chat import (
    CHAT_MODULE_VALUES,
    ChatConversationProjection,
    ChatMessageProjection,
    ChatMessageRole,
    ChatMessageStatus,
    ChatMode,
    ChatModeEventProjection,
    ChatModuleId,
    ChatPluginSelectionItem,
    ChatRunStatus,
    ChatRunView,
    ChatThinkingSummary,
    ContextNoteProjection,
    McpCallMessageProjection,
    ModuleSuggestionProjection,
    RemovedPluginSelection,
    TurnOutcome,
    TurnResultProjection,
)
from bridges.contracts.humanizer import (
    HUMANIZER_CHECKPOINT_KEY,
)
from bridges.contracts.image import ImageTaskProjection
from bridges.contracts.speech import ReadAloudProjection
from bridges.contracts.study_diagnosis import (
    StudyDiagnosisTaskProjection,
    StudyLearningRecordProjection,
)
from bridges.contracts.teaching import TeachingTurnProjection
from bridges.contracts.video import VideoTaskProjection
from bridges.github.contracts import GithubProjectsProjection
from bridges.paper.contracts import PaperSearchProjection
from bridges.resources.contracts import LearningResourcesProjection
from bridges.study.controls import StudyControlRepository
from bridges.study.diagnosis_repository import StudyDiagnosisRepository
from bridges.study.goals import StudyGoalRepository
from bridges.study.local_progress import StudyLocalProgressRepository
from bridges.study.record_controls import StudyRecordControlService
from bridges.study.verification import StudyVerificationRepository
from bridges.study.versions import KNOWN_STUDY_GRAPH_VERSION_SET
from bridges.tieba.contracts import TiebaResearchProjection
from bridges.web_search.contracts import WebSearchProjection

if TYPE_CHECKING:
    from bridges.chat.service import ChatService


class _ServiceProjectionsMixin:
    def _run_view(self: ChatService, run: GenerationRunRecord, account_id: str) -> ChatRunView:
        """由运行记录构造外部视图；游标取已持久化的最后事件 seq。"""
        policy = (run.config or {}).get("global_writing_policy")
        return ChatRunView(
            run_id=run.run_id,
            status=ChatRunStatus(run.status),
            stage=run.stage,
            global_writing_policy_version=(
                policy.get("version") if isinstance(policy, dict) else None
            ),
            cursor=self._repo.last_generation_event_seq(account_id, run.run_id),
            attempt_count=run.attempt_count,
            created_at=run.created_at,
            updated_at=run.updated_at,
            graph_version=run.graph_version,
            current_node=run.current_node,
            wait_reason=run.wait_reason,
            model_lock_id=run.model_lock_id,
        )
    def _turn_result_projection(
        self: ChatService,
        message: MessageRecord,
        run_view: ChatRunView | None,
    ) -> TurnResultProjection | None:
        """公开回合结果投影（工单 38）。

        复合运行随终态写入了精确结果（含阻塞结论与步骤可信状态），这里
        只补齐消息级路由能力字段；其余路径（单模块/聊天/学习/历史消息）
        按同一规则从消息终态、领域投影与路由快照确定性推导，不迁移旧行。
        只发布已提交投影，待核验草稿不在任何输入中。
        """
        if message.role is not ChatMessageRole.ASSISTANT:
            return None
        # 流式阶段只允许已持久的渐进快照；最终完成只在原子提交后发出。
        streaming = message.status is ChatMessageStatus.STREAMING
        route = message.route if isinstance(message.route, dict) else None
        parsed: TurnResultProjection | None = None
        if isinstance(message.turn_result, dict):
            try:
                parsed = TurnResultProjection.model_validate(message.turn_result)
            except ValidationError:
                parsed = None
        if streaming:
            return (
                parsed if parsed is not None and parsed.outcome is TurnOutcome.RUNNING else None
            )
        progressive = (
            parsed if parsed is not None and parsed.outcome is TurnOutcome.RUNNING else None
        )
        if progressive is not None:
            parsed = None
        if parsed is None:
            # 历史读取也需要本条运行的等待与学习来源；不能把会话当前阶段
            # 套到此前消息，或只在仍活跃的 SSE 回合中显示等待。
            run = self._repo.get_run_by_message(message.account_id, message.message_id)
            study = (
                self._study_history.read(
                    message.account_id, message.conversation_id,
                ).study
                if run is not None
                and run.graph_version in KNOWN_STUDY_GRAPH_VERSION_SET
                else None
            )
            parsed = derive_turn_result(
                status=message.status,
                error_code=message.error_code,
                route=route,
                projections={
                    "paper_search": message.paper_search,
                    "tieba_research": message.tieba_research,
                    "career_plan": message.career_plan,
                    "learning_resources": message.learning_resources,
                    "commute_route": message.commute_route,
                    "github_projects": message.github_projects,
                },
                wait_reason=(run_view.wait_reason if run_view is not None
                             else run.wait_reason if run is not None else None),
                web_search=message.web_search,
                study=study,
                study_node=run.current_node if run is not None else None,
                user_message_id=run.user_message_id if run is not None else None,
                assistant_message_id=message.message_id,
            )
        if progressive is not None:
            parsed = parsed.model_copy(update={
                "delivered": progressive.delivered,
                "trust": progressive.trust,
                "trust_label": progressive.trust_label,
                "task_id": progressive.task_id,
                "task_version": progressive.task_version,
            })
        actual = parsed.actual_module_id
        if actual is None and route is not None:
            actual = route.get("module_id") if isinstance(route.get("module_id"), str) else None
        capabilities = parsed.capability_list or [
            str(item)
            for item in (route or {}).get("capability_list") or []
            if isinstance(item, str)
        ]
        return parsed.model_copy(
            update={
                "requested_module_id": parsed.requested_module_id
                or (
                    route.get("requested_module_id")
                    if route is not None
                    and isinstance(route.get("requested_module_id"), str)
                    else None
                ),
                "actual_module_id": actual,
                "capability_list": capabilities,
                "route_source": parsed.route_source
                or (
                    route.get("route_source")
                    if route is not None and isinstance(route.get("route_source"), str)
                    else None
                ),
            }
        )
    def _project_message(
        self: ChatService,
        message: MessageRecord,
        run_view: ChatRunView | None = None,
    ) -> ChatMessageProjection:
        attachments = (
            [
                attachment.projection()
                for attachment in self._attachments.list_for_message(
                    message.account_id, message.conversation_id, message.message_id
                )
            ]
            if self._attachments is not None and message.role == ChatMessageRole.USER
            else []
        )
        return ChatMessageProjection(
            message_id=message.message_id,
            conversation_id=message.conversation_id,
            role=message.role,
            attempt_number=message.attempt_number,
            status=message.status,
            content=message.content,
            attachments=attachments,
            thinking=(
                ChatThinkingSummary(**message.thinking)
                if message.thinking is not None
                else None
            ),
            retrieval=(
                self._retrieval.round_projection(
                    message.account_id, message.message_id
                )
                if self._retrieval is not None
                and message.role == ChatMessageRole.ASSISTANT
                else None
            ),
            retrieval_decision=(
                self._retrieval.decision_projection(
                    message.account_id, message.message_id
                )
                if self._retrieval is not None
                and message.role == ChatMessageRole.ASSISTANT
                else None
            ),
            web_search=(
                WebSearchProjection(**message.web_search)
                if message.web_search is not None
                and message.role == ChatMessageRole.ASSISTANT
                else None
            ),
            arxiv_search=(
                ArxivSearchProjection(**message.arxiv_search)
                if message.arxiv_search is not None
                and message.role == ChatMessageRole.ASSISTANT
                else None
            ),
            module_id=(
                ChatModuleId(message.module_id)
                if message.module_id is not None
                and message.role == ChatMessageRole.USER
                else None
            ),
            paper_search=(
                PaperSearchProjection.model_validate(message.paper_search)
                if message.paper_search is not None
                and message.role == ChatMessageRole.ASSISTANT
                else None
            ),
            module_suggestion=(
                ModuleSuggestionProjection.model_validate(message.module_suggestion)
                if message.module_suggestion is not None
                and message.module_suggestion.get("module_id") in CHAT_MODULE_VALUES
                and message.role == ChatMessageRole.ASSISTANT
                else None
            ),
            tieba_research=(
                TiebaResearchProjection.model_validate(message.tieba_research)
                if message.tieba_research is not None
                and message.role == ChatMessageRole.ASSISTANT
                else None
            ),
            career_plan=(
                CareerPlanProjection.model_validate(message.career_plan)
                if message.career_plan is not None
                and message.role == ChatMessageRole.ASSISTANT
                else None
            ),
            github_projects=(
                GithubProjectsProjection.model_validate(message.github_projects)
                if message.github_projects is not None
                and message.role == ChatMessageRole.ASSISTANT
                else None
            ),
            learning_resources=(
                LearningResourcesProjection.model_validate(message.learning_resources)
                if message.learning_resources is not None
                and message.role == ChatMessageRole.ASSISTANT
                else None
            ),
            commute_route=(
                CommuteRouteProjection.model_validate(message.commute_route)
                if message.commute_route is not None
                and message.role == ChatMessageRole.ASSISTANT
                else None
            ),
            route=_route_projection(message.route),
            teaching=(
                TeachingTurnProjection.model_validate(message.teaching)
                if message.teaching is not None and message.role == ChatMessageRole.ASSISTANT
                else None
            ),
            context_note=(
                ContextNoteProjection.model_validate(message.context_note)
                if message.context_note is not None
                and message.role == ChatMessageRole.ASSISTANT
                else None
            ),
            skill=(
                # Issue 05：生成中途的 skill 列可能只含写作调用检查点
                # （非完整投影），不向用户面透出内部检查点键。
                None
                if isinstance(message.skill, dict)
                and HUMANIZER_CHECKPOINT_KEY in message.skill
                else message.skill
            ),
            # 助手消息的 skill 列只承载人味化结果投影（输入快照只在用户消息），
            # 直接按结果投影解析，无需魔数判别；生成中途的检查点解析失败
            # 时返回 None（刷新/轮询期间不崩）。
            humanizer=(
                _humanizer_projection(message)
                if message.skill is not None
                and message.role == ChatMessageRole.ASSISTANT
                else None
            ),
            career_planning=(
                CareerPlanningProjection.model_validate(message.career_planning)
                if message.career_planning is not None
                and message.role == ChatMessageRole.ASSISTANT
                else None
            ),
            read_aloud=(
                ReadAloudProjection.model_validate(message.read_aloud)
                if message.read_aloud is not None
                and message.role == ChatMessageRole.ASSISTANT
                else None
            ),
            # 助手消息的 image 列只承载任务/资产状态快照（请求载荷只在
            # 用户消息，由 stream 分支按角色读取，不外发为任务投影）。
            image=(
                ImageTaskProjection.model_validate(message.image)
                if message.image is not None
                and message.role == ChatMessageRole.ASSISTANT
                else None
            ),
            # 助手消息的 video 列同样只承载任务/资产状态快照（Issue 32）。
            video=(
                VideoTaskProjection.model_validate(message.video)
                if message.video is not None
                and message.role == ChatMessageRole.ASSISTANT
                else None
            ),
            # 助手消息的 mcp_call 列只承载调用结果投影（请求载荷只在用户
            # 消息，由 stream 分支按角色读取，不外发为结果投影）。
            mcp_call=(
                McpCallMessageProjection.model_validate(message.mcp_call)
                if message.mcp_call is not None
                and message.role == ChatMessageRole.ASSISTANT
                else None
            ),
            error_code=message.error_code,
            error_message=message.error_message,
            turn_result=self._turn_result_projection(message, run_view),
            duration_ms=message.duration_ms,
            model_id=message.model_id,
            run_lock_id=message.run_lock_id,
            active_run=run_view,
            created_at=message.created_at,
            updated_at=message.updated_at,
        )
    def _project_conversation(
        self: ChatService,
        account_id: str,
        conversation_id: str,
        *,
        title: str,
        mode: ChatMode,
        pinned: bool,
        project_id: str | None,
        created_at: datetime,
        updated_at: datetime,
        messages: list[MessageRecord],
        mode_events: list[ModeEventRecord] | None = None,
        plugin_selection: list[ChatPluginSelectionItem] | None = None,
        removed_selections: list[RemovedPluginSelection] | None = None,
        run_views: dict[str, ChatRunView] | None = None,
        mode_locked: bool = False,
    ) -> ChatConversationProjection:
        study_history = (
            self._study_history.read(account_id, conversation_id)
            if mode == ChatMode.STUDY
            else None
        )
        study_local_progress = (
            StudyLocalProgressRepository(self._repo.database).get(
                account_id, conversation_id
            )
            if mode == ChatMode.STUDY
            else None
        )
        study_goal = (
            StudyGoalRepository(self._repo.database).get_linked_goal(
                account_id, conversation_id
            )
            if mode == ChatMode.STUDY
            else None
        )
        study_pending_check = (
            StudyVerificationRepository(self._repo.database).pending_projection(
                account_id, conversation_id
            )
            if mode == ChatMode.STUDY
            else None
        )
        # Issue 04：学习控制、暂停与阶段小结只按账户会话读取真实记录。
        control_repository = StudyControlRepository(self._repo.database)
        study_last_control = (
            control_repository.latest_control(account_id, conversation_id)
            if mode == ChatMode.STUDY
            else None
        )
        study_pause = (
            control_repository.active_pause(account_id, conversation_id)
            if mode == ChatMode.STUDY
            else None
        )
        study_summary = (
            control_repository.latest_summary(account_id, conversation_id)
            if mode == ChatMode.STUDY
            else None
        )
        # Issue 05：学习详情按需展示诊断任务真实状态与账户级档案；
        # 非学习模式不读取，也不把队列内部字段暴露给用户。
        # Issue 06：档案读取经用户控制服务——已删除记录不显示，纠正与
        # 来源不可用按真实状态标注。
        study_diagnosis_tasks: list[StudyDiagnosisTaskProjection] = []
        study_learning_record: list[StudyLearningRecordProjection] = []
        if mode == ChatMode.STUDY:
            diagnosis_repository = StudyDiagnosisRepository(self._repo.database)
            study_diagnosis_tasks = diagnosis_repository.list_tasks(
                account_id, conversation_id
            )
            study_learning_record = StudyRecordControlService(
                self._repo.database
            ).record_projections(account_id, conversation_id)
        return ChatConversationProjection(
            conversation_id=conversation_id,
            title=title,
            mode=mode,
            study=study_history.study if study_history is not None else None,
            study_local_progress=study_local_progress,
            study_goal=study_goal,
            study_pending_check=study_pending_check,
            study_last_control=study_last_control,
            study_pause=study_pause,
            study_summary=study_summary,
            study_diagnosis_tasks=study_diagnosis_tasks,
            study_learning_record=study_learning_record,
            study_history_status=(
                study_history.status if study_history is not None else None
            ),
            mode_locked=mode_locked,
            pinned=pinned,
            project_id=project_id,
            legacy_project_name=self._repo.legacy_project_name(
                account_id, conversation_id
            ),
            plugin_selection=list(plugin_selection or []),
            removed_selections=list(removed_selections or []),
            created_at=created_at,
            updated_at=updated_at,
            messages=[
                self._project_message(message, (run_views or {}).get(message.message_id))
                for message in messages
            ],
            mode_events=[
                ChatModeEventProjection(
                    event_id=event.event_id,
                    conversation_id=event.conversation_id,
                    from_mode=ChatMode(event.from_mode),
                    to_mode=ChatMode(event.to_mode),
                    created_at=event.created_at,
                )
                for event in (mode_events or [])
            ],
        )
