"""Institution management API routes.

Routes implement institution creation, membership management, policy,
institution-owned projects and controlled content access. Every route resolves
authenticated subject and fails closed on missing permissions.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from science_companion.api.auth import SubjectDep
from science_companion.contracts.institution import (
    ControlledContentAccessCreateRequest,
    ControlledContentAccessRequest,
    Institution,
    InstitutionCreateRequest,
    InstitutionError,
    InstitutionInviteRequest,
    InstitutionMemberUpdateRequest,
    InstitutionOwnedProjectDisclosure,
    InstitutionPolicy,
    InstitutionProjectCreateRequest,
    InstitutionProjectCreateResponse,
    Membership,
)
from science_companion.contracts.projects import Project
from science_companion.institution import InstitutionService, InstitutionServiceError

router = APIRouter(prefix="/institutions", tags=["institutions"])


def _get_institution_service(request: Request) -> InstitutionService:
    service: InstitutionService | None = getattr(
        request.app.state, "institution_service", None
    )
    if service is None:
        raise RuntimeError("InstitutionService not attached to application state.")
    return service


InstitutionServiceDep = Annotated[
    InstitutionService, Depends(_get_institution_service)
]


def _institution_error(status_code: int, error: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=InstitutionError(error=error, message=message).model_dump(),
    )


@router.post(
    "",
    response_model=Institution,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": InstitutionError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": InstitutionError},
    },
)
async def create_institution(
    service: InstitutionServiceDep,
    subject: SubjectDep,
    request: InstitutionCreateRequest,
) -> Institution:
    """Create a new institution. The creator becomes the first admin."""
    try:
        return service.create_institution(subject.account_id, request)
    except InstitutionServiceError as exc:
        raise _institution_error(
            status.HTTP_400_BAD_REQUEST, "institution_create_failed", str(exc)
        ) from exc


@router.get(
    "",
    response_model=list[Institution],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": InstitutionError},
    },
)
async def list_institutions(
    service: InstitutionServiceDep,
    subject: SubjectDep,
) -> list[Institution]:
    """List institutions the current account is a member of."""
    return service.list_institutions_for_account(subject.account_id)


@router.get(
    "/{institution_id}",
    response_model=Institution,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": InstitutionError},
        status.HTTP_404_NOT_FOUND: {"model": InstitutionError},
    },
)
async def get_institution(
    service: InstitutionServiceDep,
    subject: SubjectDep,
    institution_id: str,
) -> Institution:
    """Get an institution projection."""
    try:
        return service.get_institution(subject.account_id, institution_id)
    except InstitutionServiceError as exc:
        raise _institution_error(
            status.HTTP_404_NOT_FOUND, "institution_not_found", str(exc)
        ) from exc


@router.post(
    "/{institution_id}/invites",
    response_model=dict[str, Any],
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": InstitutionError},
        status.HTTP_403_FORBIDDEN: {"model": InstitutionError},
        status.HTTP_404_NOT_FOUND: {"model": InstitutionError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": InstitutionError},
    },
)
async def create_invite(
    service: InstitutionServiceDep,
    subject: SubjectDep,
    institution_id: str,
    request: InstitutionInviteRequest,
) -> dict[str, Any]:
    """Create an institution membership invite."""
    try:
        invite = service.invite_member(institution_id, subject.account_id, request)
    except InstitutionServiceError as exc:
        raise _institution_error(
            status.HTTP_404_NOT_FOUND, "invite_failed", str(exc)
        ) from exc
    return {
        "token_id": invite.token_id,
        "token_secret": invite.token_secret,
        "institution_id": invite.institution_id,
        "role": invite.role,
        "expires_at": invite.expires_at.isoformat(),
    }


@router.post(
    "/invites/accept",
    response_model=Membership,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": InstitutionError},
        status.HTTP_404_NOT_FOUND: {"model": InstitutionError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": InstitutionError},
    },
)
async def accept_invite(
    service: InstitutionServiceDep,
    subject: SubjectDep,
    token_secret: str,
) -> Membership:
    """Accept an institution invite and join the institution."""
    try:
        return service.accept_invite(subject.account_id, token_secret)
    except InstitutionServiceError as exc:
        raise _institution_error(
            status.HTTP_404_NOT_FOUND, "invite_invalid", str(exc)
        ) from exc


@router.get(
    "/{institution_id}/members",
    response_model=list[Membership],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": InstitutionError},
        status.HTTP_404_NOT_FOUND: {"model": InstitutionError},
    },
)
async def list_members(
    service: InstitutionServiceDep,
    subject: SubjectDep,
    institution_id: str,
) -> list[Membership]:
    """List members of an institution."""
    try:
        return service.list_members(subject.account_id, institution_id)
    except InstitutionServiceError as exc:
        raise _institution_error(
            status.HTTP_404_NOT_FOUND, "institution_not_found", str(exc)
        ) from exc


@router.patch(
    "/{institution_id}/members/{account_id}",
    response_model=Membership,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": InstitutionError},
        status.HTTP_403_FORBIDDEN: {"model": InstitutionError},
        status.HTTP_404_NOT_FOUND: {"model": InstitutionError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": InstitutionError},
    },
)
async def update_member_role(
    service: InstitutionServiceDep,
    subject: SubjectDep,
    institution_id: str,
    account_id: str,
    request: InstitutionMemberUpdateRequest,
) -> Membership:
    """Update an institution member's role."""
    try:
        return service.update_member_role(
            institution_id, subject.account_id, account_id, request
        )
    except InstitutionServiceError as exc:
        raise _institution_error(
            status.HTTP_404_NOT_FOUND, "member_update_failed", str(exc)
        ) from exc


