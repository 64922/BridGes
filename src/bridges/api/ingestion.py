"""文档摄取与索引状态 API 路由（Issue 17）。

附件详情、失败重试与索引版本状态都绑定当前账户：跨账户或未知对象
返回同一 404 ``attachment_not_found``，不泄漏资源是否存在。任何响应
都不包含对象库路径、原文内容或凭据。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from bridges.api.auth import SubjectDep
from bridges.chat.attachments import ChatAttachmentError, ChatAttachmentService
from bridges.contracts.ingestion import (
    DocumentIngestionProjection,
    DocumentIngestionStatus,
    IndexStatusProjection,
)
from bridges.ingestion.service import IngestionService, parser_version_for
from bridges.retirement import raise_retired_file_source

router = APIRouter(prefix="/chat", tags=["ingestion"])


def _get_ingestion_service(request: Request) -> IngestionService:
    service: IngestionService | None = getattr(
        request.app.state, "ingestion_service", None
    )
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "ingestion_unavailable",
                "message": "文档摄取服务未启用，当前实例拒绝摄取操作。",
            },
        )
    return service


IngestionServiceDep = Annotated[IngestionService, Depends(_get_ingestion_service)]


def _get_attachment_service(request: Request) -> ChatAttachmentService:
    service: ChatAttachmentService | None = getattr(
        request.app.state, "chat_attachment_service", None
    )
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "attachments_unavailable",
                "message": "附件服务未启用，当前实例拒绝附件读写。",
            },
        )
    return service


AttachmentServiceDep = Annotated[ChatAttachmentService, Depends(_get_attachment_service)]


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"error": code, "message": message},
    )


def _require_attachment(
    service: ChatAttachmentService,
    account_id: str,
    conversation_id: str,
    object_id: str,
) -> None:
    """附件授权检查：跨账户或不存在返回同一 404。"""
    try:
        attachment = service.get(account_id, conversation_id, object_id)
    except ChatAttachmentError as exc:
        raise _error(exc.status_code, exc.code, exc.message) from exc
    if attachment is None:
        raise _error(
            status.HTTP_404_NOT_FOUND,
            "attachment_not_found",
            "附件不存在或没有访问权限。",
        )


@router.get(
    "/conversations/{conversation_id}/attachments/{object_id}/ingestion",
    response_model=DocumentIngestionProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": dict},
        status.HTTP_404_NOT_FOUND: {"model": dict},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": dict},
    },
)
def get_attachment_ingestion(
    conversation_id: str,
    object_id: str,
    service: IngestionServiceDep,
    attachment_service: AttachmentServiceDep,
    subject: SubjectDep,
) -> DocumentIngestionProjection:
    """返回附件摄取的完整详情（状态、失败阶段、页码/章节、向量可用性）。

    无摄取记录时返回 ``none`` 状态投影（该类型不支持索引或本 Issue
    之前上传的对象），不以空响应掩盖失败。
    """
    _require_attachment(attachment_service, subject.account_id, conversation_id, object_id)
    projection = service.projection(subject.account_id, object_id)
    if projection is None:
        attachment = attachment_service.get(
            subject.account_id, conversation_id, object_id
        )
        assert attachment is not None
        return DocumentIngestionProjection(
            document_id=f"doc-{object_id}",
            object_id=object_id,
            conversation_id=conversation_id,
            status=DocumentIngestionStatus.NONE,
            parser_version=parser_version_for(attachment.media_type),
            content_hash=attachment.content_hash,
            retry_count=0,
            created_at=attachment.created_at,
            updated_at=attachment.updated_at,
            vector_unavailable_reason=service.index_status(
                subject.account_id
            ).vector_unavailable_reason,
        )
    return projection


@router.post(
    "/conversations/{conversation_id}/attachments/{object_id}/ingestion/retry",
    response_model=None,
    status_code=status.HTTP_410_GONE,
    responses={
        status.HTTP_410_GONE: {"model": dict},
        status.HTTP_401_UNAUTHORIZED: {"model": dict},
        status.HTTP_404_NOT_FOUND: {"model": dict},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": dict},
    },
)
def retry_attachment_ingestion(
    conversation_id: str,
    object_id: str,
    request: Request,
    subject: SubjectDep,
) -> None:
    """聊天附件摄取重试已退役；知识库材料由知识库流程重建派生索引。"""
    del conversation_id, object_id, subject
    raise_retired_file_source(request, endpoint="legacy.chat.attachments.ingestion_retry")


@router.get(
    "/ingestion/index",
    response_model=IndexStatusProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": dict},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": dict},
    },
)
def get_index_status(
    service: IngestionServiceDep,
    subject: SubjectDep,
) -> IndexStatusProjection:
    """返回当前账户的索引状态（向量可用性、活跃版本与可回滚版本链）。"""
    return service.index_status(subject.account_id)
