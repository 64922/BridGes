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
from typing import Annotated, Any
from urllib.parse import quote, unquote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse

from bridges.api.auth import SubjectDep
from bridges.chat.attachments import (
    MAX_ATTACHMENT_BYTES,
    ChatAttachmentError,
    ChatAttachmentService,
)
from bridges.chat.selections import ChatSelectionsService
from bridges.chat.service import (
    ChatDomainError,
    ChatService,
)
from bridges.contracts.chat import (
    ChatAttachmentProjection,
    ChatConversationListProjection,
    ChatConversationProjection,
    ChatConversationUpdateRequest,
    ChatCreateRequest,
    ChatError,
    ChatFirstTurnRequest,
    ChatFirstTurnResponse,
    ChatMessageCreateRequest,
    ChatMessageProjection,
    ChatModeSwitchRequest,
    ChatModeSwitchResponse,
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
from bridges.ingestion.service import IngestionError, IngestionService
from bridges.learning_projects import LearningProjectError, LearningProjectService
from bridges.retrieval.service import LayeredRetrievalService, RetrievalError

router = APIRouter(prefix="/chat", tags=["chat"])

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


def _get_learning_project_service(request: Request) -> LearningProjectService:
    service: LearningProjectService | None = getattr(
        request.app.state, "learning_project_service", None
    )
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=ChatError(
                error="projects_unavailable",
                message="学习项目服务未启用，当前实例拒绝创建项目归属会话。",
            ).model_dump(),
        )
    return service


LearningProjectServiceDep = Annotated[
    LearningProjectService, Depends(_get_learning_project_service)
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
    response_model=ChatConversationProjection,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ChatError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ChatError},
    },
)
def create_conversation(
    body: ChatCreateRequest,
    service: ChatServiceDep,
    subject: SubjectDep,
    learning_project_service: LearningProjectServiceDep,
    selections_service: SelectionsServiceDep,
) -> ChatConversationProjection:
    """新建对话；标题可选，缺省由首条消息自动推导。

    ``mode`` 缺省为日常陪伴；学习项目新建学习对话时传 ``study``。
    ``plugin_selection`` 为初始插件选择（新聊天首页先选插件再建对话），
    逐项校验当前账户已安装且启用，非法项 422 拒绝并说明原因。
    """
    try:
        if body.project_id is not None:
            try:
                learning_project_service.get_project(subject.account_id, body.project_id)
            except LearningProjectError as exc:
                raise _error(
                    status.HTTP_404_NOT_FOUND,
                    "project_not_found",
                    "学习项目不存在或没有访问权限。",
                ) from exc
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
        return service.create_conversation(
            subject.account_id,
            title=body.title,
            mode=body.mode,
            project_id=body.project_id,
            plugin_selection=body.plugin_selection,
        )
    except ChatDomainError as exc:
        raise _handle_domain_error(exc) from exc


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
    body: ChatFirstTurnRequest,
    service: ChatServiceDep,
    subject: SubjectDep,
    learning_project_service: LearningProjectServiceDep,
    selections_service: SelectionsServiceDep,
    response: Response,
) -> ChatFirstTurnResponse:
    """原子创建新会话首轮（Issue 03）：同一事务创建会话、用户消息、
    助手占位与 queued 运行，返回完整投影。

    首页发送第一条消息或调用任一功能时使用——客户端收到成功响应后再
    导航，``sessionStorage`` 不再承担业务真相。``idempotency_key`` 抵御
    双击与网络重放：同键重放返回 200 与既有数据；新建返回 201。
    ``conversation_id`` 可选指定已预建的空会话（附件上传路径先建会话
    再发送），缺省在事务内新建会话；``mode``/``project_id``/
    ``plugin_selection`` 随首轮写入会话。失败整事务回滚，不留空草稿。
    """
    try:
        if body.project_id is not None:
            try:
                learning_project_service.get_project(subject.account_id, body.project_id)
            except LearningProjectError as exc:
                raise _error(
                    status.HTTP_404_NOT_FOUND,
                    "project_not_found",
                    "学习项目不存在或没有访问权限。",
                ) from exc
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
            project_id=body.project_id,
            plugin_selection=body.plugin_selection,
            attachment_ids=body.attachment_ids,
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
            use_profile=body.use_profile,
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
    body: ChatConversationUpdateRequest,
    service: ChatServiceDep,
    subject: SubjectDep,
    learning_project_service: LearningProjectServiceDep,
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
        if "project_id" in body.model_fields_set:
            record = learning_project_service.update_conversation_metadata(
                subject.account_id,
                conversation_id,
                title=body.title,
                pinned=body.pinned,
                project_id=body.project_id,
            )
            if "plugin_selection" in body.model_fields_set:
                return service.update_conversation(
                    subject.account_id,
                    conversation_id,
                    plugin_selection=body.plugin_selection,
                )
            return service.projection_from_record(record)
        return service.update_conversation(
            subject.account_id,
            conversation_id,
            title=body.title,
            pinned=body.pinned,
            plugin_selection=body.plugin_selection,
        )
    except ChatDomainError as exc:
        raise _handle_domain_error(exc) from exc
    except LearningProjectError as exc:
        raise _error(exc.status_code, exc.code, exc.message) from exc


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
    response_model=ChatModeSwitchResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_404_NOT_FOUND: {"model": ChatError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ChatError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ChatError},
    },
)
def switch_conversation_mode(
    conversation_id: str,
    body: ChatModeSwitchRequest,
    service: ChatServiceDep,
    subject: SubjectDep,
) -> ChatModeSwitchResponse:
    """切换对话模式（日常陪伴/学习模式）。

    写入可见模式切换事件，只影响切换后的消息；既有消息、回答与引用
    不被重写。相同模式幂等返回当前投影。
    """
    try:
        conversation, event = service.set_conversation_mode(
            subject.account_id, conversation_id, body.mode
        )
    except ChatDomainError as exc:
        raise _handle_domain_error(exc) from exc
    return ChatModeSwitchResponse(conversation=conversation, event=event)


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


