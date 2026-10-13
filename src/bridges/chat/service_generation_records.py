# mypy: disable-error-code="misc"

"""聊天服务的生成记录组装方法。"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import TYPE_CHECKING, Any

from bridges.chat.graph import DAILY_GRAPH_VERSION
from bridges.chat.repository import (
    GenerationRunRecord,
    MessageRecord,
)
from bridges.chat.service_contract import ChatDomainError
from bridges.chat.service_helpers import (
    _started_event_payload,
    logger,
)
from bridges.chat.terminal import internal_error_outcome
from bridges.chat.turn import (
    STREAM_INTERRUPTED_MESSAGE,  # noqa: F401 - re-export
    error_is_retryable,  # noqa: F401 - re-export
    initial_thinking,
    user_facing_error,  # noqa: F401 - re-export
)
from bridges.chat.understanding import task_turn_request
from bridges.contracts.chat import (
    ChatMessageRole,
    ChatMessageStatus,
    ChatMode,
    ChatRunStatus,
)
from bridges.contracts.tasks import TaskRelation, TaskTurnRequest
from bridges.contracts.understanding import (
    UNDERSTANDING_CONTRACT_VERSION,
    MainUnderstanding,
    RouteSource,
)
from bridges.routing import (
    MODULE_CAPABILITIES,
    CapabilityRoute,
    MainCapability,
    RouteStatus,
)
from bridges.study.service import STUDY_GRAPH_VERSION

if TYPE_CHECKING:
    from bridges.chat.service import ChatService


class _ServiceGenerationRecordsMixin:
    def _assemble_generation_records(
        self: ChatService,
        *,
        account_id: str,
        conversation_id: str,
        content: str,
        mode: ChatMode,
        image_payload: dict[str, Any] | None,
        video_payload: dict[str, Any] | None,
        mcp_call_payload: dict[str, Any] | None,
        route: CapabilityRoute,
        use_knowledge_base: bool,
        use_profile: bool,
        now: datetime,
        module_id: str | None = None,
        idempotency_key: str | None = None,
        understanding: MainUnderstanding | None = None,
        user_message_id: str | None = None,
    ) -> tuple[MessageRecord, MessageRecord, GenerationRunRecord, dict[str, Any]]:
        """组装一轮的用户消息、助手占位、queued 运行与 started 载荷。

        供续轮（``start_generation``）与原子首轮（``start_first_turn``）
        共用；调用方负责事务写入与幂等语义。运行创建即视为"活跃"，
        读取收敛不会误伤（判定源为运行表）。V2 Issue 02：``module_id``
        随用户消息持久化；运行记录写入图版本与幂等键。改进工单 12：
        ``understanding`` 随运行配置持久化（审计/恢复读取同一份理解），
        ``user_message_id`` 允许调用方预生成以便理解产出引用同一消息 ID。
        """
        thinking = initial_thinking(mode).model_dump(mode="json")
        web_search = (
            self._web_search.initial_projection(self._web_search.plan(content, mode))
            if self._web_search is not None
            and route.web_search_allowed
            and not route.is_paper_search
            and route.status not in {RouteStatus.CLARIFY, RouteStatus.REJECTED}
            else None
        )
        arxiv_search = (
            self._arxiv_search.initial_projection(
                self._arxiv_search.plan_from_route(route)
            )
            if self._arxiv_search is not None
            and route.is_paper_search
            else None
        )
        teaching = None
        user_message = MessageRecord(
            message_id=(
                user_message_id
                if user_message_id is not None
                else secrets.token_urlsafe(16)
            ),
            conversation_id=conversation_id,
            account_id=account_id,
            role=ChatMessageRole.USER,
            attempt_number=1,
            status=ChatMessageStatus.DONE,
            content=content,
            thinking=None,
            error_code=None,
            error_message=None,
            duration_ms=None,
            model_id=None,
            run_lock_id=None,
            created_at=now,
            updated_at=now,
            image=image_payload,
            video=video_payload,
            mcp_call=mcp_call_payload,
            route=route.model_dump(mode="json"),
            module_id=module_id,
        )
        assistant_message = MessageRecord(
            message_id=secrets.token_urlsafe(16),
            conversation_id=conversation_id,
            account_id=account_id,
            role=ChatMessageRole.ASSISTANT,
            attempt_number=1,
            status=ChatMessageStatus.STREAMING,
            content="",
            thinking=thinking,
            error_code=None,
            error_message=None,
            duration_ms=None,
            model_id=None,
            run_lock_id=None,
            created_at=now,
            updated_at=now,
            web_search=(web_search.model_dump(mode="json") if web_search else None),
            arxiv_search=(arxiv_search.model_dump(mode="json") if arxiv_search else None),
            teaching=(teaching.model_dump(mode="json") if teaching else None),
            route=route.model_dump(mode="json"),
        )
        # Issue 02：同一事务创建消息、queued 运行、started 事件与队列行。
        run_id = secrets.token_urlsafe(16)
        run_config: dict[str, Any] = {
            "use_knowledge_base": use_knowledge_base,
            "use_profile": use_profile,
        }
        if understanding is not None:
            run_config["understanding"] = understanding.model_dump(mode="json")
        # V2 Issue 09 + 改进工单 03：本轮启动时锁定主模型 ID 与完整额度快照。
        self._apply_run_model_lock(run_config)
        if (
            image_payload is None
            and video_payload is None
            and mcp_call_payload is None
        ):
            run_config["global_writing_policy"] = self._writing_policy.seed(
                mode
            ).model_dump(mode="json")
        run_record = GenerationRunRecord(
            run_id=run_id,
            account_id=account_id,
            conversation_id=conversation_id,
            user_message_id=user_message.message_id,
            assistant_message_id=assistant_message.message_id,
            attempt_number=assistant_message.attempt_number,
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
                STUDY_GRAPH_VERSION if mode == ChatMode.STUDY else DAILY_GRAPH_VERSION
            ),
            idempotency_key=idempotency_key,
        )
        started_payload = _started_event_payload(
            conversation_id=conversation_id,
            user_message_id=user_message.message_id,
            message_id=assistant_message.message_id,
            attempt_number=assistant_message.attempt_number,
            message=assistant_message,
        )
        return user_message, assistant_message, run_record, started_payload
    def _apply_task_understanding(
        self: ChatService,
        *,
        account_id: str,
        conversation_id: str,
        user_message_id: str,
        assistant_message_id: str,
        content: str,
        understanding: MainUnderstanding | None,
    ) -> None:
        """把一次理解的关系落地为任务状态（普通聊天不触碰任务）。

        关系落地失败（并发版本冲突/终态冲突）时把本轮消息如实收敛为可
        重试错误，绝不静默吞掉用户的修改或伪造成功。澄清问题在任务上
        登记等待（绑定版本），答复由下一次理解补齐字段并解决等待。
        """

        if self._tasks is None or understanding is None:
            return
        if understanding.task_relation is None:
            return
        from bridges.tasks.repository import TaskError  # 运行时任务领域依赖

        request = task_turn_request(
            understanding,
            conversation_id=conversation_id,
            answer_text=content if understanding.answer_fields else None,
        )
        if request is None:
            return
        try:
            result = self._tasks.apply_turn(account_id, request)
        except TaskError as exc:
            run = self._repo.get_run_by_message(account_id, assistant_message_id)
            self.terminal.converge(
                account_id,
                run.run_id if run is not None else None,
                assistant_message_id,
                fallback=internal_error_outcome(),
            )
            raise ChatDomainError(
                "task_state_conflict",
                "任务状态已变化，请刷新后基于最新条件重试。",
                409,
            ) from exc
        run = self._repo.get_run_by_message(account_id, assistant_message_id)
        if run is not None and result.task is not None:
            self._repo.update_generation_config(account_id, run.run_id, {
                **(run.config or {}),
                "task_binding": {
                    "task_id": result.task.task.task_id,
                    "version": result.task.task.current_version,
                },
            })
        if not understanding.clarification_question or not understanding.missing_fields:
            return
        task_id = (
            result.task.task.task_id
            if result.task is not None
            else understanding.target_task_id
        )
        if task_id is None:
            return
        try:
            self._tasks.open_wait(
                account_id,
                conversation_id=conversation_id,
                question=understanding.clarification_question,
                missing_fields=list(understanding.missing_fields),
                origin_message_id=assistant_message_id,
                source_message_id=user_message_id,
                task_id=task_id,
            )
        except TaskError as exc:
            self.terminal.converge(
                account_id, run.run_id if run is not None else None,
                assistant_message_id, fallback=internal_error_outcome(),
            )
            raise ChatDomainError(
                "task_state_conflict", "澄清等待未能保存，请刷新后重试。", 409,
            ) from exc
    def _override_route_for_module(
        self: ChatService,
        route: CapabilityRoute | None,
        content: str,
        module_id: str,
        understanding: MainUnderstanding | None = None,
    ) -> CapabilityRoute:
        """点击建议启动模块：另存实际路由来源，不改写原消息标识。

        原消息正文的硬条件（不要联网/只查论文等）仍然生效，不得因点击
        建议而放宽。
        """

        metadata: dict[str, Any] = {
            "requested_module_id": module_id,
            "route_source": RouteSource.SUGGESTION_CLICK.value,
            "capability_list": [module_id],
            "understanding_version": UNDERSTANDING_CONTRACT_VERSION,
        }
        web_allowed, kb_allowed = self._route_flags(understanding)
        flags: dict[str, Any] = {
            "web_search_allowed": web_allowed,
            "knowledge_base_allowed": kb_allowed,
        }
        if module_id == "paper":
            classified = self._router.classify(content)
            if classified.is_paper_search or classified.status in {
                RouteStatus.CLARIFY,
                RouteStatus.REJECTED,
            }:
                return classified.model_copy(update={
                    **metadata, **flags,
                    "module_id": "paper" if classified.is_paper_search else None,
                    "web_search_allowed": classified.web_search_allowed and web_allowed,
                    "knowledge_base_allowed": classified.knowledge_base_allowed and kb_allowed,
                })
        if module_id != "paper" and module_id in MODULE_CAPABILITIES:
            return CapabilityRoute(
                status=RouteStatus.MATCHED,
                main_capability=MODULE_CAPABILITIES[module_id],
                confidence=1.0,
                reason="用户点击建议启动模块（原消息标识不改写）。",
                module_id=module_id,
                knowledge_base_allowed=kb_allowed,
                web_search_allowed=web_allowed,
                **metadata,
            )
        if route is None:
            return CapabilityRoute(
                status=RouteStatus.ORDINARY,
                main_capability=MainCapability.ORDINARY_CHAT,
                confidence=1.0,
                reason="用户点击建议启动模块（原消息标识不改写）。",
                knowledge_base_allowed=kb_allowed,
                web_search_allowed=web_allowed,
                **metadata,
            )
        return route.model_copy(update={**metadata, **flags})
    def _bind_suggestion_to_task(
        self: ChatService,
        *,
        account_id: str,
        conversation_id: str,
        user_message_id: str,
        content: str,
        binding: dict[str, Any] | None,
        understanding: MainUnderstanding | None,
    ) -> dict[str, Any] | None:
        """建议沿用原轮任务；普通消息的首次点击建立独立任务版本。"""

        if self._tasks is None:
            return None
        from bridges.tasks.repository import TaskError

        if binding is None:
            request = task_turn_request(understanding.model_copy(update={
                "user_message_id": user_message_id, "task_relation": TaskRelation.NEW,
                "goal": content, "target_task_id": None, "expected_task_version": None,
                "answer_fields": [],
            }), conversation_id=conversation_id) if understanding is not None else None
            if request is not None and understanding is not None:
                for condition in request.conditions:
                    condition.source_message_id = understanding.user_message_id
            result = self._tasks.apply_turn(account_id, request or TaskTurnRequest(
                conversation_id=conversation_id, user_message_id=user_message_id,
                relation=TaskRelation.NEW, goal=content,
            ))
            if result.task is None:
                return None
            return {"task_id": result.task.task.task_id,
                    "version": result.task.task.current_version}

        projection = self._tasks.projection(account_id, binding["task_id"])
        if (projection is None or projection.task.conversation_id != conversation_id
                or projection.task.current_version != binding["version"]
                or projection.task.status.value in {"cancelled", "completed", "paused"}):
            raise ChatDomainError(
                "task_state_conflict", "该建议对应的任务已变化，请基于当前任务重新发起。", 409,
            )
        open_capability = any(
            "capability" in wait.missing_fields
            for wait in projection.open_waits
        )
        request = TaskTurnRequest(
            conversation_id=conversation_id,
            user_message_id=user_message_id,
            relation=TaskRelation.CONTINUE,
            explicit_task_id=projection.task.task_id,
            expected_version=projection.task.current_version,
            answer_fields=["capability"] if open_capability else [],
            answer_text=content if open_capability else None,
        )
        try:
            self._tasks.apply_turn(account_id, request)
        except TaskError as exc:
            raise ChatDomainError(
                "task_state_conflict",
                "任务状态已变化，请刷新后基于最新条件重试。",
                409,
            ) from exc
        return binding
    def _pause_task_for_stop(
        self: ChatService,
        *,
        account_id: str,
        conversation_id: str,
        source_message_id: str,
    ) -> None:
        """停止当前运行即暂停任务；任务领域未装配或无可暂停任务时静默跳过。"""

        if self._tasks is None:
            return
        context = self._tasks.current_reference_context(account_id, conversation_id)
        if context is None or context.status not in {"active", "waiting"}:
            return
        from bridges.tasks.repository import TaskError

        request = TaskTurnRequest(
            conversation_id=conversation_id,
            user_message_id=source_message_id,
            relation=TaskRelation.PAUSE,
            explicit_task_id=context.task_id,
            expected_version=context.version,
        )
        try:
            self._tasks.apply_turn(account_id, request)
        except TaskError:
            logger.warning(
                "停止生成时暂停任务失败：task_id=%s", context.task_id, exc_info=True
            )
