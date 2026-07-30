"""Vault API routes and dependencies.

Routes implement private object creation, cloud control projection lookup,
temporary task capsule issuance, and device-unavailability handling. All routes
resolve the authenticated subject and enforce account-level ownership.
"""

from __future__ import annotations

import base64
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from science_companion.api.auth import SubjectDep
from science_companion.contracts.vault import (
    CapsuleIssueRequest,
    CloudControlProjection,
    DeviceCertificate,
    DevicePairingRequest,
    DevicePairingResponse,
    DeviceRevocationRequest,
    DeviceUnavailableState,
    TemporaryTaskCapsule,
    VaultError,
    VaultObject,
    VaultObjectCreateRequest,
    VaultObjectSummary,
    VaultShareRequest,
)
from science_companion.vault import VaultService
from science_companion.vault.adapters import VaultError as VaultAdapterError

router = APIRouter(prefix="/vault", tags=["vault"])


def _get_vault_service(request: Request) -> VaultService:
    service: VaultService | None = getattr(request.app.state, "vault_service", None)
    if service is None:
        raise RuntimeError("VaultService not attached to application state.")
    return service


VaultServiceDep = Annotated[VaultService, Depends(_get_vault_service)]


def _vault_error(status_code: int, error: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=VaultError(error=error, message=message).model_dump(),
    )


@router.post(
    "/objects",
    response_model=VaultObject,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": VaultError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": VaultError},
    },
)
async def create_object(
    service: VaultServiceDep,
    subject: SubjectDep,
    request: VaultObjectCreateRequest,
) -> VaultObject:
    """Create a private vault object owned by the current account."""
    # Enforce that the subject owns the object.
    if request.owner_account_id != subject.account_id:
        raise _vault_error(
            status.HTTP_403_FORBIDDEN,
            "ownership_mismatch",
            "只能为自己的账户创建保险库对象。",
        )
    content_bytes = base64.b64decode(request.content)
    try:
        return service.create_private_object(
            owner_account_id=request.owner_account_id,
            content=content_bytes,
            content_authority=request.content_authority,
            device_id=request.device_id,
            purpose=request.purpose,
        )
    except VaultAdapterError as exc:
        raise _vault_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "object_creation_failed",
            str(exc),
        ) from exc


@router.get(
    "/objects",
    response_model=list[VaultObjectSummary],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": VaultError},
    },
)
async def list_objects(
    service: VaultServiceDep,
    subject: SubjectDep,
) -> list[VaultObjectSummary]:
    """List vault object summaries for the current account."""
    return service.list_objects(subject.account_id)


@router.get(
    "/objects/{object_id}",
    response_model=VaultObject,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": VaultError},
        status.HTTP_404_NOT_FOUND: {"model": VaultError},
    },
)
async def get_object(
    service: VaultServiceDep,
    subject: SubjectDep,
    object_id: str,
) -> VaultObject:
    """Get a vault object projection by deep-link identifier."""
    try:
        result = service.get_object(subject.account_id, object_id)
    except VaultAdapterError as exc:
        raise _vault_error(
            status.HTTP_404_NOT_FOUND,
            "object_not_found",
            str(exc),
        ) from exc
    if isinstance(result, DeviceUnavailableState):
        raise _vault_error(
            status.HTTP_404_NOT_FOUND,
            "device_unavailable",
            "设备不可用，无法获取对象投影。",
        )
    return result


@router.get(
    "/objects/{object_id}/content",
    responses={
        status.HTTP_200_OK: {"content": {"application/octet-stream": {}}},
        status.HTTP_202_ACCEPTED: {"model": DeviceUnavailableState},
        status.HTTP_401_UNAUTHORIZED: {"model": VaultError},
        status.HTTP_404_NOT_FOUND: {"model": VaultError},
    },
)
async def get_content(
    service: VaultServiceDep,
    subject: SubjectDep,
    object_id: str,
) -> Response:
    """Request plaintext content for a vault object.

    If the authoritative device is unavailable, the endpoint returns 202 Accepted
    with a DeviceUnavailableState instead of silently uploading full text.
    """
    try:
        result = service.get_content(subject.account_id, object_id)
    except VaultAdapterError as exc:
        raise _vault_error(
            status.HTTP_404_NOT_FOUND,
            "object_not_found",
            str(exc),
        ) from exc
    if isinstance(result, DeviceUnavailableState):
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content=result.model_dump(),
        )
    return Response(content=result, media_type="application/octet-stream")


