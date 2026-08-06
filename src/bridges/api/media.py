"""Media asset API routes for T030, T032, T033, T034 and T035.

T030 routes: uploading, retrieving, correcting and revoking scientific
images, scans, formulas and tables within the scope of an account and project.

T032 routes: generating data charts and scientific figures with editable
sources, SVG rendering and claim bindings.

T033 routes: creating, updating and validating structured storyboards,
generating source code, running in sandbox, and retrieving validation reports.

T034 routes: generating, retrieving and validating complete accessibility
bundles (narration, captions, transcript, keyboard paths, reduced motion,
sequential reading) and controlling playback of timed content.

T035 routes: cross-media claim consistency checking, multi-modal publish
gate evaluation, publishing and listing publish records.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from bridges.api.auth import SubjectDep
from bridges.contracts.media import (
    AccessibilityBundle,
    AccessibilityBundleRequest,
    AccessibilityValidationResult,
    ChartGenerationRequest,
    ChartMark,
    CrossMediaClaimEntry,
    CrossMediaConsistencyResult,
    DerivedAsset,
    FigureGenerationRequest,
    GenerationResult,
    MediaCorrectionRequest,
    MediaIngestionRunRef,
    MediaPublishRequest,
    MediaProjection,
    MediaStoryboard,
    MediaUploadRequest,
    MultimodalPublishGateResult,
    PlaybackControlRequest,
    PlaybackState,
    SandboxRunRequest,
    SandboxRunResult,
    SandboxRunStatus,
    ScientificMediaObject,
    SpecValidationResult,
    StoryboardGenerationRequest,
    StoryboardResult,
    ValidationReport,
)
from bridges.contracts.media import (
    MediaError as MediaErrorContract,
)
from bridges.contracts.science import ClaimGraphResult, ClaimRequest
from bridges.media import (
    MediaError,
    MediaGenerationError,
    MediaGenerationService,
    MediaIngestionService,
    MediaPublishError,
    MediaPublishService,
)
from bridges.media.accessibility_service import (
    AccessibilityError,
    AccessibilityService,
)
from bridges.media.storyboard_service import (
    SandboxError,
    SandboxService,
    StoryboardError,
    StoryboardService,
    build_validation_report,
)
from bridges.science import ClaimEvidenceService

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


def _get_generation_service(request: Request) -> MediaGenerationService:
    service: MediaGenerationService | None = getattr(
        request.app.state, "media_generation_service", None
    )
    if service is None:
        raise RuntimeError("MediaGenerationService not attached to application state.")
    return service


def _get_publish_service(request: Request) -> MediaPublishService:
    service: MediaPublishService | None = getattr(
        request.app.state, "media_publish_service", None
    )
    if service is None:
        raise RuntimeError("MediaPublishService not attached to application state.")
    return service


GenerationServiceDep = Annotated[MediaGenerationService, Depends(_get_generation_service)]
PublishServiceDep = Annotated[MediaPublishService, Depends(_get_publish_service)]


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


# ── T032: Chart and figure generation routes ──────────────────────────


@router.post(
    "/charts",
    response_model=GenerationResult,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": MediaErrorContract},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": MediaErrorContract},
    },
)
async def create_chart(
    service: GenerationServiceDep,
    subject: SubjectDep,
    request: ChartGenerationRequest,
) -> GenerationResult:
    """Generate a data chart from structured data with claim bindings."""
    try:
        return service.generate_chart(request, account_id=subject.account_id)
    except MediaGenerationError as exc:
        raise _media_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "chart_generation_failed", str(exc)
        ) from exc


@router.post(
    "/figures",
    response_model=GenerationResult,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": MediaErrorContract},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": MediaErrorContract},
    },
)
async def create_figure(
    service: GenerationServiceDep,
    subject: SubjectDep,
    request: FigureGenerationRequest,
) -> GenerationResult:
    """Generate a scientific figure from element definitions."""
    try:
        return service.generate_figure(request, account_id=subject.account_id)
    except MediaGenerationError as exc:
        raise _media_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "figure_generation_failed", str(exc)
        ) from exc


@router.get(
    "/objects/{object_id}",
    response_model=ScientificMediaObject,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": MediaErrorContract},
        status.HTTP_404_NOT_FOUND: {"model": MediaErrorContract},
    },
)
async def get_media_object(
    service: GenerationServiceDep,
    subject: SubjectDep,
    object_id: str,
) -> ScientificMediaObject:
    """Get a generated media object by ID (owner-scoped, Issue 39 AC9)."""
    try:
        return service.get_media_object(object_id, account_id=subject.account_id)
    except MediaGenerationError as exc:
        raise _media_error(
            status.HTTP_404_NOT_FOUND, "media_object_not_found", str(exc)
        ) from exc


@router.put(
    "/objects/{object_id}/spec",
    response_model=ScientificMediaObject,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": MediaErrorContract},
        status.HTTP_404_NOT_FOUND: {"model": MediaErrorContract},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": MediaErrorContract},
    },
)
async def update_media_object_spec(
    service: GenerationServiceDep,
    subject: SubjectDep,
    object_id: str,
    spec_json: str,
) -> ScientificMediaObject:
    """Update the editable source of a chart or figure and re-validate."""
    try:
        obj = service.get_media_object(object_id, account_id=subject.account_id)
    except MediaGenerationError as exc:
        # 对象不存在或跨账户：统一 404，不进入规格校验路径（Issue 39 AC9）。
        raise _media_error(
            status.HTTP_404_NOT_FOUND, "media_object_not_found", str(exc)
        ) from exc
    try:
        if obj.media_type.value == "chart":
            return service.update_chart_spec(
                object_id, spec_json, account_id=subject.account_id
            )
        return service.update_figure_spec(
            object_id, spec_json, account_id=subject.account_id
        )
    except MediaGenerationError as exc:
        raise _media_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "spec_update_failed", str(exc)
        ) from exc


@router.post(
    "/validate-spec",
    response_model=SpecValidationResult,
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": MediaErrorContract},
    },
)
async def validate_spec(
    service: GenerationServiceDep,
    subject: SubjectDep,
    spec_json: str,
) -> SpecValidationResult:
    """Validate a chart or figure spec JSON without generating output."""
    try:
        return service.validate_spec(spec_json)
    except MediaGenerationError as exc:
        raise _media_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "spec_validation_failed", str(exc)
        ) from exc


# ── T033: Storyboard and sandbox routes ─────────────────────────────


def _get_storyboard_service(request: Request) -> StoryboardService:
    service: StoryboardService | None = getattr(
        request.app.state, "storyboard_service", None
    )
    if service is None:
        raise RuntimeError("StoryboardService not attached to application state.")
    return service


def _get_sandbox_service(request: Request) -> SandboxService:
    service: SandboxService | None = getattr(
        request.app.state, "sandbox_service", None
    )
    if service is None:
        raise RuntimeError("SandboxService not attached to application state.")
    return service


StoryboardServiceDep = Annotated[StoryboardService, Depends(_get_storyboard_service)]
SandboxServiceDep = Annotated[SandboxService, Depends(_get_sandbox_service)]


@router.post(
    "/storyboards",
    response_model=StoryboardResult,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": MediaErrorContract},
        status.HTTP_403_FORBIDDEN: {"model": MediaErrorContract},
    },
)
async def create_storyboard(
    service: StoryboardServiceDep,
    subject: SubjectDep,
    request: StoryboardGenerationRequest,
) -> StoryboardResult:
    """Create a structured storyboard from a generation request."""
    try:
        return service.generate_storyboard(
            request,
            account_id=subject.account_id,
        )
    except StoryboardError as exc:
        raise _media_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "storyboard_generation_failed",
            str(exc),
        ) from exc


@router.get(
    "/storyboards/{storyboard_id}",
    response_model=MediaStoryboard,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": MediaErrorContract},
        status.HTTP_404_NOT_FOUND: {"model": MediaErrorContract},
    },
)
async def get_storyboard(
    service: StoryboardServiceDep,
    subject: SubjectDep,
    storyboard_id: str,
) -> MediaStoryboard:
    """Get a storyboard by ID (owner-scoped, Issue 39 AC9)."""
    try:
        return service.get_storyboard(storyboard_id, account_id=subject.account_id)
    except StoryboardError as exc:
        raise _media_error(
            status.HTTP_404_NOT_FOUND, "storyboard_not_found", str(exc)
        ) from exc


@router.put(
    "/storyboards/{storyboard_id}",
    response_model=MediaStoryboard,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": MediaErrorContract},
        status.HTTP_404_NOT_FOUND: {"model": MediaErrorContract},
    },
)
async def update_storyboard(
    service: StoryboardServiceDep,
    subject: SubjectDep,
    storyboard_id: str,
    title: str | None = None,
    teaching_objectives: list[str] | None = None,
) -> MediaStoryboard:
    """Update a storyboard's metadata."""
    try:
        return service.update_storyboard(
            storyboard_id,
            title=title,
            teaching_objectives=teaching_objectives,
            account_id=subject.account_id,
        )
    except StoryboardError as exc:
        raise _media_error(
            status.HTTP_404_NOT_FOUND, "storyboard_update_failed", str(exc)
        ) from exc


