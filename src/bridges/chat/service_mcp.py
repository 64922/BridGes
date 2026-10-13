# mypy: disable-error-code="misc"

"""聊天服务的 MCP 方法。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from bridges.chat.service_contract import ChatDomainError
from bridges.chat.turn import (
    STREAM_INTERRUPTED_MESSAGE,  # noqa: F401 - re-export
    error_is_retryable,  # noqa: F401 - re-export
    result_summary,
    user_facing_error,  # noqa: F401 - re-export
)
from bridges.contracts.chat import (
    ChatMessageProjection,
    McpCallMessageProjection,
    McpCallStatus,
)
from bridges.contracts.mcp import McpError

if TYPE_CHECKING:
    from bridges.chat.service import ChatService


class _ServiceMcpMixin:
    def approve_mcp_confirmation(
        self: ChatService,
        account_id: str,
        conversation_id: str,
        message_id: str,
        confirmation_id: str,
    ) -> ChatMessageProjection:
        """聊天内敏感操作确认（approve）：调 MCP 服务 → 更新消息投影。

        只允许确认本人消息上真实挂起的敏感调用；结果写回消息 mcp_call
        列（终态后也可更新），刷新/恢复历史对话不丢失。
        """
        self._ensure_extension_payload_allowed(mcp_call={})
        return self._resolve_mcp_confirmation(
            account_id, conversation_id, message_id, confirmation_id, denied=False
        )
    def deny_mcp_confirmation(
        self: ChatService,
        account_id: str,
        conversation_id: str,
        message_id: str,
        confirmation_id: str,
    ) -> ChatMessageProjection:
        """聊天内敏感操作拒绝（deny）：调用安全终止并落库 denied 终态。"""
        self._ensure_extension_payload_allowed(mcp_call={})
        return self._resolve_mcp_confirmation(
            account_id, conversation_id, message_id, confirmation_id, denied=True
        )
    def _resolve_mcp_confirmation(
        self: ChatService,
        account_id: str,
        conversation_id: str,
        message_id: str,
        confirmation_id: str,
        *,
        denied: bool,
    ) -> ChatMessageProjection:
        if self._mcp is None:
            raise ChatDomainError("mcp_unavailable", "MCP 服务不可用，请稍后重试。", 503)
        message = self._repo.get_message(account_id, message_id)
        if message is None or message.conversation_id != conversation_id:
            raise ChatDomainError("message_not_found", "消息不存在或没有访问权限。", 404)
        if not message.mcp_call:
            raise ChatDomainError(
                "no_mcp_call", "该消息没有 MCP 调用，无法确认。", 409
            )
        call = McpCallMessageProjection.model_validate(message.mcp_call)
        if (
            call.status != McpCallStatus.SENSITIVE_PENDING
            or call.confirmation is None
            or call.confirmation.confirmation_id != confirmation_id
        ):
            raise ChatDomainError(
                "no_pending_confirmation", "该调用没有待确认的敏感操作。", 409
            )
        try:
            if denied:
                result = self._mcp.deny(account_id, call.mcp_id, confirmation_id)
            else:
                result = self._mcp.approve(account_id, call.mcp_id, confirmation_id)
        except McpError as exc:
            raise ChatDomainError(exc.code, exc.message, exc.status_code) from exc
        if denied:
            updated = call.model_copy(
                update={
                    "status": McpCallStatus.DENIED,
                    "error_code": "sensitive_denied",
                    "error_message": "已拒绝本次敏感操作，调用安全终止。",
                    "updated_at": datetime.now(UTC),
                }
            )
        elif result.status == "success":
            updated = call.model_copy(
                update={
                    "status": McpCallStatus.SUCCEEDED,
                    "result_summary": result_summary(result.result),
                    "confirmation": None,
                    "updated_at": datetime.now(UTC),
                }
            )
        else:
            updated = call.model_copy(
                update={
                    "status": McpCallStatus.FAILED,
                    "error_code": result.error_code,
                    "error_message": result.error_message or "调用失败，请重试。",
                    "confirmation": None,
                    "updated_at": datetime.now(UTC),
                }
            )
        self._repo.update_message_mcp_call(
            account_id, message_id, updated.model_dump(mode="json"), datetime.now(UTC)
        )
        refreshed = self._repo.get_message(account_id, message_id)
        assert refreshed is not None
        return self._project_message(refreshed)
