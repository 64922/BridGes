# mypy: disable-error-code="misc"

"""聊天服务的重试方法。"""

from __future__ import annotations

import secrets
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from bridges.arxiv_mcp.contracts import ArxivSearchProjection, ArxivSearchStatus
from bridges.chat.graph import DAILY_GRAPH_VERSION
from bridges.chat.graph_contracts import STUDY_LEGACY_RETIRED
from bridges.chat.repository import (
    GenerationRunRecord,
    MessageRecord,
)
from bridges.chat.service_contract import ChatDomainError
from bridges.chat.service_helpers import (
    _started_event_payload,
)
from bridges.chat.turn import (
    STREAM_INTERRUPTED_MESSAGE,  # noqa: F401 - re-export
    attempt_group,
    capability_route_from,
    # error_is_retryable / user_facing_error 在此 re-export，保持 api/chat.py
    # 的既有导入路径不变（定义在 chat/turn.py）。
    error_is_retryable,  # noqa: F401 - re-export
    initial_thinking,
    owner_user_message,
    user_facing_error,  # noqa: F401 - re-export
)
from bridges.contracts.chat import (
    CHAT_MODULE_VALUES,
    ChatMessageProjection,
    ChatMessageRole,
    ChatMessageStatus,
    ChatMode,
    ChatRunStatus,
    ChatStreamEventKind,
)
from bridges.contracts.teaching import TeachingTurnProjection
from bridges.contracts.understanding import (
    understanding_from_snapshot,
)
from bridges.routing import (
    RouteStatus,
)
from bridges.state_copy import (
    WAIT_GENERATION_IN_PROGRESS_TEXT,
    render_state_copy,
)
from bridges.study.history import legacy_study_conversation
from bridges.study.versions import STUDY_TUTOR_GRAPH_VERSION
from bridges.web_search.contracts import WebSearchProjection, WebSearchStatus

if TYPE_CHECKING:
    from bridges.chat.service import ChatService


