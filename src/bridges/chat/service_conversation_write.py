# mypy: disable-error-code="misc"

"""聊天服务的会话写入方法。"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from bridges import __version__
from bridges.chat.attachments import (
    PHOTO_MEDIA_TYPES,
    ChatAttachmentError,
    ChatAttachmentService,
)
from bridges.chat.service_contract import ChatDomainError
from bridges.contracts.chat import (
    ChatConversationProjection,
    ChatMode,
    ChatPluginSelectionItem,
)

if TYPE_CHECKING:
    from bridges.chat.service import ChatService


class _ServiceConversationWriteMixin:
    def _ensure_extension_payload_allowed(
        self: ChatService,
        *,
        plugin_selection: list[ChatPluginSelectionItem] | None = None,
        skill_id: str | None = None,
        skill_input: dict[str, Any] | None = None,
        mcp_call: dict[str, Any] | None = None,
    ) -> None:
        if skill_id == "bridges-humanizer":
            # V2 issue 04：文章人味化专用入口退出；历史结果仍可查看与导出。
            raise ChatDomainError(
                "humanizer_capability_retired",
                "文章人味化能力已退役，历史结果仍可查看与导出；正文表达已并入自然对话。",
                410,
            )
        if (
            plugin_selection
            or mcp_call is not None
            or skill_id is not None
            or skill_input is not None
        ):
            raise ChatDomainError(
                "user_extensions_retired",
                "用户 SKILL、插件与通用 MCP 已退役，请返回聊天或知识库。",
                410,
            )
    def _require_attachment_service(self: ChatService) -> ChatAttachmentService:
        """返回附件服务；未挂载时拒绝附件请求（装配缺失按配置错误处理）。"""
        if self._attachments is None:
            raise ChatDomainError(
                "attachment_unavailable", "附件功能暂不可用，请稍后重试。", 503
            )
        return self._attachments
    def _validate_draft_attachments(
        self: ChatService, account_id: str, attachment_ids: list[str], *, photos_only: bool = False,
    ) -> None:
        """V2 Issue 05：校验账户级照片草稿并统一转成会话域错误。"""
        try:
            self._require_attachment_service().validate_draft_ids(
                account_id, attachment_ids
            )
        except ChatAttachmentError as exc:
            raise ChatDomainError(exc.code, exc.message, exc.status_code) from exc
        if photos_only:
            attachments = self._require_attachment_service()
            for object_id in attachment_ids:
                draft = attachments.get_draft(account_id, object_id)
                if draft is None or draft.media_type not in PHOTO_MEDIA_TYPES:
                    raise ChatDomainError(
                        "study_pages_required", "学习模式只接受本节书页照片，请移除文件附件。", 422
                    )
    def create_conversation(
        self: ChatService,
        account_id: str,
        title: str | None = None,
        mode: ChatMode = ChatMode.COMPANION,
        project_id: str | None = None,
        plugin_selection: list[ChatPluginSelectionItem] | None = None,
    ) -> ChatConversationProjection:
        """新建未锁定的日常对话草稿。

        ``plugin_selection`` 为新对话的初始插件选择（新聊天首页先选
        插件再建对话）；调用方负责逐项校验可用性，这里原样持久化。
        """
        self._ensure_extension_payload_allowed(plugin_selection=plugin_selection)
        if project_id is not None:
            raise ChatDomainError(
                "legacy_file_source_retired",
                "学习项目归属已退役，请使用全局知识库。",
                410,
            )
        project_id = None
        now = datetime.now(UTC)
        conversation_id = secrets.token_urlsafe(16)
        self._repo.create_conversation(
            account_id=account_id,
            conversation_id=conversation_id,
            title=(title or "").strip(),
            mode=mode.value,
            created_at=now,
            project_id=project_id,
            plugin_selection=(
                [item.model_dump() for item in plugin_selection]
                if plugin_selection
                else None
            ),
        )
        return self._project_conversation(
            account_id,
            conversation_id,
            title=(title or "").strip(),
            mode=mode,
            mode_locked=False,
            pinned=False,
            project_id=project_id,
            plugin_selection=list(plugin_selection or []),
            removed_selections=[],
            created_at=now,
            updated_at=now,
            messages=[],
            mode_events=[],
        )
    @staticmethod
    def _validate_study_payload(
        module_id: Any,
        image_payload: Any,
        video_payload: Any,
        mcp_call_payload: Any,
    ) -> None:
        if module_id is not None:
            raise ChatDomainError(
                "study_module_forbidden", "学习模式不能选择日常模块。", 422
            )
        if any(item is not None for item in (image_payload, video_payload, mcp_call_payload)):
            raise ChatDomainError(
                "study_payload_forbidden", "学习模式只接受本节书页照片和文字。", 422
            )
    def set_conversation_mode(
        self: ChatService,
        account_id: str,
        conversation_id: str,
        mode: ChatMode,
        *,
        traffic_class: str = "real",
    ) -> None:
        """兼容窗口内拒绝旧模式切换写请求。"""
        del account_id, conversation_id, mode
        if self._observability is not None:
            self._observability.record_compatibility_410(
                endpoint_id="chat.conversation_mode_switch",
                service_version=__version__,
                traffic_class=traffic_class,
            )
        raise ChatDomainError(
            "conversation_mode_switch_retired",
            "模式已在首条消息提交时锁定；请新建另一个会话以使用其他模式。",
            410,
        )
