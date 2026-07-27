"""Expression task API routes (T025).

Routes let users create an expression task brief, generate a fact-lock-bound
expression draft from a claim graph, and inspect the draft's claim/citation/fact
lock bindings.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from science_companion.api.auth import SubjectDep
from science_companion.contracts.expression import (
    ExpressionDraft,
    ExpressionDraftRequest,
    ExpressionDraftResult,
    ExpressionError,
)
from science_companion.expression import ExpressionService, ExpressionServiceError

router = APIRouter(prefix="/expression", tags=["expression"])


def _get_expression_service(request: Request) -> ExpressionService:
    service: ExpressionService | None = getattr(
        request.app.state, "expression_service", None
    )
    if service is None:
        raise RuntimeError("ExpressionService not attached to application state.")
    return service


ExpressionServiceDep = Annotated[ExpressionService, Depends(_get_expression_service)]


def _expression_error(status_code: int, error: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=ExpressionError(error=error, message=message).model_dump(),
    )


@router.post(
    "/drafts",
    response_model=ExpressionDraftResult,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ExpressionError},
        status.HTTP_404_NOT_FOUND: {"model": ExpressionError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ExpressionError},
    },
)
async def create_expression_draft(
    service: ExpressionServiceDep,
    subject: SubjectDep,
    request: ExpressionDraftRequest,
) -> ExpressionDraftResult:
    """Generate a fact-lock-bound expression draft from a brief and claim graph."""
    try:
        return service.create_draft_and_store(subject, request)
    except ExpressionServiceError as exc:
        msg = str(exc)
        if "不存在" in msg or "访问权限" in msg:
            raise _expression_error(
                status.HTTP_404_NOT_FOUND, "draft_source_not_found", msg
            ) from exc
        raise _expression_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "draft_generation_failed", msg
        ) from exc


@router.get(
    "/drafts/{draft_id}",
    response_model=ExpressionDraft,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ExpressionError},
        status.HTTP_404_NOT_FOUND: {"model": ExpressionError},
    },
)
async def get_expression_draft(
    service: ExpressionServiceDep,
    subject: SubjectDep,
    draft_id: str,
) -> ExpressionDraft:
    """Retrieve a previously generated expression draft."""
    try:
        return service.get_draft(subject.account_id, draft_id)
    except ExpressionServiceError as exc:
        raise _expression_error(
            status.HTTP_404_NOT_FOUND, "draft_not_found", str(exc)
        ) from exc


@router.get(
    "/drafts/{draft_id}/inspector",
    response_model=ExpressionDraft,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ExpressionError},
        status.HTTP_404_NOT_FOUND: {"model": ExpressionError},
    },
)
async def inspect_expression_draft(
    service: ExpressionServiceDep,
    subject: SubjectDep,
    draft_id: str,
) -> ExpressionDraft:
    """Inspect an expression draft: claims, citations and fact locks per span.

    The inspector is the citation/profile-checker seam referenced by T025.
    """
    try:
        return service.get_draft(subject.account_id, draft_id)
    except ExpressionServiceError as exc:
        raise _expression_error(
            status.HTTP_404_NOT_FOUND, "draft_not_found", str(exc)
        ) from exc