@router.post(
    "/storyboards/{storyboard_id}/code",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": MediaErrorContract},
        status.HTTP_404_NOT_FOUND: {"model": MediaErrorContract},
    },
)
async def generate_storyboard_code(
    service: StoryboardServiceDep,
    subject: SubjectDep,
    storyboard_id: str,
    code_language: str = "html",
) -> dict[str, object]:
    """Generate executable source code from a storyboard."""
    try:
        source = service.generate_source_code(
            storyboard_id, code_language, account_id=subject.account_id
        )
        return {"editable_source": source.model_dump()}
    except StoryboardError as exc:
        raise _media_error(
            status.HTTP_404_NOT_FOUND, "code_generation_failed", str(exc)
        ) from exc


@router.post(
    "/storyboards/{storyboard_id}/sandbox",
    response_model=SandboxRunResult,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": MediaErrorContract},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": MediaErrorContract},
    },
)
async def run_storyboard_sandbox(
    storyboard_service: StoryboardServiceDep,
    sandbox_service: SandboxServiceDep,
    subject: SubjectDep,
    storyboard_id: str,
    request: SandboxRunRequest,
) -> SandboxRunResult:
    """Run generated code in the isolated sandbox."""
    try:
        # Ensure the storyboard exists and belongs to the current account.
        storyboard_service.get_storyboard(storyboard_id, account_id=subject.account_id)
        return sandbox_service.run(
            request,
            account_id=subject.account_id,
        )
    except (StoryboardError, SandboxError) as exc:
        raise _media_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "sandbox_run_failed",
            str(exc),
        ) from exc


