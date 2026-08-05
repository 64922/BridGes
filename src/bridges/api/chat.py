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
from typing import Annotated
from urllib.parse import quote, unquote

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from bridges.api.auth import SubjectDep
from bridges.arxiv_mcp.contracts import ArxivSearchProjection
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
    ChatStreamDeltaData,
    ChatStreamDoneData,
    ChatStreamErrorData,
    ChatStreamErrorDetail,
    ChatStreamEvent,
    ChatStreamEventKind,
    ChatStreamProfileData,
    ChatStreamStartedData,
    ChatThinkingSummary,
)
from bridges.contracts.credentials import ProbeStatus
from bridges.contracts.feedback import (
    AnswerFeedback,
    AnswerFeedbackRequest,
    FeedbackResolveRequest,
)
from bridges.contracts.projects import ObjectDomain
from bridges.contracts.retrieval import CitationDetailProjection
from bridges.contracts.teaching import TeachingTurnProjection
from bridges.contracts.workflows import RunContextEnvelope
from bridges.credentials.service import KeyCredentialService
from bridges.ingestion.service import IngestionError, IngestionService
from bridges.learning_projects import LearningProjectError, LearningProjectService
from bridges.retrieval.service import LayeredRetrievalService, RetrievalError
from bridges.web_search.contracts import WebSearchProjection

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


def _sse(event: ChatStreamEventKind, data: BaseModel) -> str:
    payload = json.dumps(data.model_dump(mode="json"), ensure_ascii=False)
    return f"event: {event.value}\ndata: {payload}\n\n"


def _stream_response(
    events: Iterator[ChatStreamEvent],
) -> StreamingResponse:
    def generate() -> Iterator[str]:
        for stream_event in events:
            yield _sse(stream_event.event, stream_event.data)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "X-Accel-Buffering": "no",
        },
    )


def _stream_error_event(
    message_id: str,
    *,
    code: str,
    message: str,
    retryable: bool,
    thinking: ChatThinkingSummary | None,
    duration_ms: int | None,
    web_search: WebSearchProjection | None = None,
    arxiv_search: ArxivSearchProjection | None = None,
    teaching: TeachingTurnProjection | None = None,
) -> ChatStreamEvent:
    """构造 error 终态事件（成功/停止/失败共用的补发路径）。"""
    return ChatStreamEvent(
        event=ChatStreamEventKind.ERROR,
        data=ChatStreamErrorData(
            message_id=message_id,
            error=ChatStreamErrorDetail(
                code=code, message=message, retryable=retryable
            ),
            thinking=thinking,
            duration_ms=duration_ms,
            web_search=web_search,
            arxiv_search=arxiv_search,
            teaching=teaching,
        ),
    )


