"""聊天服务的消息载荷与投影小函数。"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from bridges.arxiv_mcp.contracts import ArxivSearchProjection
from bridges.chat.repository import (
    MessageRecord,
)
from bridges.chat.service_contract import ChatDomainError
from bridges.contracts.chat import (
    ChatPluginSelectionItem,
    ChatStreamStartedData,
    ChatThinkingSummary,
    ImageRequestPayload,
    McpCallRequestPayload,
    VideoRequestPayload,
)
from bridges.contracts.humanizer import (
    HumanizerResultProjection,
)
from bridges.contracts.image import ImageTaskKind
from bridges.contracts.routing import RouteDecision
from bridges.contracts.teaching import TeachingTurnProjection
from bridges.routing import (
    CapabilityRoute,
)
from bridges.web_search.contracts import WebSearchProjection

logger = logging.getLogger(__name__)

_TITLE_MAX = 24


def _attachment_message_title(
    attachments: Any | None, account_id: str, attachment_ids: list[str]
) -> str:
    """纯附件消息的会话标题（Issue 10）。

    照片消息沿用既有占位标题；本轮首个附件是文件时用其文件名（截断），
    不再把文件消息误标为「照片消息」。附件模块在函数内导入，避免
    辅助模块在聊天包初始化早期引入附件/摄取依赖（循环导入）。
    """
    if attachments is None or not attachment_ids:
        return "附件消息"
    from bridges.chat.attachments import PHOTO_MEDIA_TYPES

    draft = attachments.get_draft(account_id, attachment_ids[0])
    if draft is None or draft.media_type in PHOTO_MEDIA_TYPES:
        return "照片消息"
    filename = str(draft.original_filename or "").strip() or "附件消息"
    return filename if len(filename) <= _TITLE_MAX else filename[:_TITLE_MAX] + "…"


def _started_event_payload(
    *,
    conversation_id: str,
    user_message_id: str,
    message_id: str,
    attempt_number: int,
    message: MessageRecord,
) -> dict[str, Any]:
    """构造 started 事件载荷（发送/重试共用；思考/搜索/教学投影随事件持久化）。

    载荷与订阅回放的 SSE 帧 ``data`` 部分一致（不含外层 event 包装）。
    """
    return ChatStreamStartedData(
        conversation_id=conversation_id,
        user_message_id=user_message_id,
        message_id=message_id,
        attempt_number=attempt_number,
        thinking=(
            ChatThinkingSummary(**message.thinking)
            if message.thinking is not None
            else None
        ),
        web_search=(
            WebSearchProjection(**message.web_search)
            if message.web_search is not None
            else None
        ),
        arxiv_search=(
            ArxivSearchProjection(**message.arxiv_search)
            if message.arxiv_search is not None
            else None
        ),
        route=_route_projection(message.route),  # type: ignore[arg-type]
        teaching=(
            TeachingTurnProjection.model_validate(message.teaching)
            if message.teaching is not None
            else None
        ),
    ).model_dump(mode="json")
def _route_projection(
    route: dict[str, Any] | None,
) -> CapabilityRoute | RouteDecision | None:
    if route is None:
        return None
    try:
        return RouteDecision.model_validate(route)
    except ValidationError:
        return CapabilityRoute.model_validate(route)
def _humanizer_projection(
    message: MessageRecord,
) -> HumanizerResultProjection | None:
    """把助手消息 skill 列解析为人味化结果投影（Issue 05 检查点容错）。

    生成中途的 skill 列可能只含写作调用检查点（非完整投影）——刷新/轮询
    期间解析失败返回 None，不抛异常；终态后始终是完整结果投影。
    """
    try:
        return HumanizerResultProjection.model_validate(message.skill)
    except ValidationError:
        return None
def _selection_set(
    entries: list[dict[str, Any]] | None,
) -> set[tuple[str, str]]:
    """由持久化 JSON 条目提取选择键集合（去重比较用）。"""
    result: set[tuple[str, str]] = set()
    for entry in entries or []:
        if isinstance(entry, dict) and "kind" in entry and "plugin_id" in entry:
            result.add((str(entry["kind"]), str(entry["plugin_id"])))
    return result
def _record_selection(
    entries: list[dict[str, Any]] | None,
) -> list[ChatPluginSelectionItem]:
    """把持久化 JSON 条目转为契约模型（非法条目跳过）。"""
    result: list[ChatPluginSelectionItem] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        try:
            result.append(ChatPluginSelectionItem(**entry))
        except (TypeError, ValueError):
            continue
    return result
def _validate_image_payload(
    image: dict[str, Any] | None, has_skill: bool
) -> dict[str, Any] | None:
    """校验图片请求载荷并落库为用户消息快照（与 SKILL 载荷互斥）。"""
    if image is None:
        return None
    if has_skill:
        raise ChatDomainError(
            "conflicting_payload", "SKILL 与图片请求不能同时携带。", 422
        )
    try:
        payload = ImageRequestPayload.model_validate(image)
    except ValidationError as exc:
        raise ChatDomainError(
            "invalid_image_request", "图片请求载荷无效。", 422
        ) from exc
    if payload.kind == ImageTaskKind.EDIT and not payload.source_object_id:
        raise ChatDomainError(
            "invalid_image_request", "编辑必须且只能选择一个来源图片。", 422
        )
    return payload.model_dump(mode="json")
def _validate_video_payload(
    video: dict[str, Any] | None, has_skill: bool
) -> dict[str, Any] | None:
    """校验文生视频请求载荷并落库为用户消息快照（与 SKILL 载荷互斥）。"""
    if video is None:
        return None
    if has_skill:
        raise ChatDomainError(
            "conflicting_payload", "SKILL 与视频请求不能同时携带。", 422
        )
    try:
        payload = VideoRequestPayload.model_validate(video)
    except ValidationError as exc:
        raise ChatDomainError(
            "invalid_video_request", "视频请求载荷无效。", 422
        ) from exc
    return payload.model_dump(mode="json")
def _validate_mcp_call_payload(
    mcp_call: dict[str, Any] | None, has_skill: bool
) -> dict[str, Any] | None:
    """校验 MCP 调用载荷并落库为用户消息快照（与 SKILL 载荷互斥）。

    与 image/video 载荷的互斥校验一致：同一轮只允许一种载荷驱动生成。
    """
    if mcp_call is None:
        return None
    if has_skill:
        raise ChatDomainError(
            "conflicting_payload", "SKILL 与 MCP 调用不能同时携带。", 422
        )
    try:
        payload = McpCallRequestPayload.model_validate(mcp_call)
    except ValidationError as exc:
        raise ChatDomainError(
            "invalid_mcp_call", "MCP 调用载荷无效。", 422
        ) from exc
    return payload.model_dump(mode="json")
