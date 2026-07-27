"""Media asset API routes for T030.

Routes implement uploading, retrieving, correcting and revoking scientific
images, scans, formulas and tables within the scope of an account and project.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from science_companion.api.auth import SubjectDep
from science_companion.contracts.media import (
    DerivedAsset,
    MediaCorrectionRequest,
    MediaIngestionRunRef,
    MediaProjection,
    MediaUploadRequest,
)
from science_companion.contracts.media import (
    MediaError as MediaErrorContract,
)
from science_companion.contracts.science import ClaimGraphResult, ClaimRequest
from science_companion.media import MediaError, MediaIngestionService
from science_companion.science import ClaimEvidenceService

router = APIRouter(prefix="/media", tags=["media"])


def _get_media_service(request: Request) -> MediaIngestionService:
    service: MediaIngestionService | None = getattr(
        request.app.state, "media_ingestion_service", None
    )
    if service is None:
        raise RuntimeError("MediaIngestionService not attached to application state.")
    return service


def _get_claim_service(request: Request) -> ClaimEvidenceService:
    service: ClaimEvidenceService | None = getattr(
        request.app.state, "claim_evidence_service", None
    )
    if service is None:
        raise RuntimeError("ClaimEvidenceService not attached to application state.")
    return service


MediaServiceDep = Annotated[MediaIngestionService, Depends(_get_media_service)]
ClaimServiceDep = Annotated[ClaimEvidenceService, Depends(_get_claim_service)]


def _media_error(status_code: int, error: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=MediaErrorContract(error=error, message=message).model_dump(),
    )


@router.post(
    "/projects/{project_id}/assets",
    response_model=MediaIngestionRunRef,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": MediaErrorContract},
        status.HTTP_403_FORBIDDEN: {"model": MediaErrorContract},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": MediaErrorContract},
    },
)
async def upload_media_to_project(
    service: MediaServiceDep,
    subject: SubjectDep,
    project_id: str,
    request: MediaUploadRequest,
) -> MediaIngestionRunRef:
    """Upload a media asset into a project."""
    try:
        return service.ingest_upload(
            account_id=subject.account_id,
            project_id=project_id,
            request=request,
        )
    except MediaError as exc:
        raise _media_error(
            status.HTTP_403_FORBIDDEN, "media_ingestion_failed", str(exc)
        ) from exc


@router.post(
    "/assets",
    response_model=MediaIngestionRunRef,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": MediaErrorContract},
        status.HTTP_403_FORBIDDEN: {"model": MediaErrorContract},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": MediaErrorContract},
    },
)
async def upload_media(
    service: MediaServiceDep,
    subject: SubjectDep,
    request: MediaUploadRequest,
) -> MediaIngestionRunRef:
    """Upload a personal media asset (not bound to a project)."""
    try:
        return service.ingest_upload(
            account_id=subject.account_id,
            project_id=None,
            request=request,
        )
    except MediaError as exc:
        raise _media_error(
            status.HTTP_403_FORBIDDEN, "media_ingestion_failed", str(exc)
        ) from exc


@router.get(
    "/assets/{asset_id}",
    response_model=MediaProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": MediaErrorContract},
        status.HTTP_404_NOT_FOUND: {"model": MediaErrorContract},
    },
)
async def get_media_asset(
    service: MediaServiceDep,
    subject: SubjectDep,
    asset_id: str,
) -> MediaProjection:
    """Get a media projection with source asset, manifest and derived assets."""
    try:
        return service.get_asset(account_id=subject.account_id, asset_id=asset_id)
    except MediaError as exc:
        raise _media_error(
            status.HTTP_404_NOT_FOUND, "media_asset_not_found", str(exc)
        ) from exc


@router.post(
    "/assets/{asset_id}/derived",
    response_model=DerivedAsset,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": MediaErrorContract},
        status.HTTP_404_NOT_FOUND: {"model": MediaErrorContract},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": MediaErrorContract},
    },
)
async def correct_derived_asset(
    service: MediaServiceDep,
    subject: SubjectDep,
    asset_id: str,
    request: MediaCorrectionRequest,
) -> DerivedAsset:
    """Apply a human correction to a derived asset and create a new version."""
    try:
        return service.correct_derived_asset(
            account_id=subject.account_id,
            asset_id=asset_id,
            request=request,
            subject=subject,
        )
    except MediaError as exc:
        raise _media_error(
            status.HTTP_404_NOT_FOUND, "derived_asset_not_found", str(exc)
        ) from exc


@router.post(
    "/assets/{asset_id}/claim-graph",
    response_model=ClaimGraphResult,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": MediaErrorContract},
        status.HTTP_403_FORBIDDEN: {"model": MediaErrorContract},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": MediaErrorContract},
    },
)
async def generate_media_claim_graph(
    media_service: MediaServiceDep,
    claim_service: ClaimServiceDep,
    subject: SubjectDep,
    asset_id: str,
    request: ClaimRequest,
) -> ClaimGraphResult:
    """Generate a ClaimGraph from a media asset's derived structures.

    The media service ensures the asset is accessible and active; the existing
    claim-evidence service produces the locatable claim graph.
    """
    try:
        # Ensure the asset is accessible and active before generating claims.
        media_service.get_asset(subject.account_id, asset_id)
        return claim_service.generate_claim_graph(subject, request)
    except MediaError as exc:
        raise _media_error(
            status.HTTP_403_FORBIDDEN, "media_claim_generation_failed", str(exc)
        ) from exc


@router.post(
    "/assets/{asset_id}/revoke",
    response_model=dict[str, Any],
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": MediaErrorContract},
        status.HTTP_404_NOT_FOUND: {"model": MediaErrorContract},
    },
)
async def revoke_media_asset(
    service: MediaServiceDep,
    subject: SubjectDep,
    asset_id: str,
) -> dict[str, Any]:
    """Revoke a media asset so it cannot be used in new evidence."""
    try:
        asset_ref, event = service.revoke_asset(
            account_id=subject.account_id,
            asset_id=asset_id,
            reason="用户撤权",
            subject=subject,
        )
    except MediaError as exc:
        raise _media_error(
            status.HTTP_404_NOT_FOUND, "media_asset_not_found", str(exc)
        ) from exc

    return {
        "asset_id": asset_id,
        "status": "revoked",
        "object_ref": asset_ref.model_dump(),
        "invalidation_event_id": event.event_id if event is not None else None,
    }
