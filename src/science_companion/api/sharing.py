"""Sharing API routes for explicit shared projects and minimized copies.

Routes implement share preview, project copy creation, object grants, member
invites and shared-object retrieval. Every route resolves the authenticated
subject and enforces project membership and object grants before exposing data.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from science_companion.api.auth import SubjectDep
from science_companion.contracts.projects import Project, ProjectCreateRequest
from science_companion.contracts.sharing import (
    InviteAcceptRequest,
    InviteCreateRequest,
    InviteToken,
    ObjectGrant,
    SharedProjectMember,
    ShareExecuteRequest,
    ShareExecuteResult,
    SharePreview,
    SharePreviewRequest,
    SharingError,
)
from science_companion.contracts.vault import VaultObject
from science_companion.sharing import SharingService, SharingServiceError

router = APIRouter(prefix="/sharing", tags=["sharing"])


def _get_sharing_service(request: Request) -> SharingService:
    service: SharingService | None = getattr(request.app.state, "sharing_service", None)
    if service is None:
        raise RuntimeError("SharingService not attached to application state.")
    return service


SharingServiceDep = Annotated[SharingService, Depends(_get_sharing_service)]


def _sharing_error(status_code: int, error: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=SharingError(error=error, message=message).model_dump(),
    )


@router.post(
    "/projects",
    response_model=Project,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": SharingError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": SharingError},
    },
)
async def create_shared_project(
    service: SharingServiceDep,
    subject: SubjectDep,
    request: ProjectCreateRequest,
) -> Project:
    """Create a new explicit shared project owned by the current account."""
    return service.create_shared_project(
        account_id=subject.account_id,
        name=request.name,
        description=request.description,
    )


@router.get(
    "/projects",
    response_model=list[Project],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": SharingError},
    },
)
async def list_shared_projects(
    service: SharingServiceDep,
    subject: SubjectDep,
) -> list[Project]:
    """List shared projects where the current account is a member."""
    return service.list_shared_projects(account_id=subject.account_id)


@router.get(
    "/projects/{project_id}",
    response_model=Project,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": SharingError},
        status.HTTP_404_NOT_FOUND: {"model": SharingError},
    },
)
async def get_shared_project(
    service: SharingServiceDep,
    subject: SubjectDep,
    project_id: str,
) -> Project:
    """Get a shared project projection by deep-link identifier."""
    try:
        return service.get_shared_project(
            account_id=subject.account_id,
            project_id=project_id,
        )
    except SharingServiceError as exc:
        raise _sharing_error(
            status.HTTP_404_NOT_FOUND,
            "project_not_found",
            str(exc),
        ) from exc


@router.post(
    "/preview",
    response_model=SharePreview,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": SharingError},
        status.HTTP_403_FORBIDDEN: {"model": SharingError},
        status.HTTP_404_NOT_FOUND: {"model": SharingError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": SharingError},
    },
)
async def preview_share(
    service: SharingServiceDep,
    subject: SubjectDep,
    request: SharePreviewRequest,
) -> SharePreview:
    """Preview what will be copied before confirming a share."""
    try:
        return service.preview_share(
            account_id=subject.account_id,
            request=request,
        )
    except SharingServiceError as exc:
        raise _sharing_error(
            status.HTTP_404_NOT_FOUND,
            "source_or_project_not_found",
            str(exc),
        ) from exc


@router.post(
    "/execute",
    response_model=ShareExecuteResult,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": SharingError},
        status.HTTP_403_FORBIDDEN: {"model": SharingError},
        status.HTTP_404_NOT_FOUND: {"model": SharingError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": SharingError},
    },
)
async def execute_share(
    service: SharingServiceDep,
    subject: SubjectDep,
    request: ShareExecuteRequest,
) -> ShareExecuteResult:
    """Execute a share and create the minimized project copy and object grant."""
    try:
        return service.execute_share(
            account_id=subject.account_id,
            request=request,
        )
    except SharingServiceError as exc:
        raise _sharing_error(
            status.HTTP_404_NOT_FOUND,
            "share_failed",
            str(exc),
        ) from exc


@router.post(
    "/projects/{project_id}/invites",
    response_model=InviteToken,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": SharingError},
        status.HTTP_403_FORBIDDEN: {"model": SharingError},
        status.HTTP_404_NOT_FOUND: {"model": SharingError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": SharingError},
    },
)
async def create_invite(
    service: SharingServiceDep,
    subject: SubjectDep,
    project_id: str,
    request: InviteCreateRequest,
) -> InviteToken:
    """Create a short-lived, single-use invite token for a shared project."""
    if request.project_id != project_id:
        raise _sharing_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "project_id_mismatch",
            "路径与请求体中的项目标识不一致。",
        )
    try:
        return service.create_invite(
            account_id=subject.account_id,
            request=request,
        )
    except SharingServiceError as exc:
        raise _sharing_error(
            status.HTTP_404_NOT_FOUND,
            "invite_failed",
            str(exc),
        ) from exc


@router.post(
    "/invites/accept",
    response_model=SharedProjectMember,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": SharingError},
        status.HTTP_403_FORBIDDEN: {"model": SharingError},
        status.HTTP_404_NOT_FOUND: {"model": SharingError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": SharingError},
    },
)
async def accept_invite(
    service: SharingServiceDep,
    subject: SubjectDep,
    request: InviteAcceptRequest,
) -> SharedProjectMember:
    """Accept an invite token and join the shared project."""
    try:
        return service.accept_invite(
            account_id=subject.account_id,
            request=request,
        )
    except SharingServiceError as exc:
        raise _sharing_error(
            status.HTTP_404_NOT_FOUND,
            "invite_invalid",
            str(exc),
        ) from exc


@router.get(
    "/projects/{project_id}/objects/{object_id}",
    response_model=VaultObject,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": SharingError},
        status.HTTP_403_FORBIDDEN: {"model": SharingError},
        status.HTTP_404_NOT_FOUND: {"model": SharingError},
    },
)
async def get_shared_object(
    service: SharingServiceDep,
    subject: SubjectDep,
    project_id: str,
    object_id: str,
) -> VaultObject:
    """Get a shared project object projection if the current account is authorized."""
    try:
        return service.get_shared_object(
            project_id=project_id,
            account_id=subject.account_id,
            object_id=object_id,
        )
    except SharingServiceError as exc:
        raise _sharing_error(
            status.HTTP_404_NOT_FOUND,
            "object_not_found",
            str(exc),
        ) from exc


@router.get(
    "/projects/{project_id}/objects/{object_id}/content",
    responses={
        status.HTTP_200_OK: {"content": {"application/octet-stream": {}}},
        status.HTTP_401_UNAUTHORIZED: {"model": SharingError},
        status.HTTP_403_FORBIDDEN: {"model": SharingError},
        status.HTTP_404_NOT_FOUND: {"model": SharingError},
    },
)
async def get_shared_object_content(
    service: SharingServiceDep,
    subject: SubjectDep,
    project_id: str,
    object_id: str,
) -> Response:
    """Get the plaintext of a shared project object if the account has a grant."""
    try:
        content = service.get_shared_object_content(
            project_id=project_id,
            account_id=subject.account_id,
            object_id=object_id,
        )
    except SharingServiceError as exc:
        raise _sharing_error(
            status.HTTP_404_NOT_FOUND,
            "object_not_found",
            str(exc),
        ) from exc
    return Response(content=content, media_type="application/octet-stream")


@router.get(
    "/projects/{project_id}/members",
    response_model=list[SharedProjectMember],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": SharingError},
        status.HTTP_404_NOT_FOUND: {"model": SharingError},
    },
)
async def list_project_members(
    service: SharingServiceDep,
    subject: SubjectDep,
    project_id: str,
) -> list[SharedProjectMember]:
    """List members of a shared project."""
    try:
        return service.list_project_members(
            project_id=project_id,
            account_id=subject.account_id,
        )
    except SharingServiceError as exc:
        raise _sharing_error(
            status.HTTP_404_NOT_FOUND,
            "project_not_found",
            str(exc),
        ) from exc


@router.post(
    "/grants/{grant_id}/revoke",
    response_model=ObjectGrant,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": SharingError},
        status.HTTP_403_FORBIDDEN: {"model": SharingError},
        status.HTTP_404_NOT_FOUND: {"model": SharingError},
    },
)
async def revoke_grant(
    service: SharingServiceDep,
    subject: SubjectDep,
    grant_id: str,
) -> ObjectGrant:
    """Revoke an object grant."""
    try:
        return service.revoke_grant(
            account_id=subject.account_id,
            grant_id=grant_id,
        )
    except SharingServiceError as exc:
        raise _sharing_error(
            status.HTTP_404_NOT_FOUND,
            "grant_not_found",
            str(exc),
        ) from exc
