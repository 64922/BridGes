"""文件夹式学习项目 API 路由（Issue 19）。

项目的创建/列表/详情/更新/删除与项目文件的上传/列表/下载/删除都绑定
当前账户：跨账户或未知资源返回同一 404，不泄漏资源是否存在。任何响应
都不包含对象库路径、原文内容或凭据。
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from bridges.api.auth import SubjectDep
from bridges.contracts.learning_projects import (
    LearningProjectDetail,
    LearningProjectFileListProjection,
    LearningProjectListProjection,
)
from bridges.learning_projects import LearningProjectError, LearningProjectService
from bridges.retirement import raise_retired_file_source
from bridges.contracts.retirement import RetiredCapabilityError

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
    response_model=None,
    status_code=status.HTTP_410_GONE,
    responses={
        status.HTTP_410_GONE: {"model": RetiredCapabilityError},
        status.HTTP_401_UNAUTHORIZED: {"model": dict},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": dict},
    },
)
def create_project(
    request: Request,
    subject: SubjectDep,
) -> None:
    """学习项目创建已退役；历史项目仍由只读投影读取。"""
    del subject
    raise_retired_file_source(request, endpoint="legacy.learning_projects.create")


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
    response_model=None,
    status_code=status.HTTP_410_GONE,
    responses={
        status.HTTP_410_GONE: {"model": RetiredCapabilityError},
        status.HTTP_401_UNAUTHORIZED: {"model": dict},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": dict},
    },
)
def update_project(
    project_id: str,
    request: Request,
    subject: SubjectDep,
) -> None:
    """学习项目更新已退役，避免触碰历史对象。"""
    del project_id, subject
    raise_retired_file_source(request, endpoint="legacy.learning_projects.update")


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_410_GONE,
    responses={
        status.HTTP_410_GONE: {"model": RetiredCapabilityError},
        status.HTTP_401_UNAUTHORIZED: {"model": dict},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": dict},
    },
)
def delete_project(
    project_id: str,
    request: Request,
    subject: SubjectDep,
) -> None:
    """学习项目删除已退役，物理清理必须走独立审计。"""
    del project_id, subject
    raise_retired_file_source(request, endpoint="legacy.learning_projects.delete")


@router.post(
    "/{project_id}/files",
    response_model=None,
    status_code=status.HTTP_410_GONE,
    responses={
        status.HTTP_410_GONE: {"model": RetiredCapabilityError},
        status.HTTP_401_UNAUTHORIZED: {"model": dict},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": dict},
    },
)
async def upload_file(
    project_id: str,
    request: Request,
    subject: SubjectDep,
) -> None:
    """项目文件上传已退役；不读取请求体，也不访问对象库。"""
    del project_id, subject
    raise_retired_file_source(request, endpoint="legacy.learning_project_files.upload")


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
    status_code=status.HTTP_410_GONE,
    responses={
        status.HTTP_410_GONE: {"model": RetiredCapabilityError},
        status.HTTP_401_UNAUTHORIZED: {"model": dict},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": dict},
    },
)
def delete_file(
    project_id: str,
    object_id: str,
    request: Request,
    subject: SubjectDep,
) -> None:
    """项目文件删除已退役，物理清理必须单独审计。"""
    del project_id, object_id, subject
    raise_retired_file_source(request, endpoint="legacy.learning_project_files.delete")