@router.post(
    "/conversations/{conversation_id}/attachments",
    responses={
        status.HTTP_201_CREATED: {"model": ChatAttachmentProjection},
        status.HTTP_400_BAD_REQUEST: {"model": ChatError},
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_404_NOT_FOUND: {"model": ChatError},
        status.HTTP_413_CONTENT_TOO_LARGE: {"model": ChatError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ChatError},
    },
)
async def upload_attachment(
    conversation_id: str,
    request: Request,
    service: AttachmentServiceDep,
    subject: SubjectDep,
    ingestion_service: IngestionServiceDep,
) -> Response:
    """接收原始文件字节；类型、扩展名、大小与文件名均由服务端校验。

    上传成功即把对象入队摄取（Issue 17）：解析、分块与索引由后台
    执行器完成；不支持解析的类型不创建摄取记录，附件投影显示"未索引"。
    """
    declared_length = request.headers.get("content-length")
    if declared_length is not None:
        try:
            if int(declared_length) > MAX_ATTACHMENT_BYTES:
                raise _error(
                    status.HTTP_413_CONTENT_TOO_LARGE,
                    "file_too_large",
                    "文件超过 10 MB 大小限制，请压缩后重试。",
                )
        except ValueError as exc:
            raise _error(
                status.HTTP_400_BAD_REQUEST, "invalid_request", "上传请求大小无效。"
            ) from exc
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_ATTACHMENT_BYTES:
            raise _error(
                status.HTTP_413_CONTENT_TOO_LARGE,
                "file_too_large",
                "文件超过 10 MB 大小限制，请压缩后重试。",
            )
        chunks.append(chunk)
    filename_header = request.headers.get("x-bridges-filename")
    if not filename_header:
        raise _error(status.HTTP_400_BAD_REQUEST, "invalid_filename", "缺少文件名，无法上传。")
    filename = unquote(filename_header)
    try:
        projection, created = service.upload(
            subject.account_id,
            conversation_id,
            filename,
            b"".join(chunks),
            upload_id=request.headers.get("x-bridges-upload-id"),
        )
    except ChatAttachmentError as exc:
        raise _error(exc.status_code, exc.code, exc.message) from exc
    # 入队幂等（INSERT OR IGNORE），重复上传与失败重试都安全；入队失败
    # 显式报错，前端可重试，绝不静默吞掉摄取记录缺失。会话归属学习项目
    # 时新附件携带项目归属（Issue 36）：纳入项目检索范围，清除归属后的
    # 新上传不再携带旧项目上下文。
    try:
        ingestion_service.enqueue(
            subject.account_id,
            projection.object_id,
            conversation_id,
            project_id=service.conversation_project_id(
                subject.account_id, conversation_id
            ),
        )
    except IngestionError as exc:
        raise _error(exc.status_code, exc.code, exc.message) from exc
    # 入队后重新读取投影：让响应携带最新摄取状态（queued），而非入队前快照。
    refreshed = service.get(
        subject.account_id, conversation_id, projection.object_id
    )
    if refreshed is not None:
        projection = refreshed.projection()
    return JSONResponse(
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        content=projection.model_dump(mode="json"),
    )


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
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_404_NOT_FOUND: {"model": ChatError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ChatError},
    },
)
def delete_message_attachment(
    conversation_id: str,
    message_id: str,
    object_id: str,
    service: AttachmentServiceDep,
    subject: SubjectDep,
) -> Response:
    """解除消息引用并按对象引用计数安全清理附件。"""
    try:
        service.delete(
            subject.account_id,
            conversation_id,
            object_id,
            message_id=message_id,
        )
    except ChatAttachmentError as exc:
        raise _error(exc.status_code, exc.code, exc.message) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/conversations/{conversation_id}/attachments/by-upload/{upload_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_404_NOT_FOUND: {"model": ChatError},
        status.HTTP_409_CONFLICT: {"model": ChatError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ChatError},
    },
)
def cancel_attachment_upload(
    conversation_id: str,
    upload_id: str,
    service: AttachmentServiceDep,
    subject: SubjectDep,
) -> Response:
    """按上传幂等标识取消未绑定项，覆盖客户端中止后的服务端竞态。"""
    try:
        service.delete_by_upload_id(subject.account_id, conversation_id, upload_id)
    except ChatAttachmentError as exc:
        raise _error(exc.status_code, exc.code, exc.message) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/conversations/{conversation_id}/attachments/{object_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_404_NOT_FOUND: {"model": ChatError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ChatError},
    },
)
def cancel_attachment(
    conversation_id: str,
    object_id: str,
    service: AttachmentServiceDep,
    subject: SubjectDep,
) -> Response:
    """删除尚未绑定消息的上传项，用于取消或移除待发送附件。"""
    try:
        service.delete(subject.account_id, conversation_id, object_id)
    except ChatAttachmentError as exc:
        raise _error(exc.status_code, exc.code, exc.message) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
    候选；``use_profile=false``（Issue 27）：本轮请求、审计与上下文说明
    均不含任何画像切片（开关随运行快照落库，重试沿用）。
    """
    try:
        user_message, assistant_message = service.start_generation(
            subject.account_id,
            conversation_id,
            body.content,
            body.attachment_ids,
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
            use_profile=body.use_profile,
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
    service: ChatServiceDep,
    subject: SubjectDep,
    denied: bool,
) -> ChatMessageProjection:
    """聊天内 MCP 敏感操作确认（approve/deny）共用实现。

    只允许确认本人消息上真实挂起的敏感调用；结果写回消息 mcp_call 列，
    刷新/恢复历史对话不丢失（Verification 4：MCP 拒权与确认覆盖）。
    """
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
    service: ChatServiceDep,
    subject: SubjectDep,
) -> ChatMessageProjection:
    """确认消息内 MCP 调用的敏感操作（仅本次调用有效）。"""
    return _mcp_confirmation_route(
        conversation_id, message_id, confirmation_id, service, subject, denied=False
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
    service: ChatServiceDep,
    subject: SubjectDep,
) -> ChatMessageProjection:
    """拒绝消息内 MCP 调用的敏感操作（调用安全终止并落库 denied）。"""
    return _mcp_confirmation_route(
        conversation_id, message_id, confirmation_id, service, subject, denied=True
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
    use_profile = (
        previous_message.context_note.profile_enabled
        if previous_message is not None and previous_message.context_note is not None
        else True
    )
    try:
        user_message, assistant_message = service.retry_generation(
            subject.account_id,
            conversation_id,
            message_id,
            use_knowledge_base=use_knowledge_base,
            use_profile=use_profile,
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