def _generation_events(
    service: ChatService,
    subject: SubjectDep,
    conversation_id: str,
    user_message: ChatMessageProjection,
    assistant_message: ChatMessageProjection,
    run_context: RunContextEnvelope,
    use_knowledge_base: bool = True,
    use_profile: bool = True,
) -> Iterator[ChatStreamEvent]:
    """发送/重试共用的 SSE 事件序列：started → delta* → done | error。

    若消息在生成器启动前已被并发收敛（停止/断流/TTL 收敛抢先），生成器
    不会产出任何事件——此时读取消息当前状态补发诚实的终态事件，保证
    started 之后必有 done/error，绝不悬挂。所有事件载荷由契约模型
    （ChatStreamEvent）构造，是前端类型生成的唯一来源。``use_knowledge_base``
    控制本轮分层检索是否包含全局知识库层（Issue 20）。
    """
    yield ChatStreamEvent(
        event=ChatStreamEventKind.STARTED,
        data=ChatStreamStartedData(
            conversation_id=conversation_id,
            user_message_id=user_message.message_id,
            message_id=assistant_message.message_id,
            attempt_number=assistant_message.attempt_number,
            # 初始思考摘要：前端据此自动展开思考区域（不暴露原始思维链）
            thinking=assistant_message.thinking,
            web_search=assistant_message.web_search,
            arxiv_search=assistant_message.arxiv_search,
            teaching=assistant_message.teaching,
        ),
    )
    # Issue 26：画像通知（明确记忆/自动写入/候选/单次情绪）在 started 后
    # 立即下发，聊天内即时展示来源与一键撤回；通知按账户隔离持久化。
    profile_notifications = service.profile_notifications_for_message(
        subject.account_id, conversation_id, user_message.message_id
    )
    if profile_notifications:
        yield ChatStreamEvent(
            event=ChatStreamEventKind.PROFILE,
            data=ChatStreamProfileData(
                message_id=user_message.message_id,
                notifications=profile_notifications,
            ),
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
        use_knowledge_base=use_knowledge_base,
        use_profile=use_profile,
    ):
        if event.kind == "delta":
            yield ChatStreamEvent(
                event=ChatStreamEventKind.DELTA,
                data=ChatStreamDeltaData(
                    message_id=assistant_message.message_id,
                    delta=event.delta,
                ),
            )
        elif event.kind == "error":
            terminated = True
            final = service.message_projection(
                subject.account_id, assistant_message.message_id
            )
            yield _stream_error_event(
                assistant_message.message_id,
                code=event.error_code or "generation_failed",
                message=user_facing_error(event.error_code, event.error_message),
                retryable=error_is_retryable(event.error_code),
                # 失败/停止/断流时保留已完成思考摘要与真实耗时
                thinking=final.thinking if final is not None else None,
                duration_ms=final.duration_ms if final is not None else None,
                web_search=final.web_search if final is not None else None,
                arxiv_search=final.arxiv_search if final is not None else None,
                teaching=final.teaching if final is not None else None,
            )
            return
        elif event.kind == "done":
            terminated = True
            final = service.message_projection(
                subject.account_id, assistant_message.message_id
            )
            yield ChatStreamEvent(
                event=ChatStreamEventKind.DONE,
                data=ChatStreamDoneData(
                    message_id=assistant_message.message_id,
                    message=final,
                ),
            )
            return
        elif event.kind == "humanizer":
            # Issue 28：人味化过程卡五态事件（loading/empty/error/permission/
            # recovery），与 delta 同一事件流；终态由 done/error 携带。
            data = event.humanizer
            if data is not None:
                yield ChatStreamEvent(
                    event=ChatStreamEventKind.HUMANIZER,
                    data=data,
                )
    if terminated:
        return
    # 生成器空产出：以消息当前状态补发终态
    final = service.message_projection(subject.account_id, assistant_message.message_id)
    if final is None:
        return
    if final.status.value == "done":
        yield ChatStreamEvent(
            event=ChatStreamEventKind.DONE,
            data=ChatStreamDoneData(
                message_id=assistant_message.message_id,
                message=final,
            ),
        )
        return
    if final.status.value == "stopped":
        yield _stream_error_event(
            assistant_message.message_id,
            code="stopped",
            message="生成已停止。",
            retryable=True,
            # 与 error 事件载荷一致：补发终态也携带思考摘要与真实耗时，
            # 前端停止后的 error 态渲染不依赖重新加载的间隙。
            thinking=final.thinking,
            duration_ms=final.duration_ms,
            web_search=final.web_search,
            arxiv_search=final.arxiv_search,
            teaching=final.teaching,
        )
        return
    yield _stream_error_event(
        assistant_message.message_id,
        code=final.error_code or "generation_failed",
        message=final.error_message or "生成失败，请稍后重试。",
        retryable=error_is_retryable(final.error_code),
        thinking=final.thinking,
        duration_ms=final.duration_ms,
        web_search=final.web_search,
        arxiv_search=final.arxiv_search,
        teaching=final.teaching,
    )


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
) -> ChatConversationProjection:
    """新建对话；标题可选，缺省由首条消息自动推导。

    ``mode`` 缺省为日常陪伴；学习项目新建学习对话时传 ``study``。
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
    learning_project_service: LearningProjectServiceDep,
) -> ChatConversationProjection:
    """更新当前账户会话的标题、置顶状态或学习项目归属。

    ``project_id`` 字段缺省表示归属不变；显式 null 解除归属。移动只改
    归属：消息、模式事件与附件绝不被触碰。携带 ``project_id`` 的 PATCH
    与标题/置顶在同一事务内提交：任一失败整体回滚，绝不留下半更新状态。
    """
    try:
        if "project_id" in body.model_fields_set:
            record = learning_project_service.update_conversation_metadata(
                subject.account_id,
                conversation_id,
                title=body.title,
                pinned=body.pinned,
                project_id=body.project_id,
            )
            return service.projection_from_record(record)
        return service.update_conversation(
            subject.account_id,
            conversation_id,
            title=body.title,
            pinned=body.pinned,
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
    # 显式报错，前端可重试，绝不静默吞掉摄取记录缺失。
    try:
        ingestion_service.enqueue(
            subject.account_id, projection.object_id, conversation_id
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
    responses={
        status.HTTP_200_OK: {
            "model": ChatStreamEvent,
            "content": {
                "text/event-stream": {
                    "schema": {"$ref": "#/components/schemas/ChatStreamEvent"}
                }
            },
            "description": "SSE 事件流：started → delta* → done | error（载荷由契约模型定义）",
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
    credential_service: CredentialServiceDep,
    subject: SubjectDep,
) -> StreamingResponse:
    """发送用户消息并流式接收真实 Qwen 回答（SSE）。

    事件序列：``started``（消息已落库）→ 若干 ``delta`` → ``done``；
    失败时 ``delta`` 后以 ``error`` 结束，保留已接收正文。发送前可关闭
    本轮全局知识库层（``use_knowledge_base=false``）：关闭后本轮检索
    记录与引用均不包含知识库候选；也可关闭本轮画像使用
    （``use_profile=false``，Issue 27）：关闭后模型请求、审计与上下文
    说明均不含任何画像切片。
    """
    _ensure_chat_capability_ready(subject, credential_service)
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
            use_knowledge_base=body.use_knowledge_base,
            use_profile=body.use_profile,
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
        status.HTTP_200_OK: {
            "model": ChatStreamEvent,
            "content": {
                "text/event-stream": {
                    "schema": {"$ref": "#/components/schemas/ChatStreamEvent"}
                }
            },
            "description": "SSE 事件流：started → delta* → done | error（载荷由契约模型定义）",
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
    credential_service: CredentialServiceDep,
    subject: SubjectDep,
    retrieval_service: RetrievalServiceDep,
) -> StreamingResponse:
    """重试失败的助手消息：创建新的助手尝试并流式生成。

    新尝试保留审计关系（尝试号递增），历史失败尝试原样保留。检索作用域
    沿用被重试尝试轮次的设置（含知识库开关），不重复用户消息。
    """
    _ensure_chat_capability_ready(subject, credential_service)
    try:
        user_message, assistant_message = service.retry_generation(
            subject.account_id, conversation_id, message_id
        )
    except ChatDomainError as exc:
        raise _handle_domain_error(exc) from exc

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
            use_knowledge_base=use_knowledge_base,
            use_profile=use_profile,
        )
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
