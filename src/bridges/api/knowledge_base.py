"""全局本地知识库 API 路由（Issue 18）。

材料上传、列表、详情、下载、失败重试、显式重建与级联删除都绑定当前
账户：跨账户或未知材料返回同一 404 ``material_not_found``，不泄漏
资源是否存在。任何响应都不包含对象库路径、原文内容或凭据。
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import quote, unquote

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from bridges.api.auth import SubjectDep
from bridges.chat.attachments import MAX_ATTACHMENT_BYTES
from bridges.contracts.knowledge_base import KnowledgeBaseMaterialProjection
from bridges.knowledge_base import KnowledgeBaseError, KnowledgeBaseService

router = APIRouter(prefix="/knowledge-base", tags=["knowledge-base"])


def _get_knowledge_base_service(request: Request) -> KnowledgeBaseService:
    service: KnowledgeBaseService | None = getattr(
        request.app.state, "knowledge_base_service", None
    )
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "knowledge_base_unavailable",
                "message": "知识库服务未启用，当前实例拒绝知识库操作。",
            },
        )
    return service


KnowledgeBaseServiceDep = Annotated[KnowledgeBaseService, Depends(_get_knowledge_base_service)]


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"error": code, "message": message},
    )


@router.post(
    "/materials",
    responses={
        status.HTTP_201_CREATED: {"model": KnowledgeBaseMaterialProjection},
        status.HTTP_400_BAD_REQUEST: {"model": dict},
        status.HTTP_401_UNAUTHORIZED: {"model": dict},
        status.HTTP_413_CONTENT_TOO_LARGE: {"model": dict},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": dict},
    },
)
async def upload_material(
    request: Request,
    service: KnowledgeBaseServiceDep,
    subject: SubjectDep,
) -> Response:
    """接收原始文件字节；类型、扩展名、大小与文件名均由服务端校验。

    上传成功即入队摄取（不绑定对话的全局材料）：解析、分块与索引由
    后台执行器完成。接受 ``X-Bridges-Upload-Id`` 头与聊天附件保持同一
    上传约定；知识库不持久化上传标识，幂等由同名同内容复用保证——
    客户端安全重试同一上传只会得到已有材料的 200 投影，绝不重复摄取。
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
        raise _error(
            status.HTTP_400_BAD_REQUEST, "invalid_filename", "缺少文件名，无法上传。"
        )
    filename = unquote(filename_header)
    try:
        projection, created = service.upload(
            subject.account_id, filename, b"".join(chunks)
        )
    except KnowledgeBaseError as exc:
        raise _error(exc.status_code, exc.code, exc.message) from exc
    return JSONResponse(
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        content=projection.model_dump(mode="json"),
    )


@router.get(
    "/materials",
    response_model=list[KnowledgeBaseMaterialProjection],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": dict},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": dict},
    },
)
def list_materials(
    service: KnowledgeBaseServiceDep,
    subject: SubjectDep,
) -> list[KnowledgeBaseMaterialProjection]:
    """列出当前账户的全部知识库材料（最新上传在前）。"""
    return service.list_materials(subject.account_id)


@router.get(
    "/materials/{object_id}",
    response_model=KnowledgeBaseMaterialProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": dict},
        status.HTTP_404_NOT_FOUND: {"model": dict},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": dict},
    },
)
def get_material(
    object_id: str,
    service: KnowledgeBaseServiceDep,
    subject: SubjectDep,
) -> KnowledgeBaseMaterialProjection:
    """返回材料详情（摄取状态、失败阶段、索引版本与向量可用性）。"""
    try:
        return service.get_material(subject.account_id, object_id)
    except KnowledgeBaseError as exc:
        raise _error(exc.status_code, exc.code, exc.message) from exc


@router.get(
    "/materials/{object_id}/download",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": dict},
        status.HTTP_404_NOT_FOUND: {"model": dict},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": dict},
    },
)
def download_material(
    object_id: str,
    service: KnowledgeBaseServiceDep,
    subject: SubjectDep,
) -> Response:
    """通过账户授权下载材料原文，不暴露对象库路径。"""
    try:
        material, content = service.download(subject.account_id, object_id)
    except KnowledgeBaseError as exc:
        raise _error(exc.status_code, exc.code, exc.message) from exc
    safe_filename = quote(material.filename, safe="")
    return Response(
        content=content,
        media_type=material.media_type,
        headers={
            "Content-Disposition": (
                f'attachment; filename="material"; filename*=UTF-8\'\'{safe_filename}'
            ),
            "Cache-Control": "no-store",
        },
    )


@router.post(
    "/materials/{object_id}/retry",
    response_model=KnowledgeBaseMaterialProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": dict},
        status.HTTP_404_NOT_FOUND: {"model": dict},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": dict},
    },
)
def retry_material(
    object_id: str,
    service: KnowledgeBaseServiceDep,
    subject: SubjectDep,
) -> KnowledgeBaseMaterialProjection:
    """把失败材料重新入队（重置自动重试计数）；非失败状态幂等返回。"""
    try:
        return service.retry(subject.account_id, object_id)
    except KnowledgeBaseError as exc:
        raise _error(exc.status_code, exc.code, exc.message) from exc


@router.post(
    "/materials/{object_id}/rebuild",
    response_model=KnowledgeBaseMaterialProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": dict},
        status.HTTP_404_NOT_FOUND: {"model": dict},
        status.HTTP_409_CONFLICT: {"model": dict},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": dict},
    },
)
def rebuild_material(
    object_id: str,
    service: KnowledgeBaseServiceDep,
    subject: SubjectDep,
) -> KnowledgeBaseMaterialProjection:
    """显式触发版本化重建；材料正在处理中时返回 409，可稍后重试。"""
    try:
        return service.rebuild(subject.account_id, object_id)
    except KnowledgeBaseError as exc:
        raise _error(exc.status_code, exc.code, exc.message) from exc


@router.delete(
    "/materials/{object_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": dict},
        status.HTTP_404_NOT_FOUND: {"model": dict},
        status.HTTP_409_CONFLICT: {"model": dict},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": dict},
    },
)
def delete_material(
    object_id: str,
    service: KnowledgeBaseServiceDep,
    subject: SubjectDep,
) -> Response:
    """级联删除材料与派生索引数据；被后台任务使用时返回 409。"""
    try:
        service.delete(subject.account_id, object_id)
    except KnowledgeBaseError as exc:
        raise _error(exc.status_code, exc.code, exc.message) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