@router.delete(
    "/{institution_id}/members/{account_id}",
    response_model=Membership,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": InstitutionError},
        status.HTTP_403_FORBIDDEN: {"model": InstitutionError},
        status.HTTP_404_NOT_FOUND: {"model": InstitutionError},
    },
)
async def remove_member(
    service: InstitutionServiceDep,
    subject: SubjectDep,
    institution_id: str,
    account_id: str,
) -> Membership:
    """Remove a member from the institution."""
    try:
        return service.remove_member(institution_id, subject.account_id, account_id)
    except InstitutionServiceError as exc:
        raise _institution_error(
            status.HTTP_404_NOT_FOUND, "member_remove_failed", str(exc)
        ) from exc


@router.get(
    "/{institution_id}/policy",
    response_model=InstitutionPolicy,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": InstitutionError},
        status.HTTP_404_NOT_FOUND: {"model": InstitutionError},
    },
)
async def get_policy(
    service: InstitutionServiceDep,
    subject: SubjectDep,
    institution_id: str,
) -> InstitutionPolicy:
    """Get the institution policy."""
    try:
        return service.get_policy(subject.account_id, institution_id)
    except InstitutionServiceError as exc:
        raise _institution_error(
            status.HTTP_404_NOT_FOUND, "institution_not_found", str(exc)
        ) from exc


@router.put(
    "/{institution_id}/policy",
    response_model=InstitutionPolicy,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": InstitutionError},
        status.HTTP_403_FORBIDDEN: {"model": InstitutionError},
        status.HTTP_404_NOT_FOUND: {"model": InstitutionError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": InstitutionError},
    },
)
async def update_policy(
    service: InstitutionServiceDep,
    subject: SubjectDep,
    institution_id: str,
    request: InstitutionPolicy,
) -> InstitutionPolicy:
    """Update the institution policy."""
    try:
        return service.update_policy(institution_id, subject.account_id, request)
    except InstitutionServiceError as exc:
        raise _institution_error(
            status.HTTP_404_NOT_FOUND, "policy_update_failed", str(exc)
        ) from exc


@router.get(
    "/{institution_id}/project-disclosure",
    response_model=InstitutionOwnedProjectDisclosure,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": InstitutionError},
        status.HTTP_404_NOT_FOUND: {"model": InstitutionError},
    },
)
async def get_project_disclosure(
    service: InstitutionServiceDep,
    subject: SubjectDep,
    institution_id: str,
) -> InstitutionOwnedProjectDisclosure:
    """Get the disclosure that must be acknowledged before creating an institution-owned project."""
    try:
        return service.build_project_disclosure(institution_id)
    except InstitutionServiceError as exc:
        raise _institution_error(
            status.HTTP_404_NOT_FOUND, "institution_not_found", str(exc)
        ) from exc


