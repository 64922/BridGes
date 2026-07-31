"""WorkOrder and task-stage API routes.

Routes implement WorkOrder submission, confirmation, cancellation, human todo
resolution, and task-stage projection. Every route resolves the authenticated
subject and enforces account-level ownership through the workflow service.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from science_companion.api.auth import SubjectDep
from science_companion.contracts.workflows import (
    RunProjection,
    WorkOrder,
    WorkOrderConfirmRequest,
    WorkflowErrorResponse,
)
from science_companion.projects import ProjectError as ProjectServiceError
from science_companion.projects import ProjectService
from science_companion.workflows import WorkflowError, WorkflowService

from pydantic import BaseModel, Field

router = APIRouter(prefix="/projects", tags=["workflows"])


def _get_workflow_service(request: Request) -> WorkflowService:
    service: WorkflowService | None = getattr(request.app.state, "workflow_service", None)
    if service is None:
        raise RuntimeError("WorkflowService not attached to application state.")
    return service


def _get_project_service(request: Request) -> ProjectService:
    service: ProjectService | None = getattr(request.app.state, "project_service", None)
    if service is None:
        raise RuntimeError("ProjectService not attached to application state.")
    return service


WorkflowServiceDep = Annotated[WorkflowService, Depends(_get_workflow_service)]
ProjectServiceDep = Annotated[ProjectService, Depends(_get_project_service)]


def _workflow_error(status_code: int, error: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=WorkflowErrorResponse(error=error, message=message).model_dump(),
    )


class _CancelRequest(BaseModel):
    """Request body for cancelling a run."""

    reason: str = Field(description="Reason recorded for the cancellation.")


class _TodoResolveRequest(BaseModel):
    """Request body for resolving a human todo."""

    resolution: str = Field(description="How the todo is resolved.")


@router.post(
    "/{project_id}/work-orders",
    response_model=RunProjection,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": WorkflowErrorResponse},
        status.HTTP_401_UNAUTHORIZED: {"model": WorkflowErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": WorkflowErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": WorkflowErrorResponse},
    },
)
async def submit_work_order(
    workflow_service: WorkflowServiceDep,
    project_service: ProjectServiceDep,
    subject: SubjectDep,
    project_id: str,
    order: WorkOrder,
) -> RunProjection:
    """Submit a WorkOrder within a project and return a draft task-stage projection."""
    try:
        project_service.get_project(account_id=subject.account_id, project_id=project_id)
    except ProjectServiceError as exc:
        raise _workflow_error(
            status.HTTP_404_NOT_FOUND,
            "project_not_found",
            str(exc),
        ) from exc

    # Ensure the WorkOrder matches the URL project so deep links re-authenticate consistently.
    if order.project_id != project_id:
        raise _workflow_error(
            status.HTTP_400_BAD_REQUEST,
            "project_mismatch",
            "WorkOrder 中的项目标识与 URL 不一致。",
        )

    try:
        return workflow_service.submit_work_order(account_id=subject.account_id, order=order)
    except WorkflowError as exc:
        # T011/T047：对象或领域包已失效时新运行闭锁，返回可解释状态。
        raise _workflow_error(
            status.HTTP_400_BAD_REQUEST,
            "workflow_transition_failed",
            str(exc),
        ) from exc


@router.post(
    "/{project_id}/runs/{run_id}/confirm",
    response_model=RunProjection,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": WorkflowErrorResponse},
        status.HTTP_401_UNAUTHORIZED: {"model": WorkflowErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": WorkflowErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": WorkflowErrorResponse},
    },
)
async def confirm_work_order(
    workflow_service: WorkflowServiceDep,
    subject: SubjectDep,
    project_id: str,
    run_id: str,
    request: WorkOrderConfirmRequest,
) -> RunProjection:
    """Confirm the WorkOrder and compile/start the run."""
    try:
        projection = workflow_service.confirm_work_order(
            account_id=subject.account_id,
            run_id=run_id,
            confirmed=request.confirmed,
        )
    except WorkflowError as exc:
        raise _workflow_error(
            status.HTTP_400_BAD_REQUEST,
            "workflow_transition_failed",
            str(exc),
        ) from exc

    if projection.project_id != project_id:
        raise _workflow_error(
            status.HTTP_404_NOT_FOUND,
            "run_not_found",
            "运行不存在或没有访问权限。",
        )
    return projection


@router.get(
    "/{project_id}/runs/{run_id}",
    response_model=RunProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": WorkflowErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": WorkflowErrorResponse},
    },
)
async def get_run_projection(
    workflow_service: WorkflowServiceDep,
    subject: SubjectDep,
    project_id: str,
    run_id: str,
) -> RunProjection:
    """Return the current task-stage projection for a run."""
    try:
        projection = workflow_service.get_run(account_id=subject.account_id, run_id=run_id)
    except WorkflowError as exc:
        raise _workflow_error(
            status.HTTP_404_NOT_FOUND,
            "run_not_found",
            str(exc),
        ) from exc

    if projection.project_id != project_id:
        raise _workflow_error(
            status.HTTP_404_NOT_FOUND,
            "run_not_found",
            "运行不存在或没有访问权限。",
        )
    return projection


@router.post(
    "/{project_id}/runs/{run_id}/cancel",
    response_model=RunProjection,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": WorkflowErrorResponse},
        status.HTTP_401_UNAUTHORIZED: {"model": WorkflowErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": WorkflowErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": WorkflowErrorResponse},
    },
)
async def cancel_run(
    workflow_service: WorkflowServiceDep,
    subject: SubjectDep,
    project_id: str,
    run_id: str,
    request: _CancelRequest,
) -> RunProjection:
    """Cancel a run that has not reached a terminal state."""
    try:
        projection = workflow_service.cancel_run(
            account_id=subject.account_id,
            run_id=run_id,
            reason=request.reason,
        )
    except WorkflowError as exc:
        raise _workflow_error(
            status.HTTP_400_BAD_REQUEST,
            "workflow_transition_failed",
            str(exc),
        ) from exc

    if projection.project_id != project_id:
        raise _workflow_error(
            status.HTTP_404_NOT_FOUND,
            "run_not_found",
            "运行不存在或没有访问权限。",
        )
    return projection


@router.post(
    "/{project_id}/runs/{run_id}/todos/{todo_id}/resolve",
    response_model=RunProjection,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": WorkflowErrorResponse},
        status.HTTP_401_UNAUTHORIZED: {"model": WorkflowErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": WorkflowErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": WorkflowErrorResponse},
    },
)
async def resolve_human_todo(
    workflow_service: WorkflowServiceDep,
    subject: SubjectDep,
    project_id: str,
    run_id: str,
    todo_id: str,
    request: _TodoResolveRequest,
) -> RunProjection:
    """Resolve a named human todo and continue past its gate."""
    try:
        projection = workflow_service.resolve_human_todo(
            account_id=subject.account_id,
            run_id=run_id,
            todo_id=todo_id,
            resolution=request.resolution,
        )
    except WorkflowError as exc:
        raise _workflow_error(
            status.HTTP_400_BAD_REQUEST,
            "workflow_transition_failed",
            str(exc),
        ) from exc

    if projection.project_id != project_id:
        raise _workflow_error(
            status.HTTP_404_NOT_FOUND,
            "run_not_found",
            "运行不存在或没有访问权限。",
        )
    return projection
