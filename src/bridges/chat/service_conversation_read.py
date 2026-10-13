# mypy: disable-error-code="misc"

"""聊天服务的会话读取方法。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from bridges.chat.repository import (
    ConversationRecord,
)
from bridges.chat.selections import (
    SelectionResolution,
    selection_key,
)
from bridges.chat.service_contract import ChatDomainError
from bridges.chat.service_helpers import (
    _record_selection,
    _selection_set,
)
from bridges.contracts.chat import (
    ChatConversationListProjection,
    ChatConversationProjection,
    ChatConversationSummary,
    ChatMessageStatus,
    ChatMode,
    ChatPluginSelectionItem,
    ChatRunView,
    RemovedPluginSelection,
)
from bridges.state_copy import (
    WAIT_DELETE_IN_PROGRESS_TEXT,
)

if TYPE_CHECKING:
    from bridges.chat.service import ChatService


class _ServiceConversationReadMixin:
    def list_conversations(self: ChatService, account_id: str) -> ChatConversationListProjection:
        records = self._repo.list_conversations(account_id)
        summaries: list[ChatConversationSummary] = []
        for record in records:
            message_count = self._repo.message_count(account_id, record.conversation_id)
            # 新聊天页在发送前会先创建一个无标题空草稿；它不是可回访的
            # 最近会话，不能伪装成已存在的临时聊天。
            if not record.title and message_count == 0:
                continue
            summaries.append(
                ChatConversationSummary(
                    conversation_id=record.conversation_id,
                    title=record.title,
                    mode=ChatMode(record.mode),
                    mode_locked=record.mode_locked,
                    pinned=record.pinned,
                    project_id=record.project_id,
                    legacy_project_name=record.legacy_project_name,
                    message_count=message_count,
                    created_at=record.created_at,
                    updated_at=record.updated_at,
                )
            )
        return ChatConversationListProjection(conversations=summaries)
    def update_conversation(
        self: ChatService,
        account_id: str,
        conversation_id: str,
        *,
        title: str | None = None,
        pinned: bool | None = None,
        plugin_selection: list[ChatPluginSelectionItem] | None = None,
    ) -> ChatConversationProjection:
        """改名、置顶或替换插件选择；跨账户目标统一返回安全 404。

        ``plugin_selection`` 全量替换当前选择；显式提交的选择逐项校验
        可用性（已停用/已卸载/撤权项 422 拒绝并说明原因，不静默清洗
        用户意图）；读取路径的失效清洗由 ``get_conversation`` 负责。
        """
        self._ensure_extension_payload_allowed(plugin_selection=plugin_selection)
        record = self._repo.get_conversation(account_id, conversation_id)
        if record is None:
            raise ChatDomainError("conversation_not_found", "对话不存在或没有访问权限。", 404)
        normalized_title = title.strip() if title is not None else None
        if title is not None and not normalized_title:
            raise ChatDomainError("invalid_title", "对话标题不能为空。", 422)
        changed = normalized_title != record.title if normalized_title is not None else False
        changed = changed or (pinned is not None and pinned != record.pinned)
        selection = None
        removed: list[RemovedPluginSelection] = []
        if plugin_selection is not None:
            if self._selections is None:
                raise ChatDomainError(
                    "selections_unavailable", "插件选择服务未启用，请稍后重试。", 503
                )
            result = self._selections.validate_items(account_id, plugin_selection)
            if result.removed:
                reasons = "；".join(
                    f"「{entry.name}」{entry.reason}" for entry in result.removed
                )
                raise ChatDomainError("plugin_not_available", reasons, 422)
            current = _selection_set(record.plugin_selection)
            target = {selection_key(item) for item in result.valid}
            changed = changed or current != target
            if current != target:
                selection = result.valid
            removed = result.removed
        now = datetime.now(UTC)
        if changed:
            self._repo.update_conversation(
                account_id,
                conversation_id,
                title=normalized_title,
                pinned=pinned,
                plugin_selection=(
                    [item.model_dump() for item in selection]
                    if selection is not None
                    else None
                ),
                updated_at=now,
            )
            record = self._repo.get_conversation(account_id, conversation_id)
            assert record is not None
        valid_selection = (
            selection
            if selection is not None
            else _record_selection(record.plugin_selection)
        )
        return self._project_conversation(
            account_id,
            conversation_id,
            title=record.title,
            mode=ChatMode(record.mode),
            mode_locked=record.mode_locked,
            pinned=record.pinned,
            project_id=record.project_id,
            plugin_selection=valid_selection,
            removed_selections=removed,
            created_at=record.created_at,
            updated_at=record.updated_at,
            messages=self._repo.list_messages(account_id, conversation_id),
            mode_events=self._repo.list_mode_events(account_id, conversation_id),
        )
    def projection_from_record(
        self: ChatService, record: ConversationRecord
    ) -> ChatConversationProjection:
        """由会话记录构造完整投影（与 update_conversation 同一响应形状）。

        组合更新路径（标题/置顶 + 学习项目归属在同一事务内完成，见
        ``LearningProjectService.update_conversation_metadata``）用已更新
        的记录投影响应，不触发读取路径的陈旧流收敛。
        """
        resolution = self._resolve_selections(record.account_id, record.conversation_id)
        return self._project_conversation(
            record.account_id,
            record.conversation_id,
            title=record.title,
            mode=ChatMode(record.mode),
            mode_locked=record.mode_locked,
            pinned=record.pinned,
            project_id=record.project_id,
            plugin_selection=resolution.valid,
            removed_selections=resolution.removed,
            created_at=record.created_at,
            updated_at=record.updated_at,
            messages=self._repo.list_messages(record.account_id, record.conversation_id),
            mode_events=self._repo.list_mode_events(
                record.account_id, record.conversation_id
            ),
        )
    def _resolve_selections(
        self: ChatService, account_id: str, conversation_id: str
    ) -> SelectionResolution:
        """读取会话选择并校验清洗（Issue 36）；未挂载选择服务时原样返回。"""
        if self._selections is None:
            record = self._repo.get_conversation(account_id, conversation_id)
            return SelectionResolution(
                valid=_record_selection(record.plugin_selection)
                if record is not None
                else []
            )
        return self._selections.resolve(account_id, conversation_id)
    def delete_conversation(self: ChatService, account_id: str, conversation_id: str) -> None:
        """删除自己的会话及其历史；生成中会话先拒绝，避免删除流状态。"""
        record = self._repo.get_conversation(account_id, conversation_id)
        if record is None:
            raise ChatDomainError("conversation_not_found", "对话不存在或没有访问权限。", 404)
        if any(
            message.status == ChatMessageStatus.STREAMING
            for message in self._repo.list_messages(account_id, conversation_id)
        ):
            raise ChatDomainError(
                "generation_in_progress", WAIT_DELETE_IN_PROGRESS_TEXT, 409
            )
        deleted = self._repo.delete_conversation(account_id, conversation_id)
        if deleted != 1:
            raise ChatDomainError("conversation_not_found", "对话不存在或没有访问权限。", 404)
        if self._attachments is not None:
            self._attachments.delete_for_conversation(account_id, conversation_id)
    def get_conversation(
        self: ChatService, account_id: str, conversation_id: str
    ) -> ChatConversationProjection | None:
        """返回对话完整投影；不存在的对话返回 None。

        读取时以持久化生成为活跃判定源（Issue 02）：仍有 queued/running
        运行的消息视为进行中，读取不打断；没有活跃运行的残留 streaming
        消息交给生成终态 module 的陈旧收敛（Issue 03）——按已终态的运行
        修复，或按迁移前的 stream_interrupted 语义兜底，并连带补齐缺失的
        终态事件与运行。绝不把半截占位当回答，也绝不误伤可恢复的运行。
        """
        record = self._repo.get_conversation(account_id, conversation_id)
        if record is None:
            return None
        messages = self._repo.list_messages(account_id, conversation_id)
        active_runs = {
            run.assistant_message_id: run
            for run in self._repo.list_active_runs(account_id, conversation_id)
        }
        run_views: dict[str, ChatRunView] = {}
        for index, message in enumerate(messages):
            if message.status != ChatMessageStatus.STREAMING:
                continue
            active = active_runs.get(message.message_id)
            if active is not None:
                # 运行尚未终态：生成仍在进行（或等待执行器领取），不打断
                run_views[message.message_id] = self._run_view(active, account_id)
                continue
            # 残留 streaming（无活跃运行）：陈旧收敛唯一入口；收敛后以落库
            # 记录为准重建投影，读取路径不再自拼终态文案与思考摘要。
            self.terminal.reconcile_stale_message(account_id, message.message_id)
            refreshed = self._repo.get_message(account_id, message.message_id)
            if refreshed is not None:
                messages[index] = refreshed
        resolution = self._resolve_selections(account_id, conversation_id)
        return self._project_conversation(
            account_id,
            record.conversation_id,
            title=record.title,
            mode=ChatMode(record.mode),
            mode_locked=record.mode_locked,
            pinned=record.pinned,
            project_id=record.project_id,
            plugin_selection=resolution.valid,
            removed_selections=resolution.removed,
            created_at=record.created_at,
            updated_at=record.updated_at,
            messages=messages,
            mode_events=self._repo.list_mode_events(account_id, conversation_id),
            run_views=run_views,
        )