class _ServiceRetryMixin:
    def retry_generation(
        self: ChatService,
        account_id: str,
        conversation_id: str,
        message_id: str,
        use_knowledge_base: bool = True,
        use_profile: bool = True,
        idempotency_key: str | None = None,
        module_id: str | None = None,
    ) -> tuple[ChatMessageProjection, ChatMessageProjection, bool]:
        """为已终态（失败/停止/完成）的助手消息创建新的助手尝试。

        尝试号递增，历史尝试原样保留（含错误状态），新尝试成为该轮的
        最新回答；绝不静默改写历史，也不把失败注册为成功。Issue 02：
        新尝试在同一事务创建 queued 运行并持久化 started 事件，由后台
        执行器领取执行（与发送同一外壳）。``use_knowledge_base``/
        ``use_profile`` 沿用旧尝试轮次的开关，随运行快照落库。

        V2 Issue 02：``idempotency_key`` 使同会话同键重试复用同一运行
        （返回既有投影，不创建新尝试）；返回第三个元素表示幂等重放。

        V2 Issue 11：``module_id`` 是「点击建议一键启动模块」的显式覆盖——
        仅当该轮用户消息本身没有模块时生效（普通聊天里的论文请求以原文
        重新派发到论文模块），绝不改写历史消息的模块标识。
        """
        message = self._repo.get_message(account_id, message_id)
        if message is None or message.conversation_id != conversation_id:
            raise ChatDomainError(
                "message_not_found", "消息不存在或没有访问权限。", 404
            )
        if message.role != ChatMessageRole.ASSISTANT:
            raise ChatDomainError(
                "not_retryable_message", "只有助手消息可以重试。", 400
            )
        record = self._repo.get_conversation(account_id, conversation_id)
        if record is None:
            raise ChatDomainError(
                "conversation_not_found", "对话不存在或没有访问权限。", 404
            )
        # 改进工单 12：旧五阶段学习会话（含旧版本运行）只读退出；旧重试
        # 入口不能重新执行旧流程，恢复方式是用户主动续学。
        if record.mode == ChatMode.STUDY.value and legacy_study_conversation(
            self._repo.database, account_id, conversation_id
        ):
            raise ChatDomainError(
                STUDY_LEGACY_RETIRED,
                render_state_copy(f"error.{STUDY_LEGACY_RETIRED}"),
                410,
            )
        # V2 Issue 02：同幂等键重试复用稳定运行标识（含仍在进行中的运行）。
        if idempotency_key:
            replay = self._repo.get_run_by_idempotency_key(
                account_id, conversation_id, idempotency_key
            )
            if replay is not None:
                return (*self._replay_generation_response(account_id, replay), True)
        existing = self._repo.list_messages(account_id, conversation_id)
        if any(m.status == ChatMessageStatus.STREAMING for m in existing):
            raise ChatDomainError(
                "generation_in_progress",
                WAIT_GENERATION_IN_PROGRESS_TEXT,
                409,
            )
        owner = owner_user_message(existing, message_id)
        if owner is None:
            raise ChatDomainError(
                "not_retryable_message", "找不到该助手消息对应的用户消息。", 400
            )
        previous_run = self._repo.get_run_by_message(account_id, message_id)
        previous_config = (previous_run.config or {}) if previous_run is not None else {}
        previous_understanding = understanding_from_snapshot(
            previous_config.get("understanding")
        )
        previous_modules = {
            owner.module_id,
            (message.route or {}).get("module_id"),
            previous_config.get("module_id"),
            *(previous_understanding.capability_list if previous_understanding else []),
        }
        legacy_capability = (message.route or {}).get("main_capability")
        if legacy_capability in {"career", "resources", "github"}:
            previous_modules.add(legacy_capability)
        previous_modules.update(module for module, has_result in {
            "career": message.career_plan is not None or message.career_planning is not None,
            "resources": message.learning_resources is not None,
            "github": message.github_projects is not None,
        }.items() if has_result)
        if any(value is not None and value not in CHAT_MODULE_VALUES
               for value in previous_modules):
            raise ChatDomainError(
                "module_not_allowed", "该模块已取消，历史结果可查看，请发送新的消息。", 400
            )
        if record.mode == ChatMode.STUDY.value:
            for previous_attempt in attempt_group(existing, owner.message_id):
                if previous_attempt.status != ChatMessageStatus.DONE:
                    continue
                if previous_attempt.teaching is None:
                    continue
                previous_teaching = TeachingTurnProjection.model_validate(
                    previous_attempt.teaching
                )
                if (
                    previous_teaching.plan is not None
                    and previous_teaching.lesson is not None
                ):
                    raise ChatDomainError(
                        "teaching_already_published",
                        "本轮计划和第一课已经发布，无需重复生成。",
                        409,
                    )
        if owner.skill:
            # V2 issue 04：人味化任务的重试写路径退出——新尝试不再沿用旧
            # 任务契约做二次全文改写；历史输入与结果保持只读。
            raise ChatDomainError(
                "humanizer_retry_retired",
                "文章人味化任务已退役，历史结果仍可查看与导出，不能重试。",
                410,
            )
        max_attempt = max(
            (m.attempt_number for m in attempt_group(existing, owner.message_id)),
            default=0,
        )
        now = datetime.now(UTC)
        mode = ChatMode(record.mode)
        # 后续重试沿用该尝试的实际路由，不能退回首次消息的建议/澄清。
        route = capability_route_from(message.route or owner.route)
        reusable_arxiv_search: ArxivSearchProjection | None = None
        reusable_web_search: WebSearchProjection | None = None
        for previous_attempt in reversed(attempt_group(existing, owner.message_id)):
            if previous_attempt.arxiv_search is None:
                pass
            else:
                previous_projection = ArxivSearchProjection(**previous_attempt.arxiv_search)
                if previous_projection.status == ArxivSearchStatus.SUCCESS:
                    reusable_arxiv_search = previous_projection
            if previous_attempt.web_search is not None:
                previous_web_projection = WebSearchProjection(
                    **previous_attempt.web_search
                )
                if previous_web_projection.status in {
                    WebSearchStatus.SUCCESS,
                    WebSearchStatus.PARTIAL,
                }:
                    reusable_web_search = previous_web_projection
            if reusable_arxiv_search is not None and reusable_web_search is not None:
                break
        web_search = (
            reusable_web_search
            or self._web_search.initial_projection(
                self._web_search.plan(owner.content, mode), recovery=True
            )
            if self._web_search is not None
            and route is not None
            and route.web_search_allowed
            and not route.is_paper_search
            and route.status not in {RouteStatus.CLARIFY, RouteStatus.REJECTED}
            else None
        )
        arxiv_search = (
            reusable_arxiv_search
            or self._arxiv_search.initial_projection(
                self._arxiv_search.plan_from_route(route), recovery=True
            )
            if self._arxiv_search is not None
            and route is not None
            and route.is_paper_search
            else None
        )
        new_attempt = MessageRecord(
            message_id=secrets.token_urlsafe(16),
            conversation_id=conversation_id,
            account_id=account_id,
            role=ChatMessageRole.ASSISTANT,
            attempt_number=max_attempt + 1,
            status=ChatMessageStatus.STREAMING,
            content="",
            thinking=initial_thinking(mode).model_dump(mode="json"),
            error_code=None,
            error_message=None,
            duration_ms=None,
            model_id=None,
            run_lock_id=None,
            # 重试尝试保持所属轮次的时间戳：排序时按 attempt_number 归入
            # 该轮（避免 created_at=now 把新尝试排到对话末尾，导致渲染分组
            # 与模型上下文错配到后续轮次）。
            created_at=owner.created_at,
            updated_at=now,
            web_search=(web_search.model_dump(mode="json") if web_search else None),
            arxiv_search=(arxiv_search.model_dump(mode="json") if arxiv_search else None),
            route=(route.model_dump(mode="json") if route is not None else owner.route),
        )
        # Issue 02：新尝试同一事务创建 queued 运行与 started 事件并入队，
        # 由后台执行器领取执行（重试沿用旧轮次的知识库/画像开关）。
        run_id = secrets.token_urlsafe(16)
        # 改进工单 12：理解快照随重试沿用——原消息的硬条件（不要联网等）
        # 不因重试或点击建议而放宽。
        understanding_snapshot = previous_understanding
        module_override: str | None = None
        if module_id is not None:
            # V2 Issue 11：点击建议一键启动论文模块——只在逐消息无模块时
            # 生效，且不与历史模块标识冲突（历史不被改写）。
            if module_id not in CHAT_MODULE_VALUES:
                raise ChatDomainError(
                    "module_not_allowed", "模块选择不合法。", 400
                )
            if owner.module_id is not None and owner.module_id != module_id:
                raise ChatDomainError(
                    "module_already_selected",
                    "该轮已经选择了模块，不能叠加另一个模块。",
                    409,
                )
            module_override = module_id
        if module_override is not None:
            # 改进工单 12：点击建议另存实际路由来源，并绑定当前任务版本；
            # 原用户消息的 module_id 不改写，历史不被冒充为首次选择。
            route = self._override_route_for_module(
                route, owner.content, module_override, understanding_snapshot
            )
            if route is not None:
                new_attempt = replace(
                    new_attempt, route=route.model_dump(mode="json")
                )
            previous_config["task_binding"] = self._bind_suggestion_to_task(
                account_id=account_id,
                conversation_id=conversation_id,
                user_message_id=new_attempt.message_id,
                content=owner.content,
                binding=(previous_config or {}).get("task_binding"),
                understanding=understanding_snapshot,
            )
        policy_snapshot = (previous_config or {}).get("global_writing_policy")
        if (
            policy_snapshot is None
            and owner.skill is None
            and owner.image is None
            and owner.video is None
            and owner.mcp_call is None
        ):
            policy_snapshot = self._writing_policy.seed(mode).model_dump(mode="json")
        run_config: dict[str, Any] = {
            "use_knowledge_base": use_knowledge_base,
            "use_profile": use_profile,
        }
        if module_override is not None:
            run_config["module_id"] = module_override
        elif (previous_config or {}).get("module_id") is not None:
            run_config["module_id"] = previous_config["module_id"]
        if understanding_snapshot is not None:
            run_config["understanding"] = understanding_snapshot.model_dump(mode="json")
        if (previous_config or {}).get("task_binding") is not None:
            run_config["task_binding"] = previous_config["task_binding"]
        # V2 Issue 09 + 改进工单 03：重试尝试沿用本轮启动时的模型与额度快照。
        self._apply_run_model_lock(run_config, previous_config=previous_config)
        if policy_snapshot is not None:
            run_config["global_writing_policy"] = policy_snapshot
        # 画像工单 11：重试沿用冻结的完整编译快照（重试预算重新裁剪发送子集）
        # 与实际发送记录两种键，与首轮 `_compile_profile_slice` 的语义一致。
        adopted_compiled = (previous_config or {}).get("adopted_profile_slice_compiled")
        if isinstance(adopted_compiled, dict):
            run_config["adopted_profile_slice_compiled"] = dict(adopted_compiled)
        adopted_profile_slice = (previous_config or {}).get("adopted_profile_slice")
        if isinstance(adopted_profile_slice, dict):
            run_config["adopted_profile_slice"] = dict(adopted_profile_slice)
        profile_correction = (previous_config or {}).get("profile_correction")
        if isinstance(profile_correction, dict):
            run_config["profile_correction"] = dict(profile_correction)
        # Issue 03：重试沿用本轮评价快照——旧作答重试仍绑定当时的待答
        # 问题与版本，不会被判到之后的新问题上，也不重复记账。
        verification_snapshot = (previous_config or {}).get("study_verification")
        if isinstance(verification_snapshot, dict):
            run_config["study_verification"] = verification_snapshot
        # Issue 09：重试沿用本轮采用快照——重试期间发生的纠正/删除由
        # 调用前复验撤回，不静默换成另一份学情。
        adaptation_snapshot = (previous_config or {}).get("study_adaptation")
        if isinstance(adaptation_snapshot, dict):
            run_config["study_adaptation"] = adaptation_snapshot
        run_record = GenerationRunRecord(
            run_id=run_id,
            account_id=account_id,
            conversation_id=conversation_id,
            user_message_id=owner.message_id,
            assistant_message_id=new_attempt.message_id,
            attempt_number=new_attempt.attempt_number,
            status=ChatRunStatus.QUEUED.value,
            config=run_config,
            stage=None,
            lease_owner=None,
            lease_expires_at=None,
            attempt_count=0,
            stop_requested=False,
            error_code=None,
            error_message=None,
            duration_ms=None,
            created_at=now,
            updated_at=now,
            graph_version=(
                STUDY_TUTOR_GRAPH_VERSION
                if mode == ChatMode.STUDY
                else DAILY_GRAPH_VERSION
            ),
            idempotency_key=idempotency_key,
        )
        started_payload = _started_event_payload(
            conversation_id=conversation_id,
            user_message_id=owner.message_id,
            message_id=new_attempt.message_id,
            attempt_number=new_attempt.attempt_number,
            message=new_attempt,
        )
        self._repo.insert_generation_attempt(
            new_attempt,
            run_record,
            [(ChatStreamEventKind.STARTED.value, started_payload)],
        )
        # 改进工单 09：重试创建的是新运行——建立新预算运行（初值重分配），
        # 有效产物复用语义由任务/产物票负责；本运行的账本从创建时冻结。
        self._freeze_run_budget(
            run_record,
            mode=mode,
            route=route,
            image_payload=owner.image,
            video_payload=owner.video,
            now=now,
        )
        self._ensure_retrieval_decision(
            account_id=account_id,
            conversation_id=conversation_id,
            assistant_message_id=new_attempt.message_id,
            user_message_id=owner.message_id,
            query=owner.content,
            mode=mode,
            use_knowledge_base=use_knowledge_base,
            image_payload=owner.image,
            video_payload=owner.video,
            mcp_call_payload=owner.mcp_call,
        )
        self._repo.touch_conversation(account_id, conversation_id, now)
        return (
            self._project_message(owner),
            self._project_message(new_attempt, self._run_view(run_record, account_id)),
            False,
        )