@router.get(
    "/sandbox-runs/{run_id}",
    response_model=SandboxRunResult,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": MediaErrorContract},
        status.HTTP_404_NOT_FOUND: {"model": MediaErrorContract},
    },
)
async def get_sandbox_run(
    sandbox_service: SandboxServiceDep,
    subject: SubjectDep,
    run_id: str,
) -> SandboxRunResult:
    """Get a sandbox run result by ID (owner-scoped, Issue 39 AC9)."""
    try:
        return sandbox_service.get_run(run_id, account_id=subject.account_id)
    except SandboxError as exc:
        raise _media_error(
            status.HTTP_404_NOT_FOUND, "sandbox_run_not_found", str(exc)
        ) from exc


@router.post(
    "/sandbox-runs/{run_id}/repair",
    response_model=SandboxRunResult,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": MediaErrorContract},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": MediaErrorContract},
    },
)
async def repair_sandbox_run(
    sandbox_service: SandboxServiceDep,
    subject: SubjectDep,
    run_id: str,
    patch: str,
    code_language: str = "python",
    fact_lock_ids: list[str] | None = None,
) -> SandboxRunResult:
    """Attempt a limited repair on a failed sandbox run."""
    try:
        return sandbox_service.repair(
            run_id,
            patch,
            account_id=subject.account_id,
            fact_locks=None,
            fact_lock_ids=fact_lock_ids or [],
        )
    except SandboxError as exc:
        raise _media_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "sandbox_repair_failed",
            str(exc),
        ) from exc