@router.post(
    "/objects/{object_id}/capsules",
    response_model=TemporaryTaskCapsule,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": VaultError},
        status.HTTP_404_NOT_FOUND: {"model": VaultError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": VaultError},
    },
)
async def issue_task_capsule(
    service: VaultServiceDep,
    subject: SubjectDep,
    object_id: str,
    request: CapsuleIssueRequest,
) -> TemporaryTaskCapsule:
    """Issue a temporary task capsule for a vault object."""
    if request.owner_account_id != subject.account_id:
        raise _vault_error(
            status.HTTP_403_FORBIDDEN,
            "ownership_mismatch",
            "只能为自己的账户发放胶囊。",
        )
    if request.object_id != object_id:
        raise _vault_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "object_id_mismatch",
            "路径与请求体中的对象标识不一致。",
        )
    try:
        return service.issue_task_capsule(
            owner_account_id=request.owner_account_id,
            object_id=request.object_id,
            run_id=request.run_id,
            purpose=request.purpose,
            ttl_seconds=request.ttl_seconds,
        )
    except VaultAdapterError as exc:
        raise _vault_error(
            status.HTTP_404_NOT_FOUND,
            "object_not_found",
            str(exc),
        ) from exc


@router.post(
    "/capsules/{capsule_id}/revoke",
    response_model=TemporaryTaskCapsule,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": VaultError},
        status.HTTP_404_NOT_FOUND: {"model": VaultError},
    },
)
async def revoke_capsule(
    service: VaultServiceDep,
    subject: SubjectDep,
    capsule_id: str,
) -> TemporaryTaskCapsule:
    """Revoke a previously issued task capsule."""
    try:
        return service.revoke_capsule(subject.account_id, capsule_id)
    except VaultAdapterError as exc:
        raise _vault_error(
            status.HTTP_404_NOT_FOUND,
            "capsule_not_found",
            str(exc),
        ) from exc


@router.post(
    "/objects/{object_id}/share",
    response_model=dict[str, Any],
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": VaultError},
        status.HTTP_404_NOT_FOUND: {"model": VaultError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": VaultError},
    },
)
async def share_as_project_copy(
    service: VaultServiceDep,
    subject: SubjectDep,
    object_id: str,
    request: VaultShareRequest,
) -> dict[str, Any]:
    """Share a personal vault object as an independent minimized project copy."""
    if request.source_object_id != object_id:
        raise _vault_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "object_id_mismatch",
            "路径与请求体中的源对象标识不一致。",
        )
    try:
        project_ref = service.share_as_project_copy(
            owner_account_id=subject.account_id,
            request=request,
        )
    except VaultAdapterError as exc:
        raise _vault_error(
            status.HTTP_404_NOT_FOUND,
            "source_not_found",
            str(exc),
        ) from exc

    return {
        "project_ref": project_ref.model_dump(),
        "source_object_id": request.source_object_id,
        "purpose": request.grant_purpose,
    }


@router.get(
    "/objects/{object_id}/projection",
    response_model=CloudControlProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": VaultError},
        status.HTTP_404_NOT_FOUND: {"model": VaultError},
    },
)
async def get_cloud_projection(
    service: VaultServiceDep,
    subject: SubjectDep,
    object_id: str,
) -> CloudControlProjection:
    """Get the cloud control projection for a vault object."""
    projection = service.get_cloud_projection(subject.account_id, object_id)
    if projection is None:
        raise _vault_error(
            status.HTTP_404_NOT_FOUND,
            "projection_not_found",
            "投影不存在或没有访问权限。",
        )
    return projection


@router.post(
    "/devices/pair",
    response_model=DevicePairingResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": VaultError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": VaultError},
    },
)
async def pair_device(
    service: VaultServiceDep,
    subject: SubjectDep,
    request: DevicePairingRequest,
) -> DevicePairingResponse:
    """Pair a new vault runtime with the current account."""
    try:
        return service.pair_device(subject.account_id, request)
    except VaultAdapterError as exc:
        raise _vault_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "pairing_failed",
            str(exc),
        ) from exc


@router.get(
    "/devices",
    response_model=list[DeviceCertificate],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": VaultError},
    },
)
async def list_devices(
    service: VaultServiceDep,
    subject: SubjectDep,
) -> list[DeviceCertificate]:
    """List paired devices for the current account."""
    try:
        return service.list_device_certificates(subject.account_id)
    except VaultAdapterError as exc:
        raise _vault_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "list_devices_failed",
            str(exc),
        ) from exc


@router.post(
    "/devices/{device_id}/revoke",
    response_model=DeviceCertificate,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": VaultError},
        status.HTTP_404_NOT_FOUND: {"model": VaultError},
    },
)
async def revoke_device(
    service: VaultServiceDep,
    subject: SubjectDep,
    device_id: str,
    request: DeviceRevocationRequest,
) -> DeviceCertificate:
    """Revoke a paired device and rotate its key epoch."""
    if request.device_id != device_id:
        raise _vault_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "device_id_mismatch",
            "路径与请求体中的设备标识不一致。",
        )
    try:
        return service.revoke_device(subject.account_id, request)
    except VaultAdapterError as exc:
        raise _vault_error(
            status.HTTP_404_NOT_FOUND,
            "device_not_found",
            str(exc),
        ) from exc
