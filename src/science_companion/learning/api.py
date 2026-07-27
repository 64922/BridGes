"""Learning mission and diagnosis API routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from science_companion.api.auth import SubjectDep
from science_companion.contracts.learning import (
    DecideKnowledgeStateProposalRequest,
    DiagnosticAnswerCreateRequest,
    DiagnosticQuestionCreateRequest,
    DiagnosticResult,
    DiagnosticRun,
    ExerciseAttempt,
    ExerciseAttemptRequest,
    GenerateLessonRequest,
    KnowledgeState,
    KnowledgeStateCorrection,
    KnowledgeStateProposal,
    KnowledgeStateProposalStatus,
    LearningError,
    LearningMission,
    LearningMissionCreateRequest,
    LearningPath,
    LearningRecord,
    LearningRecordCreateRequest,
    ProposeKnowledgeStateUpdateRequest,
    ShortLesson,
    TeachingPlan,
)
from science_companion.learning import LearningPathService, LearningService, TeachingService
from science_companion.learning.adapters import LearningError as LearningAdapterError

router = APIRouter(prefix="/learning", tags=["learning"])


def _get_learning_service(request: Request) -> LearningService:
    service: LearningService | None = getattr(request.app.state, "learning_service", None)
    if service is None:
        raise RuntimeError("LearningService not attached to application state.")
    return service


LearningServiceDep = Annotated[LearningService, Depends(_get_learning_service)]


def _get_teaching_service(request: Request) -> TeachingService:
    service: TeachingService | None = getattr(request.app.state, "teaching_service", None)
    if service is None:
        raise RuntimeError("TeachingService not attached to application state.")
    return service


TeachingServiceDep = Annotated[TeachingService, Depends(_get_teaching_service)]


def _get_learning_path_service(request: Request) -> LearningPathService:
    service: LearningPathService | None = getattr(
        request.app.state, "learning_path_service", None
    )
    if service is None:
        raise RuntimeError("LearningPathService not attached to application state.")
    return service


LearningPathServiceDep = Annotated[LearningPathService, Depends(_get_learning_path_service)]


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
        raise _error(status.HTTP_404_NOT_FOUND, "mission_not_found", str(exc)) from exc


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
        raise _error(status.HTTP_404_NOT_FOUND, "mission_not_found", str(exc)) from exc


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
        raise _error(status.HTTP_404_NOT_FOUND, "run_not_found", str(exc)) from exc


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
        raise _error(status.HTTP_400_BAD_REQUEST, "answer_failed", str(exc)) from exc


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
        raise _error(status.HTTP_400_BAD_REQUEST, "complete_failed", str(exc)) from exc


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
        raise _error(status.HTTP_404_NOT_FOUND, "result_not_found", str(exc)) from exc


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
        raise _error(status.HTTP_404_NOT_FOUND, "mission_not_found", str(exc)) from exc


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
        raise _error(status.HTTP_400_BAD_REQUEST, "correction_failed", str(exc)) from exc


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
        raise _error(status.HTTP_404_NOT_FOUND, "plan_failed", str(exc)) from exc


@router.get(
    "/plans/{plan_id}/lessons",
    response_model=list[ShortLesson],
    responses={
        status.HTTP_404_NOT_FOUND: {"model": LearningError},
    },
)
async def list_lessons(
    teaching_service: TeachingServiceDep,
    subject: SubjectDep,
    plan_id: str,
) -> list[ShortLesson]:
    """List short lessons for a teaching plan."""
    try:
        return teaching_service.list_lessons_for_plan(subject.account_id, plan_id)
    except LearningAdapterError as exc:
        raise _error(status.HTTP_404_NOT_FOUND, "plan_not_found", str(exc)) from exc


@router.post(
    "/plans/{plan_id}/lessons",
    response_model=ShortLesson,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": LearningError},
        status.HTTP_400_BAD_REQUEST: {"model": LearningError},
    },
)
async def generate_short_lesson(
    teaching_service: TeachingServiceDep,
    subject: SubjectDep,
    plan_id: str,
    request: GenerateLessonRequest | None = None,
) -> ShortLesson:
    """Generate a short lesson from a teaching plan."""
    try:
        return teaching_service.generate_short_lesson(
            subject.account_id, plan_id, request=request or GenerateLessonRequest()
        )
    except LearningAdapterError as exc:
        raise _error(status.HTTP_404_NOT_FOUND, "plan_not_found", str(exc)) from exc


@router.get(
    "/lessons/{lesson_id}",
    response_model=ShortLesson,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": LearningError},
    },
)
async def get_lesson(
    teaching_service: TeachingServiceDep,
    subject: SubjectDep,
    lesson_id: str,
) -> ShortLesson:
    """Get a short lesson by ID."""
    try:
        return teaching_service.get_lesson(subject.account_id, lesson_id)
    except LearningAdapterError as exc:
        raise _error(status.HTTP_404_NOT_FOUND, "lesson_not_found", str(exc)) from exc


@router.post(
    "/lessons/{lesson_id}/exercises/{exercise_id}/attempts",
    response_model=ExerciseAttempt,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": LearningError},
        status.HTTP_400_BAD_REQUEST: {"model": LearningError},
    },
)
async def submit_exercise_attempt(
    teaching_service: TeachingServiceDep,
    subject: SubjectDep,
    lesson_id: str,
    exercise_id: str,
    request: ExerciseAttemptRequest,
) -> ExerciseAttempt:
    """Submit an attempt for a retrieval exercise."""
    try:
        return teaching_service.submit_exercise_attempt(
            subject.account_id, lesson_id, exercise_id, request.response_text
        )
    except LearningAdapterError as exc:
        raise _error(status.HTTP_400_BAD_REQUEST, "attempt_failed", str(exc)) from exc


@router.post(
    "/missions/{mission_id}/learning-records",
    response_model=LearningRecord,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": LearningError},
        status.HTTP_400_BAD_REQUEST: {"model": LearningError},
    },
)
async def create_learning_record(
    path_service: LearningPathServiceDep,
    subject: SubjectDep,
    mission_id: str,
    request: LearningRecordCreateRequest,
) -> LearningRecord:
    """Record a qualified learning evidence item."""
    try:
        return path_service.record_learning_record(
            subject.account_id, mission_id, request
        )
    except LearningAdapterError as exc:
        raise _error(status.HTTP_404_NOT_FOUND, "mission_not_found", str(exc)) from exc


@router.get(
    "/missions/{mission_id}/learning-records",
    response_model=list[LearningRecord],
)
async def list_learning_records(
    path_service: LearningPathServiceDep,
    subject: SubjectDep,
    mission_id: str,
    concept_id: str | None = Query(default=None),
) -> list[LearningRecord]:
    """List learning records for a mission."""
    try:
        return path_service.list_learning_records(
            subject.account_id, mission_id, concept_id
        )
    except LearningAdapterError as exc:
        raise _error(status.HTTP_404_NOT_FOUND, "mission_not_found", str(exc)) from exc


@router.post(
    "/missions/{mission_id}/knowledge-state-proposals",
    response_model=KnowledgeStateProposal,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": LearningError},
        status.HTTP_400_BAD_REQUEST: {"model": LearningError},
    },
)
async def propose_knowledge_state_update(
    path_service: LearningPathServiceDep,
    subject: SubjectDep,
    mission_id: str,
    request: ProposeKnowledgeStateUpdateRequest,
) -> KnowledgeStateProposal:
    """Propose a knowledge-state update from learning records."""
    try:
        return path_service.propose_knowledge_state_update(
            subject.account_id, mission_id, request
        )
    except LearningAdapterError as exc:
        raise _error(status.HTTP_400_BAD_REQUEST, "proposal_failed", str(exc)) from exc


@router.get(
    "/missions/{mission_id}/knowledge-state-proposals",
    response_model=list[KnowledgeStateProposal],
)
async def list_knowledge_state_proposals(
    path_service: LearningPathServiceDep,
    subject: SubjectDep,
    mission_id: str,
    concept_id: str | None = Query(default=None),
    proposal_status: KnowledgeStateProposalStatus | None = Query(default=None),
) -> list[KnowledgeStateProposal]:
    """List knowledge-state proposals for a mission."""
    try:
        return path_service.list_knowledge_state_proposals(
            subject.account_id, mission_id, concept_id, proposal_status
        )
    except LearningAdapterError as exc:
        raise _error(status.HTTP_404_NOT_FOUND, "mission_not_found", str(exc)) from exc


@router.post(
    "/knowledge-state-proposals/{proposal_id}/decide",
    response_model=KnowledgeStateProposal,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": LearningError},
        status.HTTP_400_BAD_REQUEST: {"model": LearningError},
    },
)
async def decide_knowledge_state_proposal(
    path_service: LearningPathServiceDep,
    subject: SubjectDep,
    proposal_id: str,
    request: DecideKnowledgeStateProposalRequest,
) -> KnowledgeStateProposal:
    """Accept, reject or modify a knowledge-state proposal."""
    try:
        return path_service.decide_knowledge_state_proposal(
            subject.account_id, proposal_id, request
        )
    except LearningAdapterError as exc:
        raise _error(status.HTTP_400_BAD_REQUEST, "decision_failed", str(exc)) from exc


@router.get(
    "/missions/{mission_id}/learning-path",
    response_model=LearningPath,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": LearningError},
    },
)
async def get_learning_path(
    path_service: LearningPathServiceDep,
    subject: SubjectDep,
    mission_id: str,
) -> LearningPath:
    """Get the current learning path for a mission."""
    try:
        return path_service.get_learning_path(subject.account_id, mission_id)
    except LearningAdapterError as exc:
        raise _error(status.HTTP_404_NOT_FOUND, "mission_not_found", str(exc)) from exc