@router.get(
    "/storyboards/{storyboard_id}/validate",
    response_model=ValidationReport,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": MediaErrorContract},
        status.HTTP_404_NOT_FOUND: {"model": MediaErrorContract},
    },
)
async def validate_storyboard(
    storyboard_service: StoryboardServiceDep,
    sandbox_service: SandboxServiceDep,
    subject: SubjectDep,
    storyboard_id: str,
    run_id: str,
) -> ValidationReport:
    """Get a validation report for a storyboard sandbox run."""
    try:
        return build_validation_report(
            storyboard_service,
            sandbox_service,
            storyboard_id,
            run_id,
            account_id=subject.account_id,
        )
    except (StoryboardError, SandboxError) as exc:
        raise _media_error(
            status.HTTP_404_NOT_FOUND, "validation_report_failed", str(exc)
        ) from exc


# ── T034: Accessibility alternative routes ──────────────────────────


def _get_accessibility_service(request: Request) -> AccessibilityService:
    service: AccessibilityService | None = getattr(
        request.app.state, "accessibility_service", None
    )
    if service is None:
        raise RuntimeError("AccessibilityService not attached to application state.")
    return service


AccessibilityServiceDep = Annotated[
    AccessibilityService, Depends(_get_accessibility_service)
]


@router.post(
    "/accessibility/bundles",
    response_model=AccessibilityBundle,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": MediaErrorContract},
        status.HTTP_404_NOT_FOUND: {"model": MediaErrorContract},
    },
)
async def generate_accessibility_bundle(
    service: AccessibilityServiceDep,
    subject: SubjectDep,
    request: AccessibilityBundleRequest,
) -> AccessibilityBundle:
    """Generate a complete accessibility bundle for a media target."""
    try:
        return service.generate_bundle(request, account_id=subject.account_id)
    except AccessibilityError as exc:
        raise _media_error(
            status.HTTP_404_NOT_FOUND, "accessibility_target_not_found", str(exc)
        ) from exc


@router.get(
    "/accessibility/bundles/{bundle_id}",
    response_model=AccessibilityBundle,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": MediaErrorContract},
        status.HTTP_404_NOT_FOUND: {"model": MediaErrorContract},
    },
)
async def get_accessibility_bundle(
    service: AccessibilityServiceDep,
    subject: SubjectDep,
    bundle_id: str,
) -> AccessibilityBundle:
    """Get an accessibility bundle by ID."""
    try:
        return service.get_bundle(bundle_id, account_id=subject.account_id)
    except AccessibilityError as exc:
        raise _media_error(
            status.HTTP_404_NOT_FOUND, "accessibility_bundle_not_found", str(exc)
        ) from exc


@router.get(
    "/accessibility/bundles/{bundle_id}/validate",
    response_model=AccessibilityValidationResult,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": MediaErrorContract},
        status.HTTP_404_NOT_FOUND: {"model": MediaErrorContract},
    },
)
async def validate_accessibility_bundle(
    service: AccessibilityServiceDep,
    subject: SubjectDep,
    bundle_id: str,
) -> AccessibilityValidationResult:
    """Validate claim/version sharing, operability and science checks."""
    try:
        return service.validate_bundle(bundle_id, account_id=subject.account_id)
    except AccessibilityError as exc:
        raise _media_error(
            status.HTTP_404_NOT_FOUND, "accessibility_bundle_not_found", str(exc)
        ) from exc