@router.post(
    "/{institution_id}/projects",
    response_model=InstitutionProjectCreateResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": InstitutionError},
        status.HTTP_403_FORBIDDEN: {"model": InstitutionError},
        status.HTTP_404_NOT_FOUND: {"model": InstitutionError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": InstitutionError},
    },
)
async def create_institution_project(
    service: InstitutionServiceDep,
    subject: SubjectDep,
    institution_id: str,
    request: InstitutionProjectCreateRequest,
) -> InstitutionProjectCreateResponse:
    """Create an institution-owned project after disclosure acknowledgement."""
    try:
        return service.create_institution_owned_project(
            subject.account_id, institution_id, request
        )
    except InstitutionServiceError as exc:
        raise _institution_error(
            status.HTTP_404_NOT_FOUND, "project_create_failed", str(exc)
        ) from exc


@router.get(
    "/{institution_id}/projects",
    response_model=list[Project],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": InstitutionError},
        status.HTTP_404_NOT_FOUND: {"model": InstitutionError},
    },
)
async def list_institution_projects(
    service: InstitutionServiceDep,
    subject: SubjectDep,
    institution_id: str,
) -> list[Project]:
    """List institution-owned projects visible to the current member."""
    try:
        return service.list_institution_projects(subject.account_id, institution_id)
    except InstitutionServiceError as exc:
        raise _institution_error(
            status.HTTP_404_NOT_FOUND, "institution_not_found", str(exc)
        ) from exc


@router.post(
    "/{institution_id}/content-access",
    response_model=ControlledContentAccessRequest,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": InstitutionError},
        status.HTTP_403_FORBIDDEN: {"model": InstitutionError},
        status.HTTP_404_NOT_FOUND: {"model": InstitutionError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": InstitutionError},
    },
)
async def request_content_access(
    service: InstitutionServiceDep,
    subject: SubjectDep,
    institution_id: str,
    request: ControlledContentAccessCreateRequest,
) -> ControlledContentAccessRequest:
    """Request controlled access to member content for a security incident."""
    try:
        return service.request_controlled_content_access(
            institution_id,
            subject.account_id,
            request.target_account_id,
            request.object_refs,
            request.purpose,
            request.duration_minutes,
        )
    except InstitutionServiceError as exc:
        raise _institution_error(
            status.HTTP_404_NOT_FOUND, "content_access_request_failed", str(exc)
        ) from exc


@router.post(
    "/{institution_id}/content-access/{request_id}/approve",
    response_model=ControlledContentAccessRequest,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": InstitutionError},
        status.HTTP_403_FORBIDDEN: {"model": InstitutionError},
        status.HTTP_404_NOT_FOUND: {"model": InstitutionError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": InstitutionError},
    },
)
async def approve_content_access(
    service: InstitutionServiceDep,
    subject: SubjectDep,
    institution_id: str,
    request_id: str,
    reason: str,
) -> ControlledContentAccessRequest:
    """Approve a controlled content access request (second approval required)."""
    try:
        return service.approve_controlled_content_access(
            institution_id, subject.account_id, request_id, reason
        )
    except InstitutionServiceError as exc:
        raise _institution_error(
            status.HTTP_404_NOT_FOUND, "content_access_approve_failed", str(exc)
        ) from exc


@router.post(
    "/{institution_id}/content-access/{request_id}/revoke",
    response_model=ControlledContentAccessRequest,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": InstitutionError},
        status.HTTP_403_FORBIDDEN: {"model": InstitutionError},
        status.HTTP_404_NOT_FOUND: {"model": InstitutionError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": InstitutionError},
    },
)
async def revoke_content_access(
    service: InstitutionServiceDep,
    subject: SubjectDep,
    institution_id: str,
    request_id: str,
) -> ControlledContentAccessRequest:
    """Revoke an approved or pending controlled content access request."""
    try:
        return service.revoke_controlled_content_access(
            institution_id, subject.account_id, request_id
        )
    except InstitutionServiceError as exc:
        raise _institution_error(
            status.HTTP_404_NOT_FOUND, "content_access_revoke_failed", str(exc)
        ) from exc


@router.get(
    "/{institution_id}/content-access",
    response_model=list[ControlledContentAccessRequest],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": InstitutionError},
        status.HTTP_404_NOT_FOUND: {"model": InstitutionError},
    },
)
async def list_content_access_requests(
    service: InstitutionServiceDep,
    subject: SubjectDep,
    institution_id: str,
) -> list[ControlledContentAccessRequest]:
    """List controlled content access requests visible to the caller."""
    try:
        return service.list_controlled_access_requests(
            subject.account_id, institution_id
        )
    except InstitutionServiceError as exc:
        raise _institution_error(
            status.HTTP_404_NOT_FOUND, "institution_not_found", str(exc)
        ) from exc
