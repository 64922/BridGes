"""聊天 API 路由（Issue 11 纵向切片）。

对外提供对话的创建/列表/读取、消息发送（SSE 流式）、停止与重试。
能力就绪性在发送前通过账户级探测快照把关：无 Key / 探测中 / 核心能力
不可用分别返回可操作中文提示。流式事件只包含消息标识、增量正文与
分类错误，绝不输出凭据、工作流节点 ID、调试字段或系统提示。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import quote, unquote

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse

from bridges.api.auth import SubjectDep
from bridges.chat.attachments import (
    MAX_ATTACHMENT_BYTES,
    ChatAttachmentError,
    ChatAttachmentService,
)
from bridges.chat.service import (
    ChatDomainError,
    ChatService,
    error_is_retryable,
    user_facing_error,
)
from bridges.contracts.chat import (
    ChatAttachmentProjection,
    ChatConversationListProjection,
    ChatConversationProjection,
    ChatConversationUpdateRequest,
    ChatCreateRequest,
    ChatError,
    ChatMessageCreateRequest,
    ChatMessageProjection,
    ChatModeSwitchRequest,
    ChatModeSwitchResponse,
    ChatStopResponse,
)
from bridges.contracts.credentials import ProbeStatus
from bridges.contracts.projects import ObjectDomain
from bridges.contracts.workflows import RunContextEnvelope
from bridges.credentials.service import KeyCredentialService
from bridges.projects import ProjectError as ProjectServiceError
from bridges.projects import ProjectService

router = APIRouter(prefix="/chat", tags=["chat"])


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


def _get_credential_service(request: Request) -> KeyCredentialService:
    service: KeyCredentialService | None = getattr(
        request.app.state, "credential_service", None
    )
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=ChatError(
                error="credentials_unavailable",
                message="凭据服务未启用，当前实例拒绝聊天读写。",
            ).model_dump(),
        )
    return service


CredentialServiceDep = Annotated[KeyCredentialService, Depends(_get_credential_service)]


def _get_project_service(request: Request) -> ProjectService:
    service: ProjectService | None = getattr(request.app.state, "project_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=ChatError(
                error="projects_unavailable",
                message="学习项目服务未启用，当前实例拒绝创建项目归属会话。",
            ).model_dump(),
        )
    return service


ProjectServiceDep = Annotated[ProjectService, Depends(_get_project_service)]


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=ChatError(error=code, message=message).model_dump(),
    )


def _handle_domain_error(exc: ChatDomainError) -> HTTPException:
    return _error(exc.status_code, exc.code, exc.message)


def _ensure_chat_capability_ready(
    subject: SubjectDep, credential_service: KeyCredentialService
) -> None:
    """发送前核对账户级核心对话能力探测快照（ADR-0005/0009）。

    不要求近期重认证（那是密钥设置页的敏感操作门槛）；这里只读状态，
    让聊天发送不被敏感设置页的 5 分钟窗口打断。
    """
    try:
        projection = credential_service.get_projection(subject.account_id)
    except Exception as exc:  # noqa: BLE001 - 凭据存储异常统一折叠为可操作提示
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "credential_store_unavailable",
            "凭据存储当前不可用，请稍后重试。",
        ) from exc
    if not projection.configured:
        raise _error(
            status.HTTP_409_CONFLICT,
            "no_api_key",
            "尚未配置 Qwen API Key，请前往「设置」中的密钥页配置后再试。",
        )
    chat = next(
        (
            capability
            for capability in projection.capabilities
            if capability.capability_id == "chat"
        ),
        None,
    )
    if chat is None:
        raise _error(
            status.HTTP_409_CONFLICT,
            "capability_unavailable",
            "核心对话能力信息缺失，请前往「设置」重新探测。",
        )
    if chat.status == ProbeStatus.PROBING:
        raise _error(
            status.HTTP_409_CONFLICT,
            "capability_probing",
            "核心对话能力正在探测中，请稍候再试。",
        )
    if chat.status != ProbeStatus.AVAILABLE:
        reason = chat.message or "未知原因"
        raise _error(
            status.HTTP_409_CONFLICT,
            "capability_unavailable",
            f"核心对话能力当前不可用：{reason}。请前往「设置」重新探测。",
        )


def _chat_run_context(account_id: str, conversation_id: str, run_id: str) -> RunContextEnvelope:
    """为聊天生成构造合成运行上下文（对话即作用域容器）。"""
    return RunContextEnvelope(
        run_id=run_id,
        account_id=account_id,
        project_id=conversation_id,
        workflow_name="chat",
        workflow_version="1",
        object_domain=ObjectDomain.PERSONAL_VAULT,
        submitted_at=datetime.now(UTC),
    )


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _stream_response(
    events: Iterator[tuple[str, dict[str, Any]]],
) -> StreamingResponse:
    def generate() -> Iterator[str]:
        for event, data in events:
            yield _sse(event, data)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "X-Accel-Buffering": "no",
        },
    )


def _generation_events(
    service: ChatService,
    subject: SubjectDep,
    conversation_id: str,
    user_message: ChatMessageProjection,
    assistant_message: ChatMessageProjection,
    run_context: RunContextEnvelope,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """发送/重试共用的 SSE 事件序列：started → delta* → done | error。

    若消息在生成器启动前已被并发收敛（停止/断流/TTL 收敛抢先），生成器
    不会产出任何事件——此时读取消息当前状态补发诚实的终态事件，保证
    started 之后必有 done/error，绝不悬挂。
    """
    yield (
        "started",
        {
            "conversation_id": conversation_id,
            "user_message_id": user_message.message_id,
            "message_id": assistant_message.message_id,
            "attempt_number": assistant_message.attempt_number,
            # 初始思考摘要：前端据此自动展开思考区域（不暴露原始思维链）
            "thinking": (
                assistant_message.thinking.model_dump(mode="json")
                if assistant_message.thinking is not None
                else None
            ),
        },
    )
    terminated = False
    for event in service.stream_generation(
        subject.account_id,
        conversation_id,
        assistant_message.message_id,
        run_context,
        # 生成上下文截断到本轮用户消息：发送路径为新消息（等价完整历史），
        # 重试路径为该轮所属用户消息（不含后续轮次，Issue 11 重试语义）。
        until_user_message_id=user_message.message_id,
    ):
        if event.kind == "delta":
            yield "delta", {
                "message_id": assistant_message.message_id,
                "delta": event.delta,
            }
        elif event.kind == "error":
            terminated = True
            final = service.message_projection(
                subject.account_id, assistant_message.message_id
            )
            yield "error", {
                "message_id": assistant_message.message_id,
                "error": {
                    "code": event.error_code,
                    "message": user_facing_error(event.error_code, event.error_message),
                    "retryable": error_is_retryable(event.error_code),
                },
                # 失败/停止/断流时保留已完成思考摘要与真实耗时
                "thinking": (
                    final.thinking.model_dump(mode="json")
                    if final is not None and final.thinking is not None
                    else None
                ),
                "duration_ms": final.duration_ms if final is not None else None,
            }
            return
        elif event.kind == "done":
            terminated = True
            final = service.message_projection(
                subject.account_id, assistant_message.message_id
            )
            yield "done", {
                "message_id": assistant_message.message_id,
                "message": final.model_dump(mode="json") if final is not None else None,
            }
            return
    if terminated:
        return
    # 生成器空产出：以消息当前状态补发终态
    final = service.message_projection(subject.account_id, assistant_message.message_id)
    if final is None:
        return
    if final.status.value == "done":
        yield "done", {
            "message_id": assistant_message.message_id,
            "message": final.model_dump(mode="json"),
        }
        return
    if final.status.value == "stopped":
        yield "error", {
            "message_id": assistant_message.message_id,
            "error": {"code": "stopped", "message": "生成已停止。", "retryable": True},
            # 与 error 事件载荷一致：补发终态也携带思考摘要与真实耗时，
            # 前端停止后的 error 态渲染不依赖重新加载的间隙。
            "thinking": (
                final.thinking.model_dump(mode="json")
                if final.thinking is not None
                else None
            ),
            "duration_ms": final.duration_ms,
        }
        return
    yield "error", {
        "message_id": assistant_message.message_id,
        "error": {
            "code": final.error_code,
            "message": final.error_message or "生成失败，请稍后重试。",
            "retryable": error_is_retryable(final.error_code),
        },
        "thinking": (
            final.thinking.model_dump(mode="json")
            if final.thinking is not None
            else None
        ),
        "duration_ms": final.duration_ms,
    }


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
    project_service: ProjectServiceDep,
) -> ChatConversationProjection:
    """新建对话；标题可选，缺省由首条消息自动推导。

    ``mode`` 缺省为日常陪伴；学习项目新建学习对话时传 ``study``。
    """
    try:
        if body.project_id is not None:
            try:
                project_service.get_project(subject.account_id, body.project_id)
            except ProjectServiceError as exc:
                raise _error(
                    status.HTTP_404_NOT_FOUND,
                    "project_not_found",
                    "学习项目不存在或没有访问权限。",
                ) from exc
        return service.create_conversation(
            subject.account_id,
            title=body.title,
            mode=body.mode,
            project_id=body.project_id,
        )
    except ChatDomainError as exc:
        raise _handle_domain_error(exc) from exc


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
) -> ChatConversationProjection:
    """更新当前账户会话的标题或置顶状态。"""
    try:
        return service.update_conversation(
            subject.account_id,
            conversation_id,
            title=body.title,
            pinned=body.pinned,
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
) -> Response:
    """接收原始文件字节；类型、扩展名、大小与文件名均由服务端校验。"""
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
    responses={
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
    credential_service: CredentialServiceDep,
    subject: SubjectDep,
) -> StreamingResponse:
    """发送用户消息并流式接收真实 Qwen 回答（SSE）。

    事件序列：``started``（消息已落库）→ 若干 ``delta`` → ``done``；
    失败时 ``delta`` 后以 ``error`` 结束，保留已接收正文。
    """
    _ensure_chat_capability_ready(subject, credential_service)
    try:
        user_message, assistant_message = service.start_generation(
            subject.account_id, conversation_id, body.content, body.attachment_ids
        )
    except ChatDomainError as exc:
        raise _handle_domain_error(exc) from exc

    run_context = _chat_run_context(
        subject.account_id, conversation_id, assistant_message.message_id
    )

    return _stream_response(
        _generation_events(
            service,
            subject,
            conversation_id,
            user_message,
            assistant_message,
            run_context,
        )
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
    responses={
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
    credential_service: CredentialServiceDep,
    subject: SubjectDep,
) -> StreamingResponse:
    """重试失败的助手消息：创建新的助手尝试并流式生成。

    新尝试保留审计关系（尝试号递增），历史失败尝试原样保留。
    """
    _ensure_chat_capability_ready(subject, credential_service)
    try:
        user_message, assistant_message = service.retry_generation(
            subject.account_id, conversation_id, message_id
        )
    except ChatDomainError as exc:
        raise _handle_domain_error(exc) from exc

    run_context = _chat_run_context(
        subject.account_id, conversation_id, assistant_message.message_id
    )

    return _stream_response(
        _generation_events(
            service,
            subject,
            conversation_id,
            user_message,
            assistant_message,
            run_context,
        )
    )
