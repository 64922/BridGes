"""Media asset API routes for T030-T035 (legacy read-only + retired writes).

The old media write endpoints (asset ingestion/correction/revocation, chart/
figure generation, storyboard/sandbox authoring, accessibility bundle creation,
playback control, cross-media consistency and publish) are retired during the
ADR-0026 compatibility window. They return HTTP 410 with a stable error code
and Chinese guidance pointing to the modern chat and knowledge-base products.

Read endpoints that are genuinely side-effect-free remain available for
historical access and export.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from bridges.api.auth import SubjectDep
from bridges.contracts.media import (
    AccessibilityBundle,
    AccessibilityValidationResult,
    MediaProjection,
    MediaStoryboard,
    SandboxRunResult,
    ScientificMediaObject,
)
from bridges.contracts.media import (
    MediaError as MediaErrorContract,
)
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
)
from bridges.retirement import raise_retired_capability

router = APIRouter(prefix="/media", tags=["media"])


# ── Service dependencies for the remaining read-only endpoints ─────────


def _get_media_service(request: Request) -> MediaIngestionService:
    service: MediaIngestionService | None = getattr(
        request.app.state, "media_ingestion_service", None
    )
    if service is None:
        raise RuntimeError("MediaIngestionService not attached to application state.")
    return service


MediaServiceDep = Annotated[MediaIngestionService, Depends(_get_media_service)]


def _get_generation_service(request: Request) -> MediaGenerationService:
    service: MediaGenerationService | None = getattr(
        request.app.state, "media_generation_service", None
    )
    if service is None:
        raise RuntimeError("MediaGenerationService not attached to application state.")
    return service


GenerationServiceDep = Annotated[
    MediaGenerationService, Depends(_get_generation_service)
]


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


def _get_publish_service(request: Request) -> MediaPublishService:
    service: MediaPublishService | None = getattr(
        request.app.state, "media_publish_service", None
    )
    if service is None:
        raise RuntimeError("MediaPublishService not attached to application state.")
    return service


PublishServiceDep = Annotated[MediaPublishService, Depends(_get_publish_service)]


def _media_error(status_code: int, error: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=MediaErrorContract(error=error, message=message).model_dump(),
    )


# ── Legacy write command retirement registry ───────────────────────────


_RETIRED_ASSET_MESSAGE = (
    "旧媒体写入能力已退役。上传与检索材料请使用全局知识库；"
    "图片生成/编辑与视频生成请使用聊天入口。"
)

_RETIRED_OTHER_MESSAGE = (
    "旧媒体写入能力已退役。图片生成/编辑与视频生成请使用聊天入口；"
    "旧分镜、图表与沙箱没有一对一替代功能。"
)


def _retired_message_for(endpoint: str) -> str:
    """按旧能力分组给出区分现代替代路径的中文说明（AC2）。"""
    if endpoint.startswith("legacy.media.assets."):
        return _RETIRED_ASSET_MESSAGE
    return _RETIRED_OTHER_MESSAGE

_RETIRED_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_410_GONE: {"model": MediaErrorContract},
}

# (method, path_template, endpoint_id, replacement_path)
LEGACY_MEDIA_WRITE_COMMANDS: list[tuple[str, str, str, str]] = [
    # T030: old asset writes
    (
        "POST",
        "/media/projects/{project_id}/assets",
        "legacy.media.assets.project_create",
        "/knowledge-base",
    ),
    ("POST", "/media/assets", "legacy.media.assets.create", "/knowledge-base"),
    (
        "POST",
        "/media/assets/{asset_id}/derived",
        "legacy.media.assets.derived_create",
        "/knowledge-base",
    ),
    (
        "POST",
        "/media/assets/{asset_id}/claim-graph",
        "legacy.media.assets.claim_graph",
        "/chat",
    ),
    (
        "POST",
        "/media/assets/{asset_id}/revoke",
        "legacy.media.assets.revoke",
        "/knowledge-base",
    ),
    # T032: chart/figure/spec writes
    ("POST", "/media/charts", "legacy.media.charts.create", "/chat"),
    ("POST", "/media/figures", "legacy.media.figures.create", "/chat"),
    (
        "PUT",
        "/media/objects/{object_id}/spec",
        "legacy.media.objects.spec_update",
        "/chat",
    ),
    ("POST", "/media/validate-spec", "legacy.media.validate_spec", "/chat"),
    # T033: storyboard/sandbox writes
    ("POST", "/media/storyboards", "legacy.media.storyboards.create", "/chat"),
    (
        "PUT",
        "/media/storyboards/{storyboard_id}",
        "legacy.media.storyboards.update",
        "/chat",
    ),
    (
        "POST",
        "/media/storyboards/{storyboard_id}/code",
        "legacy.media.storyboards.code",
        "/chat",
    ),
    (
        "POST",
        "/media/storyboards/{storyboard_id}/sandbox",
        "legacy.media.storyboards.sandbox",
        "/chat",
    ),
    (
        "POST",
        "/media/sandbox-runs/{run_id}/repair",
        "legacy.media.sandbox_runs.repair",
        "/chat",
    ),
    # GET with write side-effect
    (
        "GET",
        "/media/storyboards/{storyboard_id}/validate",
        "legacy.media.storyboards.validate",
        "/chat",
    ),
    # T034: accessibility writes
    (
        "POST",
        "/media/accessibility/bundles",
        "legacy.media.accessibility.bundles.create",
        "/chat",
    ),
    (
        "POST",
        "/media/accessibility/bundles/{bundle_id}/playback",
        "legacy.media.accessibility.bundles.playback",
        "/chat",
    ),
    # T035: publish writes
    (
        "POST",
        "/media/cross-media/consistency",
        "legacy.media.publish.consistency",
        "/chat",
    ),
    ("POST", "/media/publish/check", "legacy.media.publish.check", "/chat"),
    ("POST", "/media/publish", "legacy.media.publish", "/chat"),
]

# Read-only GET endpoints kept under ADR-0026 historical-read policy.
READ_ONLY_MEDIA_GETS: list[tuple[str, str]] = [
    ("GET", "/media/assets/{asset_id}"),
    ("GET", "/media/objects/{object_id}"),
    ("GET", "/media/storyboards/{storyboard_id}"),
    ("GET", "/media/sandbox-runs/{run_id}"),
    ("GET", "/media/accessibility/bundles/{bundle_id}"),
    ("GET", "/media/accessibility/bundles/{bundle_id}/validate"),
    ("GET", "/media/publish/{record_id}"),
    ("GET", "/media/publish"),
]


def _retire_media_command(
    request: Request,
    endpoint: str,
    replacement_path: str,
) -> None:
    """Raise the uniform 410 retirement contract without parsing input."""
    raise_retired_capability(
        request,
        endpoint=endpoint,
        error="legacy_media_retired",
        message=_retired_message_for(endpoint),
        replacement_path=replacement_path,
    )


# ── T030 retired asset write endpoints ─────────────────────────────────


@router.post(
    "/projects/{project_id}/assets",
    status_code=status.HTTP_410_GONE,
    responses=_RETIRED_RESPONSES,
)
async def upload_media_to_project(
    project_id: str,
    request: Request,
    _subject: SubjectDep,
) -> None:
    _retire_media_command(request, "legacy.media.assets.project_create", "/knowledge-base")


@router.post(
    "/assets",
    status_code=status.HTTP_410_GONE,
    responses=_RETIRED_RESPONSES,
)
async def upload_media(
    request: Request,
    _subject: SubjectDep,
) -> None:
    _retire_media_command(request, "legacy.media.assets.create", "/knowledge-base")


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
    status_code=status.HTTP_410_GONE,
    responses=_RETIRED_RESPONSES,
)
async def correct_derived_asset(
    asset_id: str,
    request: Request,
    _subject: SubjectDep,
) -> None:
    _retire_media_command(request, "legacy.media.assets.derived_create", "/knowledge-base")


@router.post(
    "/assets/{asset_id}/claim-graph",
    status_code=status.HTTP_410_GONE,
    responses=_RETIRED_RESPONSES,
)
async def generate_media_claim_graph(
    asset_id: str,
    request: Request,
    _subject: SubjectDep,
) -> None:
    _retire_media_command(request, "legacy.media.assets.claim_graph", "/chat")


@router.post(
    "/assets/{asset_id}/revoke",
    status_code=status.HTTP_410_GONE,
    responses=_RETIRED_RESPONSES,
)
async def revoke_media_asset(
    asset_id: str,
    request: Request,
    _subject: SubjectDep,
) -> None:
    _retire_media_command(request, "legacy.media.assets.revoke", "/knowledge-base")


# ── T032 retired chart/figure/spec endpoints + read-only object GET ────


@router.post(
    "/charts",
    status_code=status.HTTP_410_GONE,
    responses=_RETIRED_RESPONSES,
)
async def create_chart(
    request: Request,
    _subject: SubjectDep,
) -> None:
    _retire_media_command(request, "legacy.media.charts.create", "/chat")


@router.post(
    "/figures",
    status_code=status.HTTP_410_GONE,
    responses=_RETIRED_RESPONSES,
)
async def create_figure(
    request: Request,
    _subject: SubjectDep,
) -> None:
    _retire_media_command(request, "legacy.media.figures.create", "/chat")


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
    status_code=status.HTTP_410_GONE,
    responses=_RETIRED_RESPONSES,
)
async def update_media_object_spec(
    object_id: str,
    request: Request,
    _subject: SubjectDep,
) -> None:
    _retire_media_command(request, "legacy.media.objects.spec_update", "/chat")


@router.post(
    "/validate-spec",
    status_code=status.HTTP_410_GONE,
    responses=_RETIRED_RESPONSES,
)
async def validate_spec(
    request: Request,
    _subject: SubjectDep,
) -> None:
    _retire_media_command(request, "legacy.media.validate_spec", "/chat")


# ── T033 retired storyboard/sandbox endpoints + read-only GETs ─────────


@router.post(
    "/storyboards",
    status_code=status.HTTP_410_GONE,
    responses=_RETIRED_RESPONSES,
)
async def create_storyboard(
    request: Request,
    _subject: SubjectDep,
) -> None:
    _retire_media_command(request, "legacy.media.storyboards.create", "/chat")


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
    status_code=status.HTTP_410_GONE,
    responses=_RETIRED_RESPONSES,
)
async def update_storyboard(
    storyboard_id: str,
    request: Request,
    _subject: SubjectDep,
) -> None:
    _retire_media_command(request, "legacy.media.storyboards.update", "/chat")


@router.post(
    "/storyboards/{storyboard_id}/code",
    status_code=status.HTTP_410_GONE,
    responses=_RETIRED_RESPONSES,
)
async def generate_storyboard_code(
    storyboard_id: str,
    request: Request,
    _subject: SubjectDep,
) -> None:
    _retire_media_command(request, "legacy.media.storyboards.code", "/chat")


@router.post(
    "/storyboards/{storyboard_id}/sandbox",
    status_code=status.HTTP_410_GONE,
    responses=_RETIRED_RESPONSES,
)
async def run_storyboard_sandbox(
    storyboard_id: str,
    request: Request,
    _subject: SubjectDep,
) -> None:
    _retire_media_command(request, "legacy.media.storyboards.sandbox", "/chat")


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
    status_code=status.HTTP_410_GONE,
    responses=_RETIRED_RESPONSES,
)
async def repair_sandbox_run(
    run_id: str,
    request: Request,
    _subject: SubjectDep,
) -> None:
    _retire_media_command(request, "legacy.media.sandbox_runs.repair", "/chat")


@router.get(
    "/storyboards/{storyboard_id}/validate",
    status_code=status.HTTP_410_GONE,
    responses=_RETIRED_RESPONSES,
)
async def validate_storyboard(
    storyboard_id: str,
    request: Request,
    _subject: SubjectDep,
) -> None:
    """Retired: this route previously mutated storyboard status on read."""
    _retire_media_command(request, "legacy.media.storyboards.validate", "/chat")


# ── T034 retired accessibility write endpoints + read-only GETs ────────


@router.post(
    "/accessibility/bundles",
    status_code=status.HTTP_410_GONE,
    responses=_RETIRED_RESPONSES,
)
async def generate_accessibility_bundle(
    request: Request,
    _subject: SubjectDep,
) -> None:
    _retire_media_command(request, "legacy.media.accessibility.bundles.create", "/chat")


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
    """Validate a bundle. Read-only: does not mutate bundle or playback state."""
    try:
        return service.validate_bundle(bundle_id, account_id=subject.account_id)
    except AccessibilityError as exc:
        raise _media_error(
            status.HTTP_404_NOT_FOUND, "accessibility_bundle_not_found", str(exc)
        ) from exc


@router.post(
    "/accessibility/bundles/{bundle_id}/playback",
    status_code=status.HTTP_410_GONE,
    responses=_RETIRED_RESPONSES,
)
async def control_accessibility_playback(
    bundle_id: str,
    request: Request,
    _subject: SubjectDep,
) -> None:
    _retire_media_command(request, "legacy.media.accessibility.bundles.playback", "/chat")


# ── T035 retired publish write endpoints + read-only GETs ──────────────


@router.post(
    "/cross-media/consistency",
    status_code=status.HTTP_410_GONE,
    responses=_RETIRED_RESPONSES,
)
async def check_cross_media_consistency(
    request: Request,
    _subject: SubjectDep,
) -> None:
    _retire_media_command(request, "legacy.media.publish.consistency", "/chat")


@router.post(
    "/publish/check",
    status_code=status.HTTP_410_GONE,
    responses=_RETIRED_RESPONSES,
)
async def evaluate_publish_gate(
    request: Request,
    _subject: SubjectDep,
) -> None:
    _retire_media_command(request, "legacy.media.publish.check", "/chat")


@router.post(
    "/publish",
    status_code=status.HTTP_410_GONE,
    responses=_RETIRED_RESPONSES,
)
async def publish_media(
    request: Request,
    _subject: SubjectDep,
) -> None:
    _retire_media_command(request, "legacy.media.publish", "/chat")


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
    """List publish records for the current account."""
    try:
        records = service.list_publish_records(subject.account_id, project_id)
        return [record.model_dump(mode="json") for record in records]
    except Exception as exc:
        raise _media_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "list_publish_records_failed", str(exc),
        ) from exc


__all__ = [
    "LEGACY_MEDIA_WRITE_COMMANDS",
    "READ_ONLY_MEDIA_GETS",
    "router",
]
