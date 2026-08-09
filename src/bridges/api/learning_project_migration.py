"""学习项目迁移兼容窗口的状态与恢复 API。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from bridges.api.auth import SubjectDep
from bridges.contracts.learning_project_migration import (
    LearningProjectMigrationRetryRequest,
    LearningProjectMigrationSummary,
)
from bridges.learning_projects.migration import (
    ProjectMigrationError,
    ProjectMigrationService,
)

router = APIRouter(prefix="/learning-projects", tags=["learning-project-migration"])


def _get_migration_service(request: Request) -> ProjectMigrationService:
    service: ProjectMigrationService | None = getattr(
        request.app.state, "learning_project_migration_service", None
    )
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "learning_project_migration_unavailable",
                "message": "学习项目迁移服务尚未启用。",
            },
        )
    return service


MigrationServiceDep = Annotated[
    ProjectMigrationService, Depends(_get_migration_service)
]


def _error(exc: ProjectMigrationError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"error": exc.code, "message": exc.message},
    )


@router.post(
    "/migration",
    response_model=LearningProjectMigrationSummary,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": dict},
        status.HTTP_409_CONFLICT: {"model": dict},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": dict},
    },
)
def start_migration(
    service: MigrationServiceDep, subject: SubjectDep
) -> LearningProjectMigrationSummary:
    """启动当前账户的可恢复迁移；重复调用只返回已有运行。"""
    try:
        return service.start(subject.account_id)
    except ProjectMigrationError as exc:
        raise _error(exc) from exc


@router.get(
    "/migration",
    response_model=LearningProjectMigrationSummary,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": dict},
        status.HTTP_404_NOT_FOUND: {"model": dict},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": dict},
    },
)
def get_migration(
    service: MigrationServiceDep, subject: SubjectDep
) -> LearningProjectMigrationSummary:
    """读取当前账户的机器可读迁移报告。"""
    summary = service.get_status(subject.account_id)
    if summary is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "migration_not_started",
                "message": "当前账户尚未启动学习项目迁移。",
            },
        )
    return summary


@router.post(
    "/migration/retry",
    response_model=LearningProjectMigrationSummary,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": dict},
        status.HTTP_404_NOT_FOUND: {"model": dict},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": dict},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": dict},
    },
)
def retry_migration(
    body: LearningProjectMigrationRetryRequest,
    service: MigrationServiceDep,
    subject: SubjectDep,
) -> LearningProjectMigrationSummary:
    """重新排入失败项；不会复活已删除的源或目标。"""
    try:
        return service.retry(subject.account_id, body.source_document_id)
    except ProjectMigrationError as exc:
        raise _error(exc) from exc


__all__ = ["router"]
