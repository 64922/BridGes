# mypy: disable-error-code="misc"

"""聊天服务的运行启动与预算方法。"""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from bridges.ai.model_quota import (
    RUN_MODEL_QUOTA_CONFIG_KEY,
    RunModelQuota,
    build_run_model_quota,
)
from bridges.ai.run_model_config import factory_run_model_config
from bridges.chat.attachments import (
    PHOTO_MEDIA_TYPES,
)
from bridges.chat.repository import (
    GenerationRunRecord,
)
from bridges.chat.run_budget_ledger import (
    RUN_BUDGET_INITIALS,
    RunBudgetLedgerRepository,
    anchor_run_budget_deadline,
    derive_run_budget_class,
    derive_run_budget_plan,
    derive_study_run_budget_plan,
    derive_tieba_run_budget_plan,
)
from bridges.chat.service_contract import ChatDomainError
from bridges.chat.service_helpers import (
    _TITLE_MAX,
    _attachment_message_title,
)
from bridges.contracts.chat import (
    ChatFirstTurnResponse,
    ChatMessageRole,
    ChatMode,
    ChatPluginSelectionItem,
    ChatStreamEventKind,
)
from bridges.routing import (
    CapabilityRoute,
    MainCapability,
    RouteStatus,
)

if TYPE_CHECKING:
    from bridges.chat.service import ChatService


