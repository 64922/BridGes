"""聊天 API 路由（Issue 11 纵向切片）。

对外提供对话的创建/列表/读取、消息发送（SSE 流式）、停止与重试。
主对话链路不再依赖账户凭据或账户探测快照（GQ-02）：请求直接进入
已由启动硬门保证完成注册的全局模型网关；流式事件只包含消息标识、
增量正文与分类错误，绝不输出凭据、工作流节点 ID、调试字段或系统提示。
图片/视频载荷的能力门控在 GQ-04 前仍按账户探测快照执行。
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Annotated, Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from bridges.api.auth import SubjectDep
from bridges.chat.attachments import ChatAttachmentError, ChatAttachmentService
from bridges.chat.selections import ChatSelectionsService
from bridges.chat.service import (
    ChatDomainError,
    ChatService,
)
from bridges.contracts.chat import (
    ChatConversationListProjection,
    ChatConversationProjection,
    ChatConversationUpdateRequest,
    ChatCreateRequest,
    ChatError,
    ChatFirstTurnRequest,
    ChatFirstTurnResponse,
    ChatMessageCreateRequest,
    ChatMessageProjection,
    ChatMode,
    ChatModeSwitchRequest,
    ChatRunStartedResponse,
    ChatRunStatus,
    ChatStopResponse,
    ChatStreamEvent,
)
from bridges.contracts.feedback import (
    AnswerFeedback,
    AnswerFeedbackRequest,
    FeedbackResolveRequest,
)
from bridges.contracts.retrieval import CitationDetailProjection
from bridges.contracts.observability import AuditAction, AuditResult
from bridges.contracts.teaching_progress import (
    LearningProgressProjection,
    PlanAdjustment,
)
from bridges.ingestion.service import IngestionService
from bridges.observability.service import ObservabilityService
from bridges.retrieval.service import LayeredRetrievalService, RetrievalError
from bridges.retirement import record_compatibility_observation, raise_retired_file_source

router = APIRouter(prefix="/chat", tags=["chat"])


class HumanizerProjectionEventRequest(BaseModel):
    """文章结果投影前端事件载荷（Issue 08 Observability）。

    只含投影版本、交付状态、风险类型、事件名与 legacy 标志；请求体
    Schema 无正文字段，任何正文/引语/diff 内容都无法进入审计。
    """

    model_config = ConfigDict(extra="forbid")

    event: Literal["expand", "copy", "legacy_read"] = Field(
        description="前端事件类型：展开审计/复制正文/legacy 读取。"
    )
    projection_version: str | None = Field(
        default=None, description="投影版本（旧结果读取时为 null）。"
    )
    delivery_status: str | None = Field(
        default=None, description="交付状态（delivered/failed）。"
    )
    risk_types: list[str] = Field(
        default_factory=list, description="风险类型 code 清单（稳定枚举）。"
    )
    legacy: bool = Field(default=False, description="是否旧版结果（无新投影）。")

#: 订阅长轮询窗口（秒）：窗口内无新事件时发心跳保持连接。
_LONG_POLL_SECONDS = 25.0


def _get_chat_service(request: Request) -> ChatService:
    service: ChatService | None = getattr(request.app.state, "chat_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=ChatError(
                error="chat_unavailable",
                message="对话存储未启用，当前实例拒绝聊天读写。",
            ).model_dump(),
        )
    return service


ChatServiceDep = Annotated[ChatService, Depends(_get_chat_service)]


def _get_attachment_service(request: Request) -> ChatAttachmentService:
    service: ChatAttachmentService | None = getattr(
        request.app.state, "chat_attachment_service", None
    )
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=ChatError(
                error="attachments_unavailable",
                message="附件服务未启用，当前实例拒绝附件读写。",
            ).model_dump(),
        )
    return service


AttachmentServiceDep = Annotated[ChatAttachmentService, Depends(_get_attachment_service)]


def _get_ingestion_service(request: Request) -> IngestionService:
    service: IngestionService | None = getattr(
        request.app.state, "ingestion_service", None
    )
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=ChatError(
                error="ingestion_unavailable",
                message="文档摄取服务未启用，当前实例拒绝摄取操作。",
            ).model_dump(),
        )
    return service


IngestionServiceDep = Annotated[IngestionService, Depends(_get_ingestion_service)]


def _get_retrieval_service(request: Request) -> LayeredRetrievalService | None:
    """检索服务依赖：未挂载时返回 None（引用详情路由自行 503）。

    重试路径只需读取旧轮次的知识库开关，检索服务缺省时回退默认开启，
    与聊天主链路（检索可选）保持一致，避免重试在无检索挂载的实例上
    被 503 拦截。
    """
    return getattr(request.app.state, "retrieval_service", None)


RetrievalServiceDep = Annotated[
    LayeredRetrievalService | None, Depends(_get_retrieval_service)
]


def _get_selections_service(request: Request) -> ChatSelectionsService | None:
    """插件选择服务依赖：未挂载时返回 None（选择端点自行 503）。"""
    return getattr(request.app.state, "chat_selections_service", None)


SelectionsServiceDep = Annotated[
    ChatSelectionsService | None, Depends(_get_selections_service)
]


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=ChatError(error=code, message=message).model_dump(),
    )


def _handle_domain_error(exc: ChatDomainError) -> HTTPException:
    return _error(exc.status_code, exc.code, exc.message)


def _reject_retired_extension_fields(
    fields: set[str],
    *,
    request: Request | None = None,
    endpoint: str = "legacy.chat.extensions.write",
    plugin_selection: object = None,
    skill_id: str | None = None,
    skill_input: object = None,
    mcp_call: object = None,
) -> None:
    """在任何会话或消息写入前拦截旧扩展载荷。"""
    uses_plugin_selection = "plugin_selection" in fields or bool(plugin_selection)
    uses_mcp = "mcp_call" in fields or mcp_call is not None
    uses_unknown_skill = skill_id not in {None, "bridges-humanizer"}
    uses_skill_input_without_builtin = skill_input is not None and skill_id != "bridges-humanizer"
    if uses_plugin_selection or uses_mcp or uses_unknown_skill or uses_skill_input_without_builtin:
        if request is not None:
            record_compatibility_observation(request, endpoint)
        raise _error(
            status.HTTP_410_GONE,
            "user_extensions_retired",
            "用户 SKILL、插件与通用 MCP 已退役，请返回聊天或知识库。",
        )


def _reject_retired_file_fields(
    request: Request,
    fields: set[str],
    *,
    endpoint: str,
) -> None:
    """拒绝新请求中的历史项目文件归属字段。"""
    if "project_id" in fields:
        raise_retired_file_source(request, endpoint=endpoint)


def _sse_frame(event_kind: str, payload: dict[str, Any]) -> str:
    """按持久化事件记录编码 SSE 帧（kind 即事件名）。"""
    data = json.dumps(payload, ensure_ascii=False)
    return f"event: {event_kind}\ndata: {data}\n\n"


def _stream_response(events: Iterator[str]) -> StreamingResponse:
    return StreamingResponse(
        events,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "X-Accel-Buffering": "no",
        },
    )


def _subscribe_generation_events(
    service: ChatService,
    account_id: str,
    run_id: str,
    cursor: int,
) -> Iterator[str]:
    """SSE 订阅生成运行：回放游标之后的已持久化事件，终态后结束。

    Issue 02：运行状态与事件持久化在数据库，客户端断开只停止本生成器
    （移除订阅者），绝不改变运行状态；运行终态时全部事件（含终态事件）
    已可读，回放完即结束。长轮询窗口内无新事件时发送心跳保持连接。
    """
    local_cursor = cursor
    deadline = time.monotonic() + _LONG_POLL_SECONDS
    while True:
        records = service.generation_events(account_id, run_id, local_cursor)
        for record in records:
            yield _sse_frame(record.kind, record.payload)
            local_cursor = record.seq
        run = service.generation_run(account_id, run_id)
        if run is None or run.status not in {
            ChatRunStatus.QUEUED.value,
            ChatRunStatus.RUNNING.value,
        }:
            # 运行已终态：全部事件已回放，结束订阅
            return
        if time.monotonic() >= deadline:
            # 心跳保持连接（客户端忽略 ping 事件）
            yield "event: ping\ndata: {}\n\n"
            deadline = time.monotonic() + _LONG_POLL_SECONDS
        time.sleep(0.3)


@router.get(
    "/conversations",
    response_model=ChatConversationListProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ChatError},
    },
)
def list_conversations(
    service: ChatServiceDep,
    subject: SubjectDep,
) -> ChatConversationListProjection:
    """返回当前账户的对话列表（按最近活动倒序）。"""
    return service.list_conversations(subject.account_id)


@router.post(
    "/conversations",
    status_code=status.HTTP_409_CONFLICT,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_409_CONFLICT: {"model": ChatError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ChatError},
    },
)
def create_conversation(
    request: Request,
    body: ChatCreateRequest,
    _subject: SubjectDep,
) -> None:
    """拒绝空会话；新会话必须由首条消息在同一事务中创建。"""
    _reject_retired_file_fields(
        request, body.model_fields_set, endpoint="legacy.chat.conversations.create"
    )
    _reject_retired_extension_fields(
        body.model_fields_set,
        request=request,
        endpoint="legacy.chat.conversations.create",
        plugin_selection=body.plugin_selection,
    )
    if body.mode != ChatMode.COMPANION:
        raise _error(
            status.HTTP_409_CONFLICT,
            "study_mode_unavailable",
            "学习模式尚未开放；历史学习对话目前仅支持查看。",
        )
    raise _error(
        status.HTTP_409_CONFLICT,
        "first_turn_required",
        "请发送首条消息以创建会话。",
    )


@router.post(
    "/first-turn",
    response_model=ChatFirstTurnResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_200_OK: {
            "model": ChatFirstTurnResponse,
            "description": "幂等重放：同键已存在，返回既有首轮数据（不产生新数据）",
        },
        status.HTTP_201_CREATED: {"model": ChatFirstTurnResponse},
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_404_NOT_FOUND: {"model": ChatError},
        status.HTTP_409_CONFLICT: {"model": ChatError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ChatError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ChatError},
    },
)
def create_first_turn(
    request: Request,
    body: ChatFirstTurnRequest,
    service: ChatServiceDep,
    subject: SubjectDep,
    selections_service: SelectionsServiceDep,
    response: Response,
) -> ChatFirstTurnResponse:
    """原子创建新会话首轮（Issue 03）：同一事务创建会话、用户消息、
    助手占位与 queued 运行，返回完整投影。

    首页发送第一条消息或调用任一功能时使用——客户端收到成功响应后再
    导航，``sessionStorage`` 不再承担业务真相。``idempotency_key`` 抵御
    双击与网络重放：同键重放返回 200 与既有数据；新建返回 201。
    ``conversation_id`` 可选指定已预建的空会话，缺省在事务内新建会话；``mode``/``project_id``/
    ``plugin_selection`` 随首轮写入会话。失败整事务回滚，不留空草稿。
    """
    _reject_retired_file_fields(
        request, body.model_fields_set, endpoint="legacy.chat.first-turn.create"
    )
    _reject_retired_extension_fields(
        body.model_fields_set,
        request=request,
        endpoint="legacy.chat.first-turn.create",
        plugin_selection=body.plugin_selection,
        skill_id=body.skill_id,
        skill_input=body.skill_input,
        mcp_call=body.mcp_call,
    )
    try:
        if body.plugin_selection:
            if selections_service is None:
                raise _error(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "selections_unavailable",
                    "插件选择服务未启用，请稍后重试。",
                )
            result = selections_service.validate_items(
                subject.account_id, body.plugin_selection or []
            )
            if result.removed:
                reasons = "；".join(
                    f"「{entry.name}」{entry.reason}" for entry in result.removed
                )
                raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "plugin_not_available", reasons)
        first_turn = service.start_first_turn(
            subject.account_id,
            content=body.content,
            idempotency_key=body.idempotency_key,
            conversation_id=body.conversation_id,
            mode=body.mode,
            project_id=None,
            plugin_selection=body.plugin_selection,
            attachment_ids=None,
            skill_id=body.skill_id,
            skill_input=(
                body.skill_input.model_dump(mode="json") if body.skill_input else None
            ),
            image=(
                body.image.model_dump(mode="json") if body.image is not None else None
            ),
            video=(
                body.video.model_dump(mode="json") if body.video is not None else None
            ),
            mcp_call=(
                body.mcp_call.model_dump(mode="json")
                if body.mcp_call is not None
                else None
            ),
            use_knowledge_base=body.use_knowledge_base,
        )
    except ChatDomainError as exc:
        raise _handle_domain_error(exc) from exc
    if first_turn.idempotent_replay:
        response.status_code = status.HTTP_200_OK
    return first_turn


@router.patch(
    "/conversations/{conversation_id}",
    response_model=ChatConversationProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_404_NOT_FOUND: {"model": ChatError},
        status.HTTP_409_CONFLICT: {"model": ChatError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ChatError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ChatError},
    },
)
def update_conversation(
    conversation_id: str,
    request: Request,
    body: ChatConversationUpdateRequest,
    service: ChatServiceDep,
    subject: SubjectDep,
    selections_service: SelectionsServiceDep,
) -> ChatConversationProjection:
    """更新当前账户会话的标题、置顶状态、学习项目归属或插件选择。

    ``project_id`` 字段缺省表示归属不变；显式 null 解除归属。移动只改
    归属：消息、模式事件与附件绝不被触碰。携带 ``project_id`` 的 PATCH
    与标题/置顶在同一事务内提交：任一失败整体回滚，绝不留下半更新状态。
    ``plugin_selection`` 全量替换当前选择（显式 [] 清空）：逐项校验
    已安装且启用，停用/卸载/撤权项 422 拒绝并说明原因（读取路径的
    失效清洗在投影层完成并解释影响）。
    """
    _reject_retired_file_fields(
        request, body.model_fields_set, endpoint="legacy.chat.conversations.update"
    )
    _reject_retired_extension_fields(
        body.model_fields_set,
        request=request,
        endpoint="legacy.chat.conversations.update",
        plugin_selection=body.plugin_selection,
    )
    try:
        # 插件选择先做纯校验（validate_items 不写库）：422 拒绝发生在
        # 一切写入之前；通过后再按项目归属（事务内）→ 插件选择（事务内）
        # 顺序提交，任一失败都不留下半更新状态。
        if "plugin_selection" in body.model_fields_set:
            if selections_service is None:
                raise _error(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "selections_unavailable",
                    "插件选择服务未启用，请稍后重试。",
                )
            result = selections_service.validate_items(
                subject.account_id, body.plugin_selection or []
            )
            if result.removed:
                reasons = "；".join(
                    f"「{entry.name}」{entry.reason}" for entry in result.removed
                )
                raise _error(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    "plugin_not_available",
                    reasons,
                )
        return service.update_conversation(
            subject.account_id,
            conversation_id,
            title=body.title,
            pinned=body.pinned,
            plugin_selection=body.plugin_selection,
        )
    except ChatDomainError as exc:
        raise _handle_domain_error(exc) from exc


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_404_NOT_FOUND: {"model": ChatError},
        status.HTTP_409_CONFLICT: {"model": ChatError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ChatError},
    },
)
def delete_conversation(
    conversation_id: str,
    service: ChatServiceDep,
    subject: SubjectDep,
) -> Response:
    """删除当前账户会话及其消息历史；跨账户访问安全返回 404。"""
    try:
        service.delete_conversation(subject.account_id, conversation_id)
    except ChatDomainError as exc:
        raise _handle_domain_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/conversations/{conversation_id}/mode",
    status_code=status.HTTP_410_GONE,
    response_model=None,
    responses={
        status.HTTP_410_GONE: {"model": ChatError},
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ChatError},
    },
)
def switch_conversation_mode(
    conversation_id: str,
    body: ChatModeSwitchRequest,
    service: ChatServiceDep,
    subject: SubjectDep,
    request: Request,
) -> None:
    """兼容窗口内拒绝旧模式切换请求，并记录隐私安全的 410 观测。"""
    try:
        service.set_conversation_mode(
            subject.account_id,
            conversation_id,
            body.mode,
            traffic_class=(
                "probe"
                if request.headers.get("X-Bridges-Compatibility-Probe") == "1"
                else "real"
            ),
        )
    except ChatDomainError as exc:
        raise _handle_domain_error(exc) from exc


@router.get(
    "/conversations/{conversation_id}",
    response_model=ChatConversationProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_404_NOT_FOUND: {"model": ChatError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ChatError},
    },
)
def get_conversation(
    conversation_id: str,
    service: ChatServiceDep,
    subject: SubjectDep,
) -> ChatConversationProjection:
    """返回对话完整历史；跨账户访问返回 404，不泄漏存在性。"""
    projection = service.get_conversation(subject.account_id, conversation_id)
    if projection is None:
        raise _error(
            status.HTTP_404_NOT_FOUND,
            "conversation_not_found",
            "对话不存在或没有访问权限。",
        )
    return projection


@router.get(
    "/conversations/{conversation_id}/attachments",
    response_model=None,
    status_code=status.HTTP_410_GONE,
    responses={
        status.HTTP_410_GONE: {"model": ChatError},
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_404_NOT_FOUND: {"model": ChatError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ChatError},
    },
)
def list_unbound_attachments(
    conversation_id: str,
    request: Request,
    subject: SubjectDep,
) -> None:
    """列出会话内「已上传未绑定」附件（Issue 04 草稿恢复）。

    用户关页重开后据此把未发送附件恢复为待绑定状态；跨账户或不存在
    返回空集，不泄漏存在性。已绑定消息的附件经消息投影读取。
    """
    del conversation_id, subject
    raise_retired_file_source(request, endpoint="legacy.chat.attachments.list_unbound")


@router.post(
    "/conversations/{conversation_id}/attachments",
    response_model=None,
    status_code=status.HTTP_410_GONE,
    responses={
        status.HTTP_410_GONE: {"model": ChatError},
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_404_NOT_FOUND: {"model": ChatError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ChatError},
    },
)
async def upload_attachment(
    conversation_id: str,
    request: Request,
    subject: SubjectDep,
) -> Response:
    """聊天附件上传已退役；文件必须先进入全局知识库。"""
    del conversation_id, subject
    raise_retired_file_source(request, endpoint="legacy.chat.attachments.upload")


@router.get(
    "/conversations/{conversation_id}/attachments/{object_id}/download",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_404_NOT_FOUND: {"model": ChatError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ChatError},
    },
)
def download_attachment(
    conversation_id: str,
    object_id: str,
    service: AttachmentServiceDep,
    subject: SubjectDep,
) -> Response:
    """通过账户与对话授权读取附件，不暴露对象库路径。"""
    try:
        attachment, content = service.download(
            subject.account_id, conversation_id, object_id
        )
    except ChatAttachmentError as exc:
        raise _error(exc.status_code, exc.code, exc.message) from exc
    safe_filename = quote(attachment.original_filename, safe="")
    return Response(
        content=content,
        media_type=attachment.media_type,
        headers={
            "Content-Disposition": (
                f'attachment; filename="attachment"; filename*=UTF-8\'\'{safe_filename}'
            ),
            "Cache-Control": "no-store",
        },
    )


@router.delete(
    "/conversations/{conversation_id}/messages/{message_id}/attachments/{object_id}",
    status_code=status.HTTP_410_GONE,
    responses={
        status.HTTP_410_GONE: {"model": ChatError},
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_404_NOT_FOUND: {"model": ChatError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ChatError},
    },
)
def delete_message_attachment(
    conversation_id: str,
    message_id: str,
    object_id: str,
    request: Request,
    subject: SubjectDep,
) -> Response:
    """聊天附件删除已退役；历史附件仅保留读取兼容。"""
    del conversation_id, message_id, object_id, subject
    raise_retired_file_source(request, endpoint="legacy.chat.attachments.delete")


@router.delete(
    "/conversations/{conversation_id}/attachments/by-upload/{upload_id}",
    status_code=status.HTTP_410_GONE,
    responses={
        status.HTTP_410_GONE: {"model": ChatError},
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_404_NOT_FOUND: {"model": ChatError},
        status.HTTP_409_CONFLICT: {"model": ChatError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ChatError},
    },
)
def cancel_attachment_upload(
    conversation_id: str,
    upload_id: str,
    request: Request,
    subject: SubjectDep,
) -> Response:
    """聊天附件上传取消已退役。"""
    del conversation_id, upload_id, subject
    raise_retired_file_source(request, endpoint="legacy.chat.attachments.cancel_upload")


@router.delete(
    "/conversations/{conversation_id}/attachments/{object_id}",
    status_code=status.HTTP_410_GONE,
    responses={
        status.HTTP_410_GONE: {"model": ChatError},
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_404_NOT_FOUND: {"model": ChatError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ChatError},
    },
)
def cancel_attachment(
    conversation_id: str,
    object_id: str,
    request: Request,
    subject: SubjectDep,
) -> Response:
    """聊天附件取消已退役。"""
    del conversation_id, object_id, subject
    raise_retired_file_source(request, endpoint="legacy.chat.attachments.cancel")


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=ChatRunStartedResponse,
    responses={
        status.HTTP_200_OK: {
            "model": ChatRunStartedResponse,
            "description": (
                "创建响应：消息已落库、生成运行已入队；"
                "随后用 run_id/cursor 订阅持久化事件"
            ),
        },
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_404_NOT_FOUND: {"model": ChatError},
        status.HTTP_409_CONFLICT: {"model": ChatError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ChatError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ChatError},
    },
)
async def send_message(
    conversation_id: str,
    request: Request,
    body: ChatMessageCreateRequest,
    service: ChatServiceDep,
    subject: SubjectDep,
) -> ChatRunStartedResponse:
    """发送用户消息：同事务创建消息与 queued 生成运行，立即返回。

    Issue 02：HTTP 不再拥有生成生命周期——生成由后台执行器按租约领取
    执行，事件持久化到运行游标；客户端以返回的 ``run_id``/``cursor``
    订阅 ``GET .../events`` 恢复进度，断开/刷新/切换会话都不改变运行。
    主对话与图片/视频任务提交不检查账户凭据或探测快照（GQ-02/GQ-04）。
    ``use_knowledge_base=false``（Issue 20）：本轮检索与引用不含知识库
    """
    _reject_retired_extension_fields(
        body.model_fields_set,
        request=request,
        endpoint="legacy.chat.messages.send",
        skill_id=body.skill_id,
        skill_input=body.skill_input,
        mcp_call=body.mcp_call,
    )
    try:
        user_message, assistant_message = service.start_generation(
            subject.account_id,
            conversation_id,
            body.content,
            None,
            skill_id=body.skill_id,
            skill_input=(
                body.skill_input.model_dump(mode="json") if body.skill_input else None
            ),
            image=(
                body.image.model_dump(mode="json") if body.image is not None else None
            ),
            video=(
                body.video.model_dump(mode="json") if body.video is not None else None
            ),
            mcp_call=(
                body.mcp_call.model_dump(mode="json")
                if body.mcp_call is not None
                else None
            ),
            use_knowledge_base=body.use_knowledge_base,
        )
    except ChatDomainError as exc:
        raise _handle_domain_error(exc) from exc
    run_view = service.run_view_of(subject.account_id, assistant_message.message_id)
    if run_view is None:
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "generation_unavailable",
            "生成运行暂不可用，请稍后重试。",
        )
    return ChatRunStartedResponse(
        run_id=run_view.run_id,
        cursor=run_view.cursor,
        user_message=user_message,
        assistant_message=assistant_message,
    )


@router.get(
    "/conversations/{conversation_id}/messages/{message_id}/events",
    responses={
        status.HTTP_200_OK: {
            "model": ChatStreamEvent,
            "content": {
                "text/event-stream": {
                    "schema": {"$ref": "#/components/schemas/ChatStreamEvent"}
                }
            },
            "description": "SSE 订阅：回放游标之后的持久化事件 → 心跳 → 终态后结束",
        },
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_404_NOT_FOUND: {"model": ChatError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ChatError},
    },
)
def subscribe_message_events(
    conversation_id: str,
    message_id: str,
    service: ChatServiceDep,
    subject: SubjectDep,
    cursor: Annotated[int, Query(ge=0)] = 0,
) -> StreamingResponse:
    """订阅生成运行的持久化事件（游标续读，跨账户安全 404）。

    Issue 02：事件由后台执行器持久化，本端点只回放与等待——客户端断开
    只移除订阅者，不改变运行状态。回放完成后运行若未终态，长轮询等待
    新事件（25 秒窗口内发心跳）；运行终态时全部事件（含终态事件）已
    可读，回放完即结束。页面重开从 ``active_run.cursor`` 恢复订阅。
    """
    message = service.message_projection(subject.account_id, message_id)
    if message is None or message.conversation_id != conversation_id:
        raise _error(
            status.HTTP_404_NOT_FOUND,
            "message_not_found",
            "消息不存在或没有访问权限。",
        )
    run_view = service.run_view_of(subject.account_id, message_id)
    if run_view is None:
        # 无运行记录（旧版本数据）：无可订阅事件，立即空流结束
        return _stream_response(iter(()))
    return _stream_response(
        _subscribe_generation_events(
            service, subject.account_id, run_view.run_id, cursor
        )
    )


@router.get(
    "/conversations/{conversation_id}/messages/{message_id}/citations/{citation_id}",
    response_model=CitationDetailProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_404_NOT_FOUND: {"model": ChatError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ChatError},
    },
)
def citation_detail(
    conversation_id: str,
    message_id: str,
    citation_id: str,
    service: RetrievalServiceDep,
    subject: SubjectDep,
) -> CitationDetailProjection:
    """返回单条引用的证据详情（展开引用时实时校验授权）。

    引用不属于当前账户/对话/消息时返回统一 404；原文已删除或权限变化时
    返回安全中文状态，不暴露资源存在性。
    """
    if service is None:
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "retrieval_unavailable",
            "本地检索服务未启用，当前实例拒绝检索操作。",
        )
    try:
        return service.citation_detail(
            subject.account_id, conversation_id, message_id, citation_id
        )
    except RetrievalError as exc:
        raise _error(exc.status_code, exc.code, exc.message) from exc


def _mcp_confirmation_route(
    conversation_id: str,
    message_id: str,
    confirmation_id: str,
    request: Request,
    service: ChatServiceDep,
    subject: SubjectDep,
    denied: bool,
) -> ChatMessageProjection:
    """聊天内 MCP 敏感操作确认（approve/deny）共用实现。

    只允许确认本人消息上真实挂起的敏感调用；结果写回消息 mcp_call 列，
    刷新/恢复历史对话不丢失（Verification 4：MCP 拒权与确认覆盖）。
    """
    _reject_retired_extension_fields(
        {"mcp_call"},
        request=request,
        endpoint="legacy.chat.mcp.confirmation",
        mcp_call={},
    )
    try:
        if denied:
            return service.deny_mcp_confirmation(
                subject.account_id, conversation_id, message_id, confirmation_id
            )
        return service.approve_mcp_confirmation(
            subject.account_id, conversation_id, message_id, confirmation_id
        )
    except ChatDomainError as exc:
        raise _handle_domain_error(exc) from exc


@router.post(
    "/conversations/{conversation_id}/messages/{message_id}/mcp/confirmations/"
    "{confirmation_id}/approve",
    response_model=ChatMessageProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_404_NOT_FOUND: {"model": ChatError},
        status.HTTP_409_CONFLICT: {"model": ChatError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ChatError},
    },
)
def approve_message_mcp_confirmation(
    conversation_id: str,
    message_id: str,
    confirmation_id: str,
    request: Request,
    service: ChatServiceDep,
    subject: SubjectDep,
) -> ChatMessageProjection:
    """确认消息内 MCP 调用的敏感操作（仅本次调用有效）。"""
    return _mcp_confirmation_route(
        conversation_id, message_id, confirmation_id, request, service, subject, denied=False
    )


@router.post(
    "/conversations/{conversation_id}/messages/{message_id}/mcp/confirmations/"
    "{confirmation_id}/deny",
    response_model=ChatMessageProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_404_NOT_FOUND: {"model": ChatError},
        status.HTTP_409_CONFLICT: {"model": ChatError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ChatError},
    },
)
def deny_message_mcp_confirmation(
    conversation_id: str,
    message_id: str,
    confirmation_id: str,
    request: Request,
    service: ChatServiceDep,
    subject: SubjectDep,
) -> ChatMessageProjection:
    """拒绝消息内 MCP 调用的敏感操作（调用安全终止并落库 denied）。"""
    return _mcp_confirmation_route(
        conversation_id, message_id, confirmation_id, request, service, subject, denied=True
    )


@router.post(
    "/conversations/{conversation_id}/messages/{message_id}/stop",
    response_model=ChatStopResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_404_NOT_FOUND: {"model": ChatError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ChatError},
    },
)
def stop_message(
    conversation_id: str,
    message_id: str,
    service: ChatServiceDep,
    subject: SubjectDep,
) -> ChatStopResponse:
    """停止进行中的生成；幂等，已终态的消息直接返回当前状态。"""
    try:
        message = service.stop_generation(
            subject.account_id, conversation_id, message_id
        )
    except ChatDomainError as exc:
        raise _handle_domain_error(exc) from exc
    return ChatStopResponse(message=message)


@router.post(
    "/conversations/{conversation_id}/messages/{message_id}/retry",
    response_model=ChatRunStartedResponse,
    responses={
        status.HTTP_200_OK: {
            "model": ChatRunStartedResponse,
            "description": "创建响应：新尝试已落库、生成运行已入队；随后订阅持久化事件",
        },
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_404_NOT_FOUND: {"model": ChatError},
        status.HTTP_409_CONFLICT: {"model": ChatError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ChatError},
    },
)
async def retry_message(
    conversation_id: str,
    message_id: str,
    service: ChatServiceDep,
    subject: SubjectDep,
    retrieval_service: RetrievalServiceDep,
) -> ChatRunStartedResponse:
    """重试失败的助手消息：创建新的助手尝试与 queued 运行，立即返回。

    新尝试保留审计关系（尝试号递增），历史失败尝试原样保留；运行经后台
    执行器领取执行（与发送同一外壳）。检索作用域沿用被重试尝试轮次的
    设置（含知识库开关），不重复用户消息。重试与发送同源（GQ-02）。
    """
    # Issue 31：图片任务消息不走消息级重试——任务卡内提供同输入重试
    # （POST /image-tasks/{id}/retry），避免创建重复任务。
    previous = service.message_projection(subject.account_id, message_id)
    if previous is not None and previous.image is not None:
        raise _error(
            status.HTTP_409_CONFLICT,
            "image_task_retry_via_card",
            "图片任务请使用任务卡内的重试按钮。",
        )
    # Issue 32：视频任务消息同样不走消息级重试——任务卡内提供同输入
    # 重试（POST /video-tasks/{id}/retry），避免创建重复任务。
    if previous is not None and previous.video is not None:
        raise _error(
            status.HTTP_409_CONFLICT,
            "video_task_retry_via_card",
            "视频任务请使用任务卡内的重试按钮。",
        )
    # 重试沿用旧尝试轮次的全局知识库开关：新尝试生成新检索轮次，但用户
    # 发送时的设置不应被重试静默改变；检索服务未挂载时回退默认开启。
    previous_round = (
        retrieval_service.round_projection(subject.account_id, message_id)
        if retrieval_service is not None
        else None
    )
    use_knowledge_base = (
        previous_round.use_knowledge_base if previous_round is not None else True
    )
    # 画像使用开关同样沿用旧尝试轮次的披露快照（Issue 27）：用户发送前
    # 的关闭选择不因重试被静默改变；无快照时回退默认开启。
    previous_message = service.message_projection(subject.account_id, message_id)
    try:
        user_message, assistant_message = service.retry_generation(
            subject.account_id,
            conversation_id,
            message_id,
            use_knowledge_base=use_knowledge_base,
        )
    except ChatDomainError as exc:
        raise _handle_domain_error(exc) from exc
    run_view = service.run_view_of(subject.account_id, assistant_message.message_id)
    if run_view is None:
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "generation_unavailable",
            "生成运行暂不可用，请稍后重试。",
        )
    return ChatRunStartedResponse(
        run_id=run_view.run_id,
        cursor=run_view.cursor,
        user_message=user_message,
        assistant_message=assistant_message,
    )


@router.post(
    "/conversations/{conversation_id}/messages/{message_id}/feedback",
    response_model=AnswerFeedback,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_404_NOT_FOUND: {"model": ChatError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ChatError},
    },
)
def submit_message_feedback(
    conversation_id: str,
    message_id: str,
    body: AnswerFeedbackRequest,
    service: ChatServiceDep,
    subject: SubjectDep,
) -> AnswerFeedback:
    """提交对一条助手消息的回答反馈（Issue 27 反馈闭环入口）。

    ``answer_inappropriate`` 标记「这次回答有问题」（可带偏好反馈）；
    ``profile_incorrect`` 标记「画像记录有误」并定位到上下文说明披露中
    的画像记录。反馈幂等持久化：失败重试不会重复写入，不丢失用户反馈；
    画像修正由画像中心的 modify/freeze/withdraw 接口完成。
    """
    try:
        return service.submit_feedback(
            subject.account_id, conversation_id, message_id, body
        )
    except ChatDomainError as exc:
        raise _handle_domain_error(exc) from exc


@router.get(
    "/conversations/{conversation_id}/feedback",
    response_model=list[AnswerFeedback],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
    },
)
def list_conversation_feedback(
    conversation_id: str,
    service: ChatServiceDep,
    subject: SubjectDep,
) -> list[AnswerFeedback]:
    """列出对话内该账户的反馈（最新在前，供前端恢复与闭环查看）。"""
    return service.list_feedback(subject.account_id, conversation_id)


@router.get(
    "/conversations/{conversation_id}/learning-progress",
    response_model=LearningProgressProjection | None,
)
def get_learning_progress(
    conversation_id: str,
    service: ChatServiceDep,
    subject: SubjectDep,
) -> LearningProgressProjection | None:
    """恢复当前账户在对话内的轻量学习进度。"""
    try:
        return service.learning_progress(subject.account_id, conversation_id)
    except ChatDomainError as exc:
        raise _handle_domain_error(exc) from exc


@router.get(
    "/conversations/{conversation_id}/learning-plan-adjustments",
    response_model=list[PlanAdjustment],
)
def list_learning_plan_adjustments(
    conversation_id: str,
    service: ChatServiceDep,
    subject: SubjectDep,
) -> list[PlanAdjustment]:
    """恢复当前账户在该对话内的幂等计划调整记录。"""
    try:
        return service.learning_adjustments(subject.account_id, conversation_id)
    except ChatDomainError as exc:
        raise _handle_domain_error(exc) from exc


@router.post(
    "/conversations/{conversation_id}/feedback/{feedback_id}/resolve",
    response_model=AnswerFeedback,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_404_NOT_FOUND: {"model": ChatError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ChatError},
    },
)
def resolve_message_feedback(
    conversation_id: str,
    feedback_id: str,
    body: FeedbackResolveRequest,
    service: ChatServiceDep,
    subject: SubjectDep,
) -> AnswerFeedback:
    """把一条反馈标记为已处理并记录修正说明（幂等）。"""
    try:
        return service.resolve_feedback(
            subject.account_id, feedback_id, body.resolution_note
        )
    except ChatDomainError as exc:
        raise _handle_domain_error(exc) from exc


@router.post(
    "/humanizer/events",
    response_model=dict[str, bool],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ChatError},
    },
)
def record_humanizer_projection_event(
    body: HumanizerProjectionEventRequest,
    request: Request,
    subject: SubjectDep,
) -> dict[str, bool]:
    """记录文章结果投影前端事件（Issue 08 Observability）。

    只接受投影版本、交付状态、风险类型、事件名与 legacy 标志；请求体
    Schema 无正文字段，正文/引语/diff 内容无法进入审计。审计失败不
    阻断用户操作（遥测尽力而为）。
    """
    try:
        observability: ObservabilityService = request.app.state.observability_service
        observability.log_audit(
            actor_account_id=subject.account_id,
            action=AuditAction.HUMANIZER_RESULT_VIEW,
            result=AuditResult.SUCCESS,
            object_refs=[],
            reason="前端记录文章结果投影查看事件（展开/复制/legacy 读取）。",
            details={
                "event": body.event,
                "projection_version": body.projection_version,
                "delivery_status": body.delivery_status,
                "risk_types": body.risk_types,
                "legacy": body.legacy,
            },
        )
    except Exception:  # noqa: BLE001 - 遥测失败不阻断用户操作
        pass
    return {"ok": True}
