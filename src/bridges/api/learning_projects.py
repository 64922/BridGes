"""文件夹式学习项目 API 路由（Issue 19）。

项目的创建/列表/详情/更新/删除与项目文件的上传/列表/下载/删除都绑定
当前账户：跨账户或未知资源返回同一 404，不泄漏资源是否存在。任何响应
都不包含对象库路径、原文内容或凭据。
"""

from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import quote, unquote

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from bridges.api.auth import SubjectDep
from bridges.chat.attachments import MAX_ATTACHMENT_BYTES
from bridges.contracts.learning_projects import (
    LearningProjectCreateRequest,
    LearningProjectDetail,
    LearningProjectFile,
    LearningProjectFileListProjection,
    LearningProjectListProjection,
    LearningProjectSummary,
    LearningProjectUpdateRequest,
)
from bridges.learning_projects import LearningProjectError, LearningProjectService
from bridges.learning_projects.service import UNSET

router = APIRouter(prefix="/learning-projects", tags=["learning-projects"])


def _get_learning_project_service(request: Request) -> LearningProjectService:
    service: LearningProjectService | None = getattr(
        request.app.state, "learning_project_service", None
    )
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "learning_projects_unavailable",
                "message": "学习项目服务未启用，当前实例拒绝学习项目操作。",
            },
        )
    return service


LearningProjectServiceDep = Annotated[
    LearningProjectService, Depends(_get_learning_project_service)
]


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"error": code, "message": message},
    )


@router.post(
    "",
    response_model=LearningProjectSummary,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": dict},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": dict},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": dict},
    },
)
def create_project(
    body: LearningProjectCreateRequest,
    service: LearningProjectServiceDep,
    subject: SubjectDep,
) -> LearningProjectSummary:
    """新建学习项目；名称必填，描述可选。"""
    try:
        return service.create_project(subject.account_id, body.name, body.description)
    except LearningProjectError as exc:
        raise _error(exc.status_code, exc.code, exc.message) from exc


@router.get(
    "",
    response_model=LearningProjectListProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": dict},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": dict},
    },
)
def list_projects(
    service: LearningProjectServiceDep,
    subject: SubjectDep,
) -> LearningProjectListProjection:
    """列出当前账户的全部学习项目（最近更新在前）。"""
    return LearningProjectListProjection(
        projects=service.list_projects(subject.account_id)
    )


@router.get(
    "/{project_id}",
    response_model=LearningProjectDetail,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": dict},
        status.HTTP_404_NOT_FOUND: {"model": dict},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": dict},
    },
)
def get_project(
    project_id: str,
    service: LearningProjectServiceDep,
    subject: SubjectDep,
) -> LearningProjectDetail:
    """返回项目详情（含归属对话列表）；跨账户访问安全返回 404。"""
    try:
        return service.get_detail(subject.account_id, project_id)
    except LearningProjectError as exc:
        raise _error(exc.status_code, exc.code, exc.message) from exc


@router.patch(
    "/{project_id}",
    response_model=LearningProjectSummary,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": dict},
        status.HTTP_404_NOT_FOUND: {"model": dict},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": dict},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": dict},
    },
)
def update_project(
    project_id: str,
    body: LearningProjectUpdateRequest,
    service: LearningProjectServiceDep,
    subject: SubjectDep,
) -> LearningProjectSummary:
    """更新项目名称或描述；字段缺省保持不变，显式 null 清空描述（名称显式 null 为 422）。"""
    try:
        return service.update_project(
            subject.account_id,
            project_id,
            name=body.name if body.name is not None else UNSET,
            description=(
                body.description if "description" in body.model_fields_set else UNSET
            ),
        )
    except LearningProjectError as exc:
        raise _error(exc.status_code, exc.code, exc.message) from exc


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": dict},
        status.HTTP_404_NOT_FOUND: {"model": dict},
        status.HTTP_409_CONFLICT: {"model": dict},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": dict},
    },
)
def delete_project(
    project_id: str,
    service: LearningProjectServiceDep,
    subject: SubjectDep,
    contents: Literal["keep", "delete"] = "keep",
) -> Response:
    """删除项目；``keep`` 保留对话（解除归属），``delete`` 连同对话删除。"""
    try:
        service.delete_project(subject.account_id, project_id, contents=contents)
    except LearningProjectError as exc:
        raise _error(exc.status_code, exc.code, exc.message) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{project_id}/files",
    responses={
        status.HTTP_201_CREATED: {"model": LearningProjectFile},
        status.HTTP_400_BAD_REQUEST: {"model": dict},
        status.HTTP_401_UNAUTHORIZED: {"model": dict},
        status.HTTP_404_NOT_FOUND: {"model": dict},
        status.HTTP_413_CONTENT_TOO_LARGE: {"model": dict},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": dict},
    },
)
async def upload_file(
    project_id: str,
    request: Request,
    service: LearningProjectServiceDep,
    subject: SubjectDep,
) -> Response:
    """接收原始文件字节；类型、扩展名、大小与文件名均由服务端校验。

    上传成功即入队摄取（source=project_file）：解析、分块与索引由后台
    执行器完成。同项目同名同内容的重复上传幂等复用——客户端安全重试
    同一上传只会得到已有文件的 200 投影，绝不重复摄取。
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
        projection, created = service.upload_file(
            subject.account_id, project_id, b"".join(chunks), filename
        )
    except LearningProjectError as exc:
        raise _error(exc.status_code, exc.code, exc.message) from exc
    return JSONResponse(
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        content=projection.model_dump(mode="json"),
    )


@router.get(
    "/{project_id}/files",
    response_model=LearningProjectFileListProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": dict},
        status.HTTP_404_NOT_FOUND: {"model": dict},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": dict},
    },
)
def list_files(
    project_id: str,
    service: LearningProjectServiceDep,
    subject: SubjectDep,
) -> LearningProjectFileListProjection:
    """列出项目的全部文件（最新上传在前）。"""
    try:
        return LearningProjectFileListProjection(
            files=service.list_files(subject.account_id, project_id)
        )
    except LearningProjectError as exc:
        raise _error(exc.status_code, exc.code, exc.message) from exc


@router.get(
    "/{project_id}/files/{object_id}/download",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": dict},
        status.HTTP_404_NOT_FOUND: {"model": dict},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": dict},
    },
)
def download_file(
    project_id: str,
    object_id: str,
    service: LearningProjectServiceDep,
    subject: SubjectDep,
) -> Response:
    """通过账户与项目授权下载文件原文，不暴露对象库路径。"""
    try:
        file, content = service.download_file(subject.account_id, project_id, object_id)
    except LearningProjectError as exc:
        raise _error(exc.status_code, exc.code, exc.message) from exc
    safe_filename = quote(file.filename, safe="")
    return Response(
        content=content,
        media_type=file.media_type,
        headers={
            "Content-Disposition": (
                f'attachment; filename="file"; filename*=UTF-8\'\'{safe_filename}'
            ),
            "Cache-Control": "no-store",
        },
    )


@router.delete(
    "/{project_id}/files/{object_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": dict},
        status.HTTP_404_NOT_FOUND: {"model": dict},
        status.HTTP_409_CONFLICT: {"model": dict},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": dict},
    },
)
def delete_file(
    project_id: str,
    object_id: str,
    service: LearningProjectServiceDep,
    subject: SubjectDep,
) -> Response:
    """级联删除项目文件与派生索引数据；被后台任务使用时返回 409。"""
    try:
        service.delete_file(subject.account_id, project_id, object_id)
    except LearningProjectError as exc:
        raise _error(exc.status_code, exc.code, exc.message) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