class _ServiceRunBudgetMixin:
    def start_first_turn(
        self: ChatService,
        account_id: str,
        *,
        content: str,
        idempotency_key: str,
        conversation_id: str | None = None,
        mode: ChatMode = ChatMode.COMPANION,
        project_id: str | None = None,
        plugin_selection: list[ChatPluginSelectionItem] | None = None,
        attachment_ids: list[str] | None = None,
        skill_id: str | None = None,
        skill_input: dict[str, Any] | None = None,
        image: dict[str, Any] | None = None,
        video: dict[str, Any] | None = None,
        mcp_call: dict[str, Any] | None = None,
        use_knowledge_base: bool = True,
        use_profile: bool = True,
        module_id: str | None = None,
    ) -> ChatFirstTurnResponse:
        """原子创建新会话首轮（Issue 03）：会话、用户消息、助手占位与
        queued 运行在同一事务内落库，返回完整投影。

        首页发送第一条消息或调用任一功能时使用，成功响应后再导航——
        ``sessionStorage`` 不再承担业务真相。``idempotency_key`` 抵御
        双击与网络重放：同账户同键重放（含并发）只产生一份数据，返回
        既有会话且 ``idempotent_replay=True``（HTTP 200）。``conversation_id``
        可选指定已预建的空会话（附件上传路径先建会话再发送）；缺省在
        事务内新建。``mode``/``project_id``/``plugin_selection`` 随首轮
        写入会话。失败不留任何会话/消息（整事务回滚），不产生空草稿。
        """
        self._ensure_extension_payload_allowed(
            plugin_selection=plugin_selection,
            skill_id=skill_id,
            skill_input=skill_input,
            mcp_call=mcp_call,
        )
        if project_id is not None:
            raise ChatDomainError(
                "legacy_file_source_retired",
                "学习项目归属已退役，请使用全局知识库。",
                410,
            )
        project_id = None
        now = datetime.now(UTC)
        content = content.strip()
        attachment_ids = [oid for oid in (attachment_ids or []) if oid]
        if not content and not attachment_ids:
            raise ChatDomainError("empty_message", "消息内容不能为空。", 422)
        image_payload, video_payload, mcp_call_payload = self._validate_turn_payloads(
            image, video, mcp_call
        )
        # 指定会话（附件路径）必须存在且属于当前账户；缺省新建会话没有
        # 这个问题。「会话已有消息」的检查在事务内（幂等查找之后）执行：
        # 同键重放（含预建会话路径）必须 200 返回既有数据，不能被 409
        # 短路；并发不同键双发同一预建会话由事务内检查拦截。
        if conversation_id is not None:
            # 共享 SQLite 连接不能在其他线程提交首轮原子事务时并发读取。
            with self._repo.connection_lock():
                record = self._repo.get_conversation(account_id, conversation_id)
            if record is None:
                raise ChatDomainError(
                    "conversation_not_found", "对话不存在或没有访问权限。", 404
                )
            if record.mode != mode.value:
                raise ChatDomainError(
                    "conversation_mode_locked",
                    "该会话模式已锁定，请新建另一个会话以使用其他模式。",
                    409,
                )
        if mode == ChatMode.STUDY:
            self._validate_study_payload(module_id, image_payload, video_payload, mcp_call_payload)
        # V2 Issue 05：附件来自账户级草稿域，随首轮在同一事务内绑定新会话。
        # Issue 10：学习模式接受同一份附件合同（照片与文件）。
        if attachment_ids:
            self._validate_draft_attachments(account_id, attachment_ids)
        target_conversation_id = conversation_id or secrets.token_urlsafe(16)
        user_message_id = secrets.token_urlsafe(16)
        understanding = self._understand_turn(
            account_id=account_id,
            conversation_id=target_conversation_id,
            user_message_id=user_message_id,
            content=content,
            mode=mode,
            module_id=module_id,
        )
        capability_route = self._route_for_turn(
            content=content,
            requested_module_id=module_id,
            understanding=understanding,
            image_payload=image_payload,
            video_payload=video_payload,
            mcp_call_payload=mcp_call_payload,
        )
        # 纯附件消息没有正文：照片沿用占位标题，文件用首个附件文件名。
        if content:
            title = (
                content if len(content) <= _TITLE_MAX else content[:_TITLE_MAX] + "…"
            )
        else:
            title = _attachment_message_title(
                self._attachments, account_id, attachment_ids
            )
        (
            user_message,
            assistant_message,
            run_record,
            started_payload,
        ) = self._assemble_generation_records(
            account_id=account_id,
            conversation_id=target_conversation_id,
            content=content,
            mode=mode,
            image_payload=image_payload,
            video_payload=video_payload,
            mcp_call_payload=mcp_call_payload,
            route=capability_route,
            use_knowledge_base=use_knowledge_base,
            use_profile=use_profile,
            now=now,
            module_id=module_id,
            understanding=understanding,
            user_message_id=user_message_id,
        )
        created_id, created, conflict_not_empty = self._repo.insert_first_turn(
            account_id=account_id,
            conversation_id=target_conversation_id,
            idempotency_key=idempotency_key,
            title=title,
            mode=mode.value,
            created_at=now,
            project_id=project_id,
            plugin_selection=(
                [item.model_dump() for item in plugin_selection]
                if plugin_selection
                else None
            ),
            user_record=user_message,
            assistant_record=assistant_message,
            run_record=run_record,
            events=[(ChatStreamEventKind.STARTED.value, started_payload)],
            attachment_ids=attachment_ids or None,
        )
        if conflict_not_empty:
            with self._repo.connection_lock():
                record = self._repo.get_conversation(account_id, target_conversation_id)
            if record is not None and record.mode != mode.value:
                raise ChatDomainError(
                    "conversation_mode_locked",
                    "该会话模式已锁定，请新建另一个会话以使用其他模式。",
                    409,
                )
            raise ChatDomainError(
                "conversation_not_empty",
                "该对话已有消息，不能作为首轮发送目标。",
                409,
            )
        if not created:
            # 幂等命中：返回已存在数据，不执行任何首轮副作用（消息已
            # 落库、profile 事件已随原首轮持久化）。
            with self._repo.connection_lock():
                conversation = self.get_conversation(account_id, created_id)
            if conversation is None:
                raise ChatDomainError(
                    "conversation_not_found", "会话不存在或已删除。", 404
                )
            user_projection = next(
                (m for m in conversation.messages if m.role == ChatMessageRole.USER),
                None,
            )
            assistant_projection = next(
                (m for m in conversation.messages if m.role == ChatMessageRole.ASSISTANT),
                None,
            )
            if user_projection is None or assistant_projection is None:
                raise ChatDomainError(
                    "first_turn_replay_inconsistent",
                    "首轮数据不完整，请刷新后重试。",
                    409,
                )
            # 运行视图按消息查询：终态运行同样有记录（active_run 只在活跃
            # 时附加到投影），因此 run 完成/失败/停止后重放仍返回 200。
            run_view = self.run_view_of(account_id, assistant_projection.message_id)
            if run_view is None:
                raise ChatDomainError(
                    "first_turn_replay_inconsistent",
                    "首轮数据不完整，请刷新后重试。",
                    409,
                )
            return ChatFirstTurnResponse(
                conversation=conversation,
                run_id=run_view.run_id,
                cursor=run_view.cursor,
                user_message=user_projection,
                assistant_message=assistant_projection,
                idempotent_replay=True,
            )
        # 改进工单 09：首轮运行创建即冻结整次运行预算账本。
        self._freeze_run_budget(
            run_record,
            mode=mode,
            route=capability_route,
            image_payload=image_payload,
            video_payload=video_payload,
            now=now,
        )
        # 改进工单 12：关系落地在消息存在后进行（失败收敛为可重试错误）。
        self._apply_task_understanding(
            account_id=account_id,
            conversation_id=created_id,
            user_message_id=user_message.message_id,
            assistant_message_id=assistant_message.message_id,
            content=content,
            understanding=understanding,
        )
        self._process_profile_effects(
            account_id=account_id,
            conversation_id=created_id,
            user_message_id=user_message.message_id,
            content=content,
            mode=mode,
            run_id=run_record.run_id,
        )
        run_view = self._run_view(run_record, account_id)
        with self._repo.connection_lock():
            conversation = self.get_conversation(account_id, created_id)
        if conversation is None:
            raise ChatDomainError(
                "conversation_not_found", "会话不存在或已删除。", 404
            )
        return ChatFirstTurnResponse(
            conversation=conversation,
            run_id=run_record.run_id,
            cursor=run_view.cursor,
            user_message=self._project_message(user_message),
            assistant_message=self._project_message(assistant_message, run_view),
            idempotent_replay=False,
        )
    def _apply_run_model_lock(
        self: ChatService,
        run_config: dict[str, Any],
        *,
        previous_config: Mapping[str, Any] | None = None,
    ) -> None:
        """把本轮启动时的模型与**完整额度快照**写入运行配置（改进工单 03）。

        与 ``run_model_id`` 一起原子保存已验证上下文窗口、最大输入额度、配置
        revision、元数据合同版本与验证依据。旧运行排队后换配置时，续跑与重试
        仍读这份快照的真实额度，不再回退到 32,768 缺省窗口。

        重试（``previous_config`` 非空）**沿用原轮次启动时锁定的模型与额度**
        （排队、重试与跨进程恢复同一口径）；原轮次额度不可解析时保留原锁定
        字段并交给编译阶段明确闭锁，绝不用当前配置冒充旧运行额度。

        未装配提供者时同样保存出厂矩阵快照，避免恢复时重新读取变化后的额度。
        """
        if previous_config is not None and (
            previous_config.get("run_model_id") is not None
            or RUN_MODEL_QUOTA_CONFIG_KEY in previous_config
        ):
            # 不可读/未验证的快照也原样保留，重试不能借当前配置绕过闭锁。
            for key in ("run_model_id", RUN_MODEL_QUOTA_CONFIG_KEY):
                if key in previous_config:
                    run_config[key] = previous_config[key]
            return
        current_snapshot = (
            self._model_config_provider.snapshot()
            if self._model_config_provider is not None
            else factory_run_model_config()
        )
        run_config["run_model_id"] = current_snapshot.model_id
        run_config[RUN_MODEL_QUOTA_CONFIG_KEY] = build_run_model_quota(
            current_snapshot
        ).to_config()
    def _freeze_run_budget(
        self: ChatService,
        run_record: GenerationRunRecord,
        *,
        mode: ChatMode,
        route: CapabilityRoute | None,
        image_payload: dict[str, Any] | None,
        video_payload: dict[str, Any] | None,
        now: datetime,
    ) -> None:
        """运行创建时冻结整次运行预算账本（改进工单 09）。

        截止时间、调用/token 上限、核验/交付预留与外部并行上限在创建时
        按已确认初值方案冻结落库，此后只由执行内核经账本仓库修改——模型
        不能延时或扩容。明确深入（论文/学习书页/多模态）在创建时获更高
        预算；普通轻量交流沿用已验证前台硬上限（120 秒、无预留）；token
        上限从本轮运行额度快照派生（已验证输入上限 × 调用上限），额度未
        验证时只计量不强制。学习书页轮按本条消息绑定的照片数量扩展时间与
        调用上限，并受固定硬上限约束。
        """
        budget_class = derive_run_budget_class(
            mode=mode.value,
            route=route,
            has_image=image_payload is not None,
            has_video=video_payload is not None,
        )
        quota = RunModelQuota.from_config(
            (run_record.config or {}).get(RUN_MODEL_QUOTA_CONFIG_KEY)
        )
        if mode == ChatMode.STUDY:
            photo_count = 0
            if self._attachments is not None:
                photo_count = sum(
                    attachment.media_type in PHOTO_MEDIA_TYPES
                    for attachment in self._attachments.list_for_message(
                        run_record.account_id,
                        run_record.conversation_id,
                        run_record.user_message_id,
                    )
                )
            plan = derive_study_run_budget_plan(
                page_count=photo_count, created_at=run_record.created_at
            )
            if quota is not None and quota.is_verified and quota.max_input_tokens is not None:
                plan = replace(plan, token_budget=quota.max_input_tokens * plan.model_call_limit)
        else:
            token_budget = (
                quota.max_input_tokens * RUN_BUDGET_INITIALS[budget_class].model_call_limit
                if quota is not None and quota.is_verified and quota.max_input_tokens is not None
                else None
            )
            if (
                route is not None
                and route.status is RouteStatus.MATCHED
                and route.main_capability is MainCapability.TIEBA
            ):
                plan = derive_tieba_run_budget_plan(
                    created_at=run_record.created_at,
                    token_budget=token_budget,
                )
            else:
                plan = derive_run_budget_plan(
                    budget_class,
                    deadline_at=anchor_run_budget_deadline(run_record.created_at, budget_class),
                    token_budget=token_budget,
                )
        RunBudgetLedgerRepository(self._repo.database).freeze_for_run(
            account_id=run_record.account_id,
            run_id=run_record.run_id,
            conversation_id=run_record.conversation_id,
            plan=plan,
            now=now,
        )
