"""Learning mission and diagnosis API routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from science_companion.api.auth import SubjectDep
from science_companion.contracts.learning import (
    DiagnosticAnswerCreateRequest,
    DiagnosticQuestionCreateRequest,
    DiagnosticResult,
    DiagnosticRun,
    KnowledgeState,
    KnowledgeStateCorrection,
    LearningActivityType,
    LearningError,
    LearningMission,
    LearningMissionCreateRequest,
    TeachingPlan,
)
from science_companion.learning import LearningService
from science_companion.learning.adapters import LearningError as LearningAdapterError

router = APIRouter(prefix="/learning", tags=["learning"])


def _get_learning_service(request: Request) -> LearningService:
    service: LearningService | None = getattr(request.app.state, "learning_service", None)
    if service is None:
        raise RuntimeError("LearningService not attached to application state.")
    return service


LearningServiceDep = Annotated[LearningService, Depends(_get_learning_service)]


def _error(status_code: int, error: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=LearningError(error=error, message=message).model_dump(),
    )


@router.post(
    "/missions",
    response_model=LearningMission,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": LearningError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": LearningError},
    },
)
async def create_mission(
    service: LearningServiceDep,
    subject: SubjectDep,
    request: LearningMissionCreateRequest,
) -> LearningMission:
    """Create a learning mission for the current user."""
    return service.create_mission(subject.account_id, request)


@router.get(
    "/missions",
    response_model=list[LearningMission],
)
async def list_missions(
    service: LearningServiceDep,
    subject: SubjectDep,
    project_id: str | None = Query(default=None),
) -> list[LearningMission]:
    """List learning missions for the current user."""
    return service.list_missions(subject.account_id, project_id)


@router.get(
    "/missions/{mission_id}",
    response_model=LearningMission,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": LearningError},
    },
)
async def get_mission(
    service: LearningServiceDep,
    subject: SubjectDep,
    mission_id: str,
) -> LearningMission:
    """Get a learning mission by ID."""
    try:
        return service.get_mission(subject.account_id, mission_id)
    except LearningAdapterError as exc:
        raise _error(status.HTTP_404_NOT_FOUND, "mission_not_found", str(exc))


@router.post(
    "/missions/{mission_id}/runs",
    response_model=DiagnosticRun,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": LearningError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": LearningError},
    },
)
async def create_diagnostic_run(
    service: LearningServiceDep,
    subject: SubjectDep,
    mission_id: str,
    questions: list[DiagnosticQuestionCreateRequest] | None = None,
) -> DiagnosticRun:
    """Create a diagnostic run for a mission."""
    try:
        return service.create_diagnostic_run(
            subject.account_id, mission_id, questions=questions
        )
    except LearningAdapterError as exc:
        raise _error(status.HTTP_404_NOT_FOUND, "mission_not_found", str(exc))


@router.get(
    "/runs/{run_id}",
    response_model=DiagnosticRun,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": LearningError},
    },
)
async def get_diagnostic_run(
    service: LearningServiceDep,
    subject: SubjectDep,
    run_id: str,
) -> DiagnosticRun:
    """Get a diagnostic run by ID."""
    try:
        return service.get_diagnostic_run(subject.account_id, run_id)
    except LearningAdapterError as exc:
        raise _error(status.HTTP_404_NOT_FOUND, "run_not_found", str(exc))


@router.post(
    "/runs/{run_id}/answers",
    response_model=DiagnosticRun,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": LearningError},
        status.HTTP_400_BAD_REQUEST: {"model": LearningError},
    },
)
async def record_answer(
    service: LearningServiceDep,
    subject: SubjectDep,
    run_id: str,
    request: DiagnosticAnswerCreateRequest,
) -> DiagnosticRun:
    """Record a diagnostic answer and return the updated run."""
    try:
        service.record_answer(subject.account_id, run_id, request)
        return service.get_diagnostic_run(subject.account_id, run_id)
    except LearningAdapterError as exc:
        raise _error(status.HTTP_400_BAD_REQUEST, "answer_failed", str(exc))


@router.post(
    "/runs/{run_id}/complete",
    response_model=DiagnosticResult,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": LearningError},
        status.HTTP_400_BAD_REQUEST: {"model": LearningError},
    },
)
async def complete_diagnostic(
    service: LearningServiceDep,
    subject: SubjectDep,
    run_id: str,
) -> DiagnosticResult:
    """Complete a diagnostic run and compute evidence-backed knowledge states."""
    try:
        return service.complete_diagnostic(subject.account_id, run_id)
    except LearningAdapterError as exc:
        raise _error(status.HTTP_400_BAD_REQUEST, "complete_failed", str(exc))


@router.get(
    "/results/{result_id}",
    response_model=DiagnosticResult,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": LearningError},
    },
)
async def get_diagnostic_result(
    service: LearningServiceDep,
    subject: SubjectDep,
    result_id: str,
) -> DiagnosticResult:
    """Get a diagnostic result by ID."""
    try:
        return service.get_diagnostic_result(subject.account_id, result_id)
    except LearningAdapterError as exc:
        raise _error(status.HTTP_404_NOT_FOUND, "result_not_found", str(exc))


@router.get(
    "/missions/{mission_id}/states",
    response_model=list[KnowledgeState],
)
async def list_knowledge_states(
    service: LearningServiceDep,
    subject: SubjectDep,
    mission_id: str,
) -> list[KnowledgeState]:
    """List knowledge states for a mission."""
    try:
        return service.list_knowledge_states(subject.account_id, mission_id)
    except LearningAdapterError as exc:
        raise _error(status.HTTP_404_NOT_FOUND, "mission_not_found", str(exc))


@router.post(
    "/missions/{mission_id}/states/correct",
    response_model=KnowledgeState,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": LearningError},
        status.HTTP_400_BAD_REQUEST: {"model": LearningError},
    },
)
async def correct_knowledge_state(
    service: LearningServiceDep,
    subject: SubjectDep,
    mission_id: str,
    correction: KnowledgeStateCorrection,
) -> KnowledgeState:
    """Correct a knowledge state with a user-supplied judgement."""
    try:
        return service.correct_knowledge_state(
            subject.account_id, mission_id, correction
        )
    except LearningAdapterError as exc:
        raise _error(status.HTTP_400_BAD_REQUEST, "correction_failed", str(exc))


@router.get(
    "/missions/{mission_id}/teaching-plan",
    response_model=TeachingPlan,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": LearningError},
    },
)
async def compile_teaching_plan(
    service: LearningServiceDep,
    subject: SubjectDep,
    mission_id: str,
) -> TeachingPlan:
    """Compile a minimal teaching plan from the mission and current states."""
    try:
        return service.compile_teaching_plan(subject.account_id, mission_id)
    except LearningAdapterError as exc:
        raise _error(status.HTTP_404_NOT_FOUND, "plan_failed", str(exc))