@router.post(
    "/accessibility/bundles/{bundle_id}/playback",
    response_model=PlaybackState,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": MediaErrorContract},
        status.HTTP_404_NOT_FOUND: {"model": MediaErrorContract},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": MediaErrorContract},
    },
)
async def control_accessibility_playback(
    service: AccessibilityServiceDep,
    subject: SubjectDep,
    bundle_id: str,
    request: PlaybackControlRequest,
) -> PlaybackState:
    """Pause, resume, seek and toggle reduced motion for timed content."""
    try:
        return service.control_playback(
            bundle_id, request, account_id=subject.account_id
        )
    except AccessibilityError as exc:
        raise _media_error(
            status.HTTP_404_NOT_FOUND, "playback_control_failed", str(exc)
        ) from exc


# ── T035: Cross-media consistency and publish routes ──────────────────


@router.post(
    "/cross-media/consistency",
    response_model=CrossMediaConsistencyResult,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": MediaErrorContract},
    },
)
async def check_cross_media_consistency(
    service: PublishServiceDep,
    subject: SubjectDep,
    entries: list[CrossMediaClaimEntry],
) -> CrossMediaConsistencyResult:
    """Check cross-media Claim consistency before publishing."""
    try:
        return service.check_cross_media_consistency(subject.account_id, entries)
    except Exception as exc:
        raise _media_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "consistency_check_failed", str(exc),
        ) from exc


@router.post(
    "/publish/check",
    response_model=MultimodalPublishGateResult,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": MediaErrorContract},
    },
)
async def evaluate_publish_gate(
    service: PublishServiceDep,
    subject: SubjectDep,
    request: MediaPublishRequest,
) -> MultimodalPublishGateResult:
    """Evaluate multi-modal publish gates without publishing."""
    try:
        return service.evaluate_publish_gate(subject.account_id, request)
    except Exception as exc:
        raise _media_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "publish_gate_evaluation_failed", str(exc),
        ) from exc


@router.post(
    "/publish",
    response_model=dict[str, Any],
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": MediaErrorContract},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": MediaErrorContract},
    },
)
async def publish_media(
    service: PublishServiceDep,
    subject: SubjectDep,
    request: MediaPublishRequest,
) -> dict[str, Any]:
    """Execute multi-modal publish. All gates must pass."""
    try:
        record = service.publish(subject.account_id, request)
        return {"record_id": record.record_id, "published_at": record.published_at.isoformat()}
    except MediaPublishError as exc:
        raise _media_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "publish_failed", str(exc)
        ) from exc


@router.get(
    "/publish/{record_id}",
    response_model=dict[str, Any],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": MediaErrorContract},
        status.HTTP_404_NOT_FOUND: {"model": MediaErrorContract},
    },
)
async def get_publish_record(
    service: PublishServiceDep,
    subject: SubjectDep,
    record_id: str,
) -> dict[str, Any]:
    """Get a publish record by ID."""
    try:
        record = service.get_publish_record(subject.account_id, record_id)
        return record.model_dump(mode="json")
    except MediaPublishError as exc:
        raise _media_error(
            status.HTTP_404_NOT_FOUND, "publish_record_not_found", str(exc)
        ) from exc


@router.get(
    "/publish",
    response_model=list[dict[str, Any]],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": MediaErrorContract},
    },
)
async def list_publish_records(
    service: PublishServiceDep,
    subject: SubjectDep,
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    """List publish records for the current account, optionally filtered by project."""
    try:
        records = service.list_publish_records(subject.account_id, project_id)
        return [record.model_dump(mode="json") for record in records]
    except Exception as exc:
        raise _media_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "list_publish_records_failed", str(exc),
        ) from exc
