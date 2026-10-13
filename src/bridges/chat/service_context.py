# mypy: disable-error-code="misc"

"""聊天服务的上下文与模块方法。"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from bridges.ai.adapters import StreamEvent
from bridges.ai.model_quota import (
    RUN_MODEL_QUOTA_CONFIG_KEY,
    RunModelQuota,
    resolve_run_quota,
)
from bridges.ai.payload_budget import CallMaterialManifest
from bridges.career_plan.service import CareerPlanService
from bridges.chat.context_compiler import CompiledTurnContext, ContextEvidence
from bridges.chat.context_compiler import compile_turn_context as _compile_turn_context
from bridges.chat.graph import run_daily_turn
from bridges.chat.lightweight_policy import (
    DEFAULT_OUTPUT_TOKENS,
    continuation_source_text,
    output_tokens_for_request,
)
from bridges.chat.material_reading import collect_photo_refs, plan_photo_reads
from bridges.chat.repository import (
    GenerationRunRecord,
)
from bridges.chat.service_contract import ChatDomainError
from bridges.chat.task_materials import (
    MODULE_DECLARATIONS,
    ModuleTaskContext,
    build_module_context,
)
from bridges.chat.turn import (
    CHAT_MODE,
    # STREAM_INTERRUPTED_MESSAGE 在此 re-export，保持既有导入路径不变
    #（定义在 chat/turn.py；读取路径的陈旧收敛已迁入终态 module）。
    STREAM_INTERRUPTED_MESSAGE,  # noqa: F401 - re-export
    capability_route_from,
    # error_is_retryable / user_facing_error 在此 re-export，保持 api/chat.py
    # 的既有导入路径不变（定义在 chat/turn.py）。
    error_is_retryable,  # noqa: F401 - re-export
    user_facing_error,  # noqa: F401 - re-export
)
from bridges.commute.service import CommuteService
from bridges.contracts.chat import (
    ChatMode,
)
from bridges.contracts.image import ImageTaskKind
from bridges.contracts.observability import AuditAction, AuditResult
from bridges.contracts.summaries import HistorySummary
from bridges.github.service import GithubProjectsService
from bridges.paper.service import PaperSearchService
from bridges.resources.service import LearningResourcesService
from bridges.retrieval.decision import capability_route_for_request
from bridges.study.service import STUDY_GRAPH_VERSION, StudyWorkflow
from bridges.tieba.service import TiebaResearchService

if TYPE_CHECKING:
    from bridges.chat.service import ChatService


class _ServiceContextMixin:
    def ensure_turn_context(self: ChatService, run: GenerationRunRecord) -> None:
        """编译本轮上下文基础：检索决策（幂等；Issue 03 扩展为编译器）。

        由日常父图 ``compile_context`` 节点调用。入队前的服务层已按同一
        参数保存过决策时，这里是幂等补齐（租约恢复/重试路径同样有效）。
        """
        user_message = self._repo.get_message(run.account_id, run.user_message_id)
        if user_message is None:
            return
        conversation = self._repo.get_conversation(
            run.account_id, run.conversation_id
        )
        mode = (
            ChatMode(conversation.mode)
            if conversation is not None
            else CHAT_MODE
        )
        self._ensure_retrieval_decision(
            account_id=run.account_id,
            conversation_id=run.conversation_id,
            assistant_message_id=run.assistant_message_id,
            user_message_id=run.user_message_id,
            query=user_message.content,
            mode=mode,
            use_knowledge_base=(run.config or {}).get("use_knowledge_base", True),
            image_payload=user_message.image,
            video_payload=user_message.video,
            mcp_call_payload=user_message.mcp_call,
        )
    def compile_turn_context(
        self: ChatService, run: GenerationRunRecord, *,
        system_prompt: str | None = None,
        evidence: list[ContextEvidence] | None = None,
    ) -> tuple[list[dict[str, str]] | None, dict[str, object] | None]:
        """编译本轮模型输入上下文（V2 Issue 03）。

        由日常父图 ``compile_context`` 节点调用：以原始消息为权威源，在
        锁定模型的已验证窗口内产出「当前请求 + 近期原文 + 较早摘要 + 按需
        补回的原文」（详见 ``context_compiler``）。审计由本方法内部单独落库
        （``to_record()``：模型 ID、预算/摘要/估算版本、采用的原文消息 ID，
        不含任何正文），返回的第二个元素是**执行记录**：它与检查点同一
        边界，携带工单 15 的各域最小查询文本，只供进程内检索与恢复复用，
        绝不写审计、不外发公开服务。

        当前轮绑定照片附件时（改进工单 14）同样走本编译：照片与按问题读取的
        历史原图以实际图片部件与数量进入统一输入预算，读取计划随编译记录
        落库；原图字节不进检查点，由回合层按计划读取一次。
        """
        user_message = self._repo.get_message(run.account_id, run.user_message_id)
        if user_message is None:
            raise ChatDomainError(
                "message_not_found", "消息不存在或没有访问权限。", 404
            )
        conversation = self._repo.get_conversation(
            run.account_id, run.conversation_id
        )
        mode = (
            ChatMode(conversation.mode)
            if conversation is not None
            else CHAT_MODE
        )
        # 本轮启动时锁定的主模型与完整额度快照（V2 Issue 09 + 改进工单 03）：
        # 编译与网关读同一份运行额度快照，取已验证窗口与最大输入额度的较小
        # 值作为输入上界。快照缺失或额度未验证时**明确闭锁**，绝不用 32,768
        # 之类缺省常数冒充该模型真实额度（排队期间换配置的旧运行因此不会被
        # 静默高估或低估）。
        current_snapshot = (
            self._model_config_provider.snapshot()
            if self._model_config_provider is not None
            else None
        )
        resolution = resolve_run_quota(
            run.config, current_snapshot=current_snapshot
        )
        if not resolution.resolved or resolution.quota is None:
            raise ChatDomainError(
                "model_quota_unverified",
                "本轮锁定的模型额度无法验证（可能是排队期间更换了运行配置）："
                "为避免用未经验证的窗口发起调用，本轮已闭锁；"
                "请核对模型配置，无法补齐时请发送新消息。",
                409,
            )
        quota = resolution.quota
        if RUN_MODEL_QUOTA_CONFIG_KEY not in (run.config or {}):
            # 兼容补齐只执行一次：先持久化，再编译/调用；恢复和网关读取同一份。
            config = dict(run.config or {})
            config["run_model_id"] = quota.model_id
            config[RUN_MODEL_QUOTA_CONFIG_KEY] = quota.to_config()
            if not self._repo.update_generation_config(run.account_id, run.run_id, config):
                raise ChatDomainError("run_not_active", "本轮已结束，无法补齐模型额度。", 409)
            run.config = config
        # 改进工单 14：照片轮不再绕过编译。当前照片与按问题读取的历史原图
        # 以实际图片部件与数量进入统一输入预算；这里只读附件引用做计划，
        # 不下载、不储存原图（原图字节由回合层按计划读取一次）。
        messages = self._repo.list_messages(
            run.account_id, run.conversation_id
        )
        photo_refs = collect_photo_refs(
            messages,
            attachments=self._attachments,
            account_id=run.account_id,
            conversation_id=run.conversation_id,
            current_message_id=run.user_message_id,
            request=user_message.content,
        )
        read_plan = plan_photo_reads(
            request=user_message.content,
            current_message_id=run.user_message_id,
            photo_refs=photo_refs,
        )
        # 改进工单 11：指代解析的任务查询接缝（只读；工单 08 的任务领域）。
        task_context = (
            self._tasks.current_reference_context(
                run.account_id, run.conversation_id
            )
            if self._tasks is not None
            else None
        )
        # 改进工单 13：有效摘要缓存实例（只读；来源指纹已在摘要域核对）。
        summaries: list[HistorySummary] = []
        if self._summaries is not None:
            try:
                summaries = self._summaries.valid_summaries(
                    run.account_id, run.conversation_id
                )
            except Exception:  # noqa: BLE001 - 摘要域故障不阻塞本轮回答
                summaries = []

        def _persisted_output_tokens() -> int | None:
            # 改进工单 21：已固化的完整策略快照是本轮输出的权威口径（重试/
            # 恢复与最终载荷 ``max_tokens`` 共用）；旧版完整快照没有输出
            # 额度字段时按默认额度，与回合层复用旧快照的行为一致。
            policy = (run.config or {}).get("global_writing_policy")
            if not isinstance(policy, dict) or policy.get("snapshot_complete") is not True:
                return None
            tokens = policy.get("output_tokens")
            if isinstance(tokens, int) and tokens > 0:
                return tokens
            return DEFAULT_OUTPUT_TOKENS

        def _compile_turn(
            active_summaries: list[HistorySummary],
        ) -> CompiledTurnContext:
            # 改进工单 21：输出预留与表达策略同一口径——显式长文/推导任务用
            # 有界任务上限，续接轮沿用最近相关请求的任务分类；仍受工单 04
            # 最终载荷门与运行额度约束。
            output_budget = _persisted_output_tokens()
            if output_budget is None:
                output_budget = output_tokens_for_request(
                    user_message.content,
                    continuation_source_text(messages, run.user_message_id),
                )
            return _compile_turn_context(
                messages=messages,
                current_user_message_id=run.user_message_id,
                model_id=quota.model_id,
                mode=mode,
                quota=quota,
                output_tokens=output_budget,
                system_prompt=system_prompt,
                evidence=evidence or [],
                task=task_context,
                summaries=active_summaries,
                image_count=read_plan.image_count(),
                material_read_plan=read_plan.to_record(),
            )

        compiled = _compile_turn(summaries)
        # 改进工单 13：缓存缺失且确需压缩时，本轮只做**一次**限时同步补齐
        # （失败/超时立即回退到有界回退行，不无限等待）；随后把后续片段
        # 登记为回答后的后台准备。新请求与最新纠正始终以原文为准，不等缓存。
        if self._summaries is not None and compiled.summary_source_range is not None:
            boundary_message_id = compiled.summary_source_range[1]
            try:
                if not compiled.summary_cache_hit:
                    prepared = self._summaries.prepare_sync(
                        run.account_id,
                        run.conversation_id,
                        boundary_message_id=boundary_message_id,
                        run_id=run.run_id,
                    )
                    if prepared:
                        summaries = self._summaries.valid_summaries(
                            run.account_id, run.conversation_id
                        )
                        compiled = _compile_turn(summaries)
                self._summaries.schedule_background(
                    run.account_id,
                    run.conversation_id,
                    boundary_message_id=boundary_message_id,
                )
            except Exception:  # noqa: BLE001 - 后台准备失败只留缺口，不阻塞回答
                pass
        if self._observability is not None:
            record = compiled.to_record()
            record["quota_compat_applied"] = resolution.compat_applied
            self._observability.log_audit(
                actor_account_id=run.account_id,
                action=AuditAction.CONTEXT_COMPILED,
                result=AuditResult.SUCCESS,
                object_refs=[run.assistant_message_id],
                reason="本轮上下文编译记录。",
                details=record,
            )
        # 执行记录（含工单 15 的各域查询）进图状态；审计仍只写 to_record()。
        return compiled.model_messages(), compiled.execution_record()
    def run_model_quota(self: ChatService, run: GenerationRunRecord) -> RunModelQuota | None:
        """本运行锁定的额度快照（与编译/网关同源）；无法验证时返回 None。

        工单 15：模块自身的模型调用（论文概述、GitHub 借鉴角度）与主对话
        共用同一份运行额度快照，不另读当前配置。
        """
        current_snapshot = (
            self._model_config_provider.snapshot()
            if self._model_config_provider is not None
            else None
        )
        resolution = resolve_run_quota(run.config, current_snapshot=current_snapshot)
        return resolution.quota if resolution.resolved else None
    def module_task_context(
        self: ChatService, run: GenerationRunRecord, module_id: str
    ) -> ModuleTaskContext | None:
        """按模块声明构建任务上下文（只读；构建失败按无上下文处理）。

        工单 15：模块从当前任务快照与有来源的相关前文取上下文，不再各自
        固定截取最近 6 条用户消息；无任务时由 :func:`build_module_context`
        回退既有窗口。它不授予任何工具权限，也不读取画像正文。
        """
        declaration = MODULE_DECLARATIONS.get(module_id)
        if declaration is None and module_id == "paper":
            # 父图按逐消息模块 ID（paper）派发，论文声明按材料域键
            # （paper_search）登记；只对确有登记的论文别名做解析，不扩大
            # 其它模块的上下文语义（它们此前不接收任务上下文）。
            declaration = MODULE_DECLARATIONS.get("paper_search")
        if declaration is None:
            return None
        try:
            messages = self._repo.list_messages(run.account_id, run.conversation_id)
            task = (
                self._tasks.current_reference_context(
                    run.account_id, run.conversation_id
                )
                if self._tasks is not None
                else None
            )
            return build_module_context(
                declaration=declaration,
                task=task,
                messages=[
                    (message.message_id, message.role.value, message.content)
                    for message in messages
                ],
                current_user_message_id=run.user_message_id,
            )
        except Exception:  # noqa: BLE001 - 模块上下文失败不阻断模块执行
            return None
    def audit_module_manifest(
        self: ChatService,
        run: GenerationRunRecord,
        call_scope: str,
        manifest: CallMaterialManifest,
    ) -> None:
        """记录子模块模型调用自己的采用清单（工单 15）。

        清单只含材料 ID、类别、必要性、来源版本、读取范围与预算门结果，
        不含正文；不代替父图编译审计，也不代替调用方自己的运行锁。
        """
        if self._observability is None:
            return
        record = manifest.to_record()
        record["call_scope"] = call_scope
        self._observability.log_audit(
            actor_account_id=run.account_id,
            action=AuditAction.PAYLOAD_BUDGET_EVALUATED,
            result=AuditResult.SUCCESS,
            object_refs=[run.assistant_message_id],
            reason="子模块模型调用采用清单。",
            details=record,
        )
    @property
    def paper_search_service(self: ChatService) -> PaperSearchService | None:
        """论文模块子图服务（V2 Issue 11）；未装配时为 None。"""
        return self._paper_search
    @property
    def github_projects_service(self: ChatService) -> GithubProjectsService | None:
        """GitHub 项目推荐子图服务（V2 Issue 16）；未装配时为 None。"""
        return self._github_projects
    @property
    def tieba_research_service(self: ChatService) -> TiebaResearchService | None:
        """贴吧信息搜集子图服务（V2 Issue 14）；未装配时为 None。"""
        return self._tieba_research
    @property
    def career_plan_service(self: ChatService) -> CareerPlanService | None:
        """职业规划模块子图服务（V2 Issue 15）；未装配时为 None。"""
        return self._career_plan
    @property
    def learning_resources_service(self: ChatService) -> LearningResourcesService | None:
        """学习资料推荐模块子图服务（V2 Issue 13）；未装配时为 None。"""
        return self._learning_resources
    @property
    def commute_service(self: ChatService) -> CommuteService | None:
        """校园通勤模块子图服务（V2 Issue 12）；未装配时为 None。"""
        return self._commute
    def run_graph_turn(
        self: ChatService,
        run: GenerationRunRecord,
        *,
        on_event: Callable[[StreamEvent], None],
        stop_event: threading.Event | None = None,
    ) -> str | None:
        """驱动日常 LangGraph 父图执行本轮（事件实时回调，返回最后 kind）。

        执行器把 ``on_event`` 接到游标事件持久化上；停止信号与终态收敛
        语义见 :mod:`bridges.chat.graph`。
        """
        if run.graph_version in {
            STUDY_GRAPH_VERSION, "study-pages-v1", "study-scope-v2", "study-tutoring-v3",
            "study-tutoring-review-v4",
            "study-tutoring-review-v5",
        }:
            return StudyWorkflow(self).run(
                run, on_event=on_event, stop_event=stop_event
            )
        return run_daily_turn(
            self,
            run,
            on_event=on_event,
            stop_event=stop_event,
        )
    def _ensure_retrieval_decision(
        self: ChatService,
        *,
        account_id: str,
        conversation_id: str,
        assistant_message_id: str,
        user_message_id: str,
        query: str,
        mode: ChatMode,
        use_knowledge_base: bool,
        image_payload: dict[str, Any] | None,
        video_payload: dict[str, Any] | None,
        mcp_call_payload: dict[str, Any] | None,
    ) -> None:
        """在入队前保存决策，使首个响应即可恢复跳过/触发状态。"""
        if self._retrieval is None or mode == ChatMode.STUDY:
            return
        route = capability_route_for_request(
            mode=mode.value,
            has_humanizer=False,
            has_image=image_payload is not None,
            image_edit=(
                image_payload is not None
                and str(image_payload.get("kind")) == ImageTaskKind.EDIT.value
            ),
            has_video=video_payload is not None,
            has_mcp=mcp_call_payload is not None,
        )
        assistant = self._repo.get_message(account_id, assistant_message_id)
        actual_route = capability_route_from(assistant.route) if assistant is not None else None
        if actual_route is not None and not actual_route.knowledge_base_allowed:
            use_knowledge_base = False
        self._retrieval.ensure_decision(
            account_id,
            conversation_id,
            assistant_message_id,
            user_message_id,
            query,
            mode=mode.value,
            capability_route=route,
            use_knowledge_base=use_knowledge_base,
        )
