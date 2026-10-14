# mypy: disable-error-code="misc"

"""聊天服务的生成准入方法。"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from bridges.chat.repository import (
    ConversationModeLockConflict,
    GenerationRunRecord,
)
from bridges.chat.service_contract import ChatDomainError
from bridges.chat.service_helpers import (
    _TITLE_MAX,
    _attachment_message_title,
    _validate_image_payload,
    _validate_mcp_call_payload,
    _validate_video_payload,
)
from bridges.contracts.chat import (
    ChatMessageProjection,
    ChatMessageStatus,
    ChatMode,
    ChatStreamEventKind,
)
from bridges.state_copy import (
    WAIT_GENERATION_IN_PROGRESS_TEXT,
)
from bridges.storage.errors import StorageError

if TYPE_CHECKING:
    from bridges.chat.service import ChatService


class _ServiceGenerationAdmissionMixin:
    def start_generation(
        self: ChatService,
        account_id: str,
        conversation_id: str,
        content: str,
        attachment_ids: list[str] | None = None,
        skill_id: str | None = None,
        skill_input: dict[str, Any] | None = None,
        image: dict[str, Any] | None = None,
        video: dict[str, Any] | None = None,
        mcp_call: dict[str, Any] | None = None,
        use_knowledge_base: bool = True,
        use_profile: bool = True,
        module_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[ChatMessageProjection, ChatMessageProjection, bool]:
        """原子创建用户消息、助手占位与 queued 运行，返回两者投影。

        Issue 02：同一事务创建用户消息、streaming 助手占位与持久化生成
        运行（queued）并登记进统一领取队列——发送返回即完成创建，生成
        由后台执行器领取执行，页面/SSE 不再拥有运行生命周期。运行快照
        本轮发送参数（``use_knowledge_base``/``use_profile``），执行器
        与重试按同一份任务契约执行。

        V2 Issue 02：``module_id`` 是服务端校验的逐消息显式模块选择，随
        用户消息持久化（模型不得从正文改写）；``idempotency_key`` 使同
        一会话同键重试复用同一运行标识——命中时返回既有消息投影且不
        重复写任何数据（含运行仍在进行中的情形，绝不误报冲突）。返回
        第三个元素表示是否为幂等重放。

        ``skill_id``/``skill_input``（Issue 28）：携带时本轮走内置 SKILL
        编排（bridges-humanizer）；载荷随用户消息落库，重试沿用同一份
        任务契约。SKILL 载荷必须通过注册校验，未注册标识直接拒绝。
        ``image``（Issue 31）：图片生成/编辑请求载荷；与 SKILL 载荷
        互斥，携带时本轮创建图片异步任务而非普通回答。
        ``video``（Issue 32）：文生视频请求载荷；与 SKILL 载荷互斥，
        携带时本轮创建视频异步任务而非普通回答。
        ``mcp_call``（Issue 36）：对选中 MCP 插件的调用载荷；与 SKILL
        载荷互斥，携带时本轮执行真实 MCP 调用而非普通回答。目标 MCP
        必须被本对话选中（选择器持久化），否则流式阶段拒绝。
        """
        self._ensure_extension_payload_allowed(
            skill_id=skill_id,
            skill_input=skill_input,
            mcp_call=mcp_call,
        )
        now = datetime.now(UTC)
        content = content.strip()
        attachment_ids = [oid for oid in (attachment_ids or []) if oid]
        if not content and not attachment_ids:
            raise ChatDomainError("empty_message", "消息内容不能为空。", 422)
        image_payload, video_payload, mcp_call_payload = self._validate_turn_payloads(
            image, video, mcp_call
        )
        record = self._repo.get_conversation(account_id, conversation_id)
        if record is None:
            raise ChatDomainError(
                "conversation_not_found", "对话不存在或没有访问权限。", 404
            )
        # V2 Issue 02：同一会话同幂等键重试复用稳定运行标识——命中即返回
        # 既有投影，不重复写用户/助手消息（运行仍在进行中也按重放处理）。
        if idempotency_key:
            replay = self._repo.get_run_by_idempotency_key(
                account_id, conversation_id, idempotency_key
            )
            if replay is not None:
                return (*self._replay_generation_response(account_id, replay), True)
        existing = self._repo.list_messages(account_id, conversation_id)
        if any(message.status == ChatMessageStatus.STREAMING for message in existing):
            raise ChatDomainError(
                "generation_in_progress",
                WAIT_GENERATION_IN_PROGRESS_TEXT,
                409,
            )
        # V2 Issue 05：附件来自账户级草稿域，发送成功后随消息原子绑定；
        # 校验失败（数量/重复/跨账户）在此拒绝，草稿保留供用户调整重试。
        # Issue 10：学习模式接受同一份附件合同（照片与文件）。
        if attachment_ids:
            self._validate_draft_attachments(account_id, attachment_ids)
        mode = ChatMode(record.mode)
        if mode == ChatMode.STUDY:
            self._validate_study_payload(module_id, image_payload, video_payload, mcp_call_payload)
        user_message_id = secrets.token_urlsafe(16)
        understanding = self._understand_turn(
            account_id=account_id,
            conversation_id=conversation_id,
            user_message_id=user_message_id,
            content=content,
            mode=mode,
            module_id=module_id,
            messages=existing,
        )
        capability_route = self._route_for_turn(
            content=content,
            requested_module_id=module_id,
            understanding=understanding,
            image_payload=image_payload,
            video_payload=video_payload,
            mcp_call_payload=mcp_call_payload,
        )
        (
            user_message,
            assistant_message,
            run_record,
            started_payload,
        ) = self._assemble_generation_records(
            account_id=account_id,
            conversation_id=conversation_id,
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
            idempotency_key=idempotency_key,
            understanding=understanding,
            user_message_id=user_message_id,
        )
        try:
            self._repo.insert_generation_turn(
                user_message,
                assistant_message,
                run_record,
                [(ChatStreamEventKind.STARTED.value, started_payload)],
                attachment_ids or None,
                lock_mode=not record.mode_locked,
            )
        except ConversationModeLockConflict as exc:
            raise ChatDomainError(
                "conversation_mode_locked",
                "该会话模式已锁定，请继续使用当前模式或新建会话。",
                409,
            ) from exc
        except StorageError:
            # 幂等键唯一索引兜底并发：同键竞争时败方按重放返回
            if idempotency_key:
                replay = self._repo.get_run_by_idempotency_key(
                    account_id, conversation_id, idempotency_key
                )
                if replay is not None:
                    return (*self._replay_generation_response(account_id, replay), True)
            raise
        # 改进工单 09：运行创建即冻结整次运行预算账本（截止时间/调用与
        # token 上限/核验交付预留）；执行内核唯一修改，恢复不重置。
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
            conversation_id=conversation_id,
            user_message_id=user_message.message_id,
            assistant_message_id=assistant_message.message_id,
            content=content,
            understanding=understanding,
        )
        self._ensure_retrieval_decision(
            account_id=account_id,
            conversation_id=conversation_id,
            assistant_message_id=assistant_message.message_id,
            user_message_id=user_message.message_id,
            query=content,
            mode=mode,
            use_knowledge_base=use_knowledge_base,
            image_payload=image_payload,
            video_payload=video_payload,
            mcp_call_payload=mcp_call_payload,
        )
        self._repo.touch_conversation(account_id, conversation_id, now)

        if not record.title:
            # 纯附件消息没有正文：照片沿用占位标题，文件用首个附件文件名，
            # 保持会话列表可读且不误标消息类型。
            # （空正文必带附件——纯空消息已被 empty_message 拒绝。）
            if content:
                title = (
                    content if len(content) <= _TITLE_MAX else content[:_TITLE_MAX] + "…"
                )
            else:
                title = _attachment_message_title(
                    self._attachments, account_id, attachment_ids
                )
            self._repo.set_conversation_title(account_id, conversation_id, title, now)

        self._process_profile_effects(
            account_id=account_id,
            conversation_id=conversation_id,
            user_message_id=user_message.message_id,
            content=content,
            mode=mode,
            run_id=run_record.run_id,
        )

        run_view = self._run_view(run_record, account_id)
        return (
            self._project_message(user_message),
            self._project_message(assistant_message, run_view),
            False,
        )
    def _replay_generation_response(
        self: ChatService, account_id: str, run: GenerationRunRecord
    ) -> tuple[ChatMessageProjection, ChatMessageProjection]:
        """按既有运行组装幂等重放投影（不创建任何新数据）。"""
        user_message = self._repo.get_message(account_id, run.user_message_id)
        assistant_message = self._repo.get_message(account_id, run.assistant_message_id)
        if user_message is None or assistant_message is None:
            raise ChatDomainError(
                "generation_replay_unavailable",
                "原请求的数据已不可用，请重新发送消息。",
                409,
            )
        return (
            self._project_message(user_message),
            self._project_message(assistant_message, self._run_view(run, account_id)),
        )
    def _validate_turn_payloads(
        self: ChatService,
        image: dict[str, Any] | None,
        video: dict[str, Any] | None,
        mcp_call: dict[str, Any] | None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
        """校验一轮载荷：图片/视频/MCP 结构与互斥。

        供续轮（``start_generation``）与原子首轮（``start_first_turn``）
        共用，失败按 422 拒绝而不是静默丢弃（失败不伪装成功）。
        SKILL 载荷在 ``_ensure_extension_payload_allowed`` 一律拒绝
        （V2 issue 04：文章人味化入口退役），不再进入本校验。
        """
        image_payload = _validate_image_payload(image, False)
        video_payload = _validate_video_payload(
            video, image_payload is not None
        )
        mcp_call_payload = _validate_mcp_call_payload(
            mcp_call,
            image_payload is not None or video_payload is not None,
        )
        return image_payload, video_payload, mcp_call_payload
