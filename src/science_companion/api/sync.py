"""API routes for control-first device synchronization."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from science_companion.api.auth import SubjectDep
from science_companion.contracts.sync import (
    ConflictBranch,
    ConflictResolutionRequest,
    SyncControlSnapshot,
    SyncExchangeRequest,
    SyncExchangeResponse,
)
from science_companion.sync import SyncError, SyncService

router = APIRouter(prefix="/sync", tags=["sync"])


def _get_sync_service(request: Request) -> SyncService:
    service: SyncService | None = getattr(request.app.state, "sync_service", None)
    if service is None:
        raise RuntimeError("SyncService not attached to application state.")
    return service


SyncServiceDep = Annotated[SyncService, Depends(_get_sync_service)]


@router.get("/control", response_model=SyncControlSnapshot)
async def get_control_snapshot(
    service: SyncServiceDep,
    subject: SubjectDep,
    device_id: str,
) -> SyncControlSnapshot:
    """Pull authorization, key epoch, revocations and tombstones."""
    return service.pull_control(subject.account_id, device_id)


@router.post(
    "/exchange",
    response_model=SyncExchangeResponse,
    status_code=status.HTTP_200_OK,
)
async def exchange(
    service: SyncServiceDep,
    subject: SubjectDep,
    request: SyncExchangeRequest,
) -> SyncExchangeResponse:
    """Apply a device outbox only after the control snapshot is evaluated."""
    try:
        return service.exchange(subject.account_id, request)
    except SyncError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/conflicts", response_model=list[ConflictBranch])
async def list_conflicts(
    service: SyncServiceDep,
    subject: SubjectDep,
) -> list[ConflictBranch]:
    """List scientific conflicts retained for human裁决."""
    return service.list_conflicts(subject.account_id)


@router.post(
    "/conflicts/{conflict_id}/resolve",
    response_model=ConflictBranch,
)
async def resolve_conflict(
    conflict_id: str,
    request: ConflictResolutionRequest,
    service: SyncServiceDep,
    subject: SubjectDep,
) -> ConflictBranch:
    """记录人工选择的冲突分支，并保留两条历史记录。"""
    try:
        return service.resolve_conflict(
            subject.account_id,
            conflict_id,
            request.selected_operation_id,
        )
    except SyncError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
