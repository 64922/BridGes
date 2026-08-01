"""Project API routes and dependencies.

Routes implement project creation, listing, selection, renaming, and archival.
Every route resolves the authenticated subject and enforces account-level
ownership so that deep links re-authenticate before returning a projection.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from bridges.api.auth import SubjectDep
from bridges.contracts.projects import (
    Project,
    ProjectCreateRequest,
    ProjectError,
    ProjectListProjection,
    ProjectUpdateRequest,
)
from bridges.projects import ProjectError as ProjectServiceError
from bridges.projects import ProjectService

router = APIRouter(prefix="/projects", tags=["projects"])


def _get_project_service(request: Request) -> ProjectService:
    service: ProjectService | None = getattr(request.app.state, "project_service", None)
    if service is None:
        raise RuntimeError("ProjectService not attached to application state.")
    return service


ProjectServiceDep = Annotated[ProjectService, Depends(_get_project_service)]


def _project_error(status_code: int, error: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=ProjectError(error=error, message=message).model_dump(),
    )


@router.post(
    "",
    response_model=Project,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": ProjectError},
        status.HTTP_401_UNAUTHORIZED: {"model": ProjectError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ProjectError},
    },
)
async def create_project(
    service: ProjectServiceDep,
    subject: SubjectDep,
    request: ProjectCreateRequest,
) -> Project:
    """Create a new scientific project space owned by the current account."""
    return service.create_project(account_id=subject.account_id, request=request)


@router.get(
    "",
    response_model=ProjectListProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProjectError},
    },
)
async def list_projects(
    service: ProjectServiceDep,
    subject: SubjectDep,
) -> ProjectListProjection:
    """List active and archived projects for the current account."""
    return service.list_projects(account_id=subject.account_id)


@router.get(
    "/{project_id}",
    response_model=Project,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProjectError},
        status.HTTP_404_NOT_FOUND: {"model": ProjectError},
    },
)
async def get_project(
    service: ProjectServiceDep,
    subject: SubjectDep,
    project_id: str,
) -> Project:
    """Get a single project projection by deep-link identifier."""
    try:
        return service.get_project(account_id=subject.account_id, project_id=project_id)
    except ProjectServiceError as exc:
        raise _project_error(
            status.HTTP_404_NOT_FOUND,
            "project_not_found",
            str(exc),
        ) from exc


@router.patch(
    "/{project_id}",
    response_model=Project,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProjectError},
        status.HTTP_404_NOT_FOUND: {"model": ProjectError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ProjectError},
    },
)
async def update_project(
    service: ProjectServiceDep,
    subject: SubjectDep,
    project_id: str,
    request: ProjectUpdateRequest,
) -> Project:
    """Rename or update a project description."""
    try:
        return service.update_project(
            account_id=subject.account_id,
            project_id=project_id,
            request=request,
        )
    except ProjectServiceError as exc:
        raise _project_error(
            status.HTTP_404_NOT_FOUND,
            "project_not_found",
            str(exc),
        ) from exc


@router.post(
    "/{project_id}/archive",
    response_model=Project,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProjectError},
        status.HTTP_404_NOT_FOUND: {"model": ProjectError},
    },
)
async def archive_project(
    service: ProjectServiceDep,
    subject: SubjectDep,
    project_id: str,
) -> Project:
    """Archive a project."""
    try:
        return service.archive_project(account_id=subject.account_id, project_id=project_id)
    except ProjectServiceError as exc:
        raise _project_error(
            status.HTTP_404_NOT_FOUND,
            "project_not_found",
            str(exc),
        ) from exc
