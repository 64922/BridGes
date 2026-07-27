"""Evaluation center API routes.

Routes implement creation of evaluation runs from completed project task runs,
replay, result-bundle retrieval and diff comparison. Every route resolves the
authenticated subject and enforces account-level ownership.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from science_companion.api.auth import SubjectDep
from science_companion.contracts.evaluation import (
    EvaluationCreateRequest,
    EvaluationDiffProjection,
    EvaluationErrorResponse,
    EvaluationResultBundle,
    EvaluationRunProjection,
)
from science_companion.evaluation import EvaluationError, EvaluationService
from science_companion.workflows import WorkflowError, WorkflowService

router = APIRouter(prefix="/projects", tags=["evaluation"])


class _DiffRequest(BaseModel):
    """Request body to compare two evaluation result bundles."""

    bundle_id_a: str = Field(description="First bundle identifier.")
    bundle_id_b: str = Field(description="Second bundle identifier.")


def _get_workflow_service(request: Request) -> WorkflowService:
    service: WorkflowService | None = getattr(request.app.state, "workflow_service", None)
    if service is None:
        raise RuntimeError("WorkflowService not attached to application state.")
    return service


def _get_evaluation_service(request: Request) -> EvaluationService:
    service: EvaluationService | None = getattr(request.app.state, "evaluation_service", None)
    if service is None:
        raise RuntimeError("EvaluationService not attached to application state.")
    return service


WorkflowServiceDep = Annotated[WorkflowService, Depends(_get_workflow_service)]
EvaluationServiceDep = Annotated[EvaluationService, Depends(_get_evaluation_service)]


def _evaluation_error(status_code: int, error: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=EvaluationErrorResponse(error=error, message=message).model_dump(),
    )


@router.post(
    "/{project_id}/runs/{run_id}/evaluations",
    response_model=EvaluationRunProjection,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": EvaluationErrorResponse},
        status.HTTP_401_UNAUTHORIZED: {"model": EvaluationErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": EvaluationErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": EvaluationErrorResponse},
    },
)
async def create_evaluation_run(
    workflow_service: WorkflowServiceDep,
    evaluation_service: EvaluationServiceDep,
    subject: SubjectDep,
    project_id: str,
    run_id: str,
    request: EvaluationCreateRequest,
) -> EvaluationRunProjection:
    """Create an evaluation run from a completed project task run."""
    try:
        source_run = workflow_service.get_run(
            account_id=subject.account_id,
            run_id=run_id,
        )
    except WorkflowError as exc:
        raise _evaluation_error(
            status.HTTP_404_NOT_FOUND,
            "run_not_found",
            str(exc),
        ) from exc

    if source_run.project_id != project_id:
        raise _evaluation_error(
            status.HTTP_404_NOT_FOUND,
            "run_not_found",
            "运行不存在或没有访问权限。",
        )

    try:
        return evaluation_service.create_evaluation_run(
            account_id=subject.account_id,
            source_run=source_run,
            request=request,
        )
    except EvaluationError as exc:
        raise _evaluation_error(
            status.HTTP_400_BAD_REQUEST,
            "evaluation_create_failed",
            str(exc),
        ) from exc


@router.post(
    "/{project_id}/evaluations/{evaluation_run_id}/replay",
    response_model=EvaluationResultBundle,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": EvaluationErrorResponse},
        status.HTTP_401_UNAUTHORIZED: {"model": EvaluationErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": EvaluationErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": EvaluationErrorResponse},
    },
)
async def replay_evaluation_run(
    evaluation_service: EvaluationServiceDep,
    subject: SubjectDep,
    project_id: str,
    evaluation_run_id: str,
) -> EvaluationResultBundle:
    """Replay an evaluation run lock and produce a new result bundle."""
    try:
        bundle = evaluation_service.replay_evaluation_run(
            account_id=subject.account_id,
            evaluation_run_id=evaluation_run_id,
        )
    except EvaluationError as exc:
        raise _evaluation_error(
            status.HTTP_400_BAD_REQUEST,
            "evaluation_replay_failed",
            str(exc),
        ) from exc

    if bundle.project_id != project_id:
        raise _evaluation_error(
            status.HTTP_404_NOT_FOUND,
            "evaluation_run_not_found",
            "评测运行不存在或没有访问权限。",
        )
    return bundle


@router.get(
    "/{project_id}/evaluations/{evaluation_run_id}",
    response_model=EvaluationRunProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": EvaluationErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": EvaluationErrorResponse},
    },
)
async def get_evaluation_run(
    evaluation_service: EvaluationServiceDep,
    subject: SubjectDep,
    project_id: str,
    evaluation_run_id: str,
) -> EvaluationRunProjection:
    """Return the evaluation-center projection for an evaluation run."""
    try:
        projection = evaluation_service.get_evaluation_run(
            account_id=subject.account_id,
            evaluation_run_id=evaluation_run_id,
        )
    except EvaluationError as exc:
        raise _evaluation_error(
            status.HTTP_404_NOT_FOUND,
            "evaluation_run_not_found",
            str(exc),
        ) from exc

    if projection.project_id != project_id:
        raise _evaluation_error(
            status.HTTP_404_NOT_FOUND,
            "evaluation_run_not_found",
            "评测运行不存在或没有访问权限。",
        )
    return projection


@router.get(
    "/{project_id}/evaluations/{evaluation_run_id}/bundles/{bundle_id}",
    response_model=EvaluationResultBundle,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": EvaluationErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": EvaluationErrorResponse},
    },
)
async def get_result_bundle(
    evaluation_service: EvaluationServiceDep,
    subject: SubjectDep,
    project_id: str,
    evaluation_run_id: str,
    bundle_id: str,
) -> EvaluationResultBundle:
    """Return a single evaluation result bundle."""
    try:
        bundle = evaluation_service.get_result_bundle(
            account_id=subject.account_id,
            bundle_id=bundle_id,
        )
    except EvaluationError as exc:
        raise _evaluation_error(
            status.HTTP_404_NOT_FOUND,
            "bundle_not_found",
            str(exc),
        ) from exc

    if bundle.project_id != project_id or bundle.lock_id != evaluation_run_id:
        raise _evaluation_error(
            status.HTTP_404_NOT_FOUND,
            "bundle_not_found",
            "结果包不存在或没有访问权限。",
        )
    return bundle


@router.post(
    "/{project_id}/evaluations/diff",
    response_model=EvaluationDiffProjection,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": EvaluationErrorResponse},
        status.HTTP_401_UNAUTHORIZED: {"model": EvaluationErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": EvaluationErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": EvaluationErrorResponse},
    },
)
async def compare_evaluation_bundles(
    evaluation_service: EvaluationServiceDep,
    subject: SubjectDep,
    project_id: str,
    request: _DiffRequest,
) -> EvaluationDiffProjection:
    """Compare two result bundles from the same evaluation run lock."""
    try:
        return evaluation_service.compare_bundles(
            account_id=subject.account_id,
            bundle_id_a=request.bundle_id_a,
            bundle_id_b=request.bundle_id_b,
        )
    except EvaluationError as exc:
        raise _evaluation_error(
            status.HTTP_400_BAD_REQUEST,
            "evaluation_diff_failed",
            str(exc),
        ) from exc
