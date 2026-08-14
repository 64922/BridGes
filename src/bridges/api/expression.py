"""Expression task API routes (T025).

Read-only routes let users inspect previously generated expression drafts and
evaluate release eligibility. All legacy write routes are retired and return
``410 Gone`` during the compatibility window, as required by ADR-0026.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from bridges.api.auth import SubjectDep
from bridges.contracts.expression import (
    ExpressionDraft,
    ExpressionError,
    ReleaseGateResult,
)
from bridges.expression import ExpressionService, ExpressionServiceError
from bridges.retirement import raise_retired_capability

router = APIRouter(prefix="/expression", tags=["expression"])

_REPLACEMENT_PATH = "/chat"
_RETIRED_MESSAGE = "旧表达写能力已退役，请在学习模式聊天中继续。"
_ERROR_CODE = "legacy_expression_retired"


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
    status_code=status.HTTP_410_GONE,
    responses={status.HTTP_410_GONE: {"model": ExpressionError}},
)
async def create_expression_draft(
    request: Request,
    subject: SubjectDep,
) -> None:
    """Create a new expression draft. Retired; returns 410 during compatibility window."""
    raise_retired_capability(
        request,
        endpoint="expression.drafts.create",
        error=_ERROR_CODE,
        replacement_path=_REPLACEMENT_PATH,
        message=_RETIRED_MESSAGE,
    )


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


@router.post(
    "/drafts/{draft_id}/style-diagnostic",
    status_code=status.HTTP_410_GONE,
    responses={status.HTTP_410_GONE: {"model": ExpressionError}},
)
async def run_style_diagnostic(
    request: Request,
    subject: SubjectDep,
    draft_id: str,
) -> None:
    """Run style diagnostic on a draft. Retired; returns 410."""
    raise_retired_capability(
        request,
        endpoint="expression.drafts.style_diagnostic",
        error=_ERROR_CODE,
        replacement_path=_REPLACEMENT_PATH,
        message=_RETIRED_MESSAGE,
    )


@router.post(
    "/drafts/{draft_id}/patches/{patch_id}/apply",
    status_code=status.HTTP_410_GONE,
    responses={status.HTTP_410_GONE: {"model": ExpressionError}},
)
async def apply_revision_patch(
    request: Request,
    subject: SubjectDep,
    draft_id: str,
    patch_id: str,
) -> None:
    """Apply a revision patch. Retired; returns 410."""
    raise_retired_capability(
        request,
        endpoint="expression.drafts.apply_patch",
        error=_ERROR_CODE,
        replacement_path=_REPLACEMENT_PATH,
        message=_RETIRED_MESSAGE,
    )


@router.post(
    "/drafts/{draft_id}/feedback",
    status_code=status.HTTP_410_GONE,
    responses={status.HTTP_410_GONE: {"model": ExpressionError}},
)
async def submit_expression_feedback(
    request: Request,
    subject: SubjectDep,
    draft_id: str,
) -> None:
    """Submit feedback for a draft. Retired; returns 410."""
    raise_retired_capability(
        request,
        endpoint="expression.drafts.feedback",
        error=_ERROR_CODE,
        replacement_path=_REPLACEMENT_PATH,
        message=_RETIRED_MESSAGE,
    )


@router.post(
    "/drafts/{draft_id}/approve",
    status_code=status.HTTP_410_GONE,
    responses={status.HTTP_410_GONE: {"model": ExpressionError}},
)
async def approve_expression_artifact(
    request: Request,
    subject: SubjectDep,
    draft_id: str,
) -> None:
    """Approve a draft artifact. Retired; returns 410."""
    raise_retired_capability(
        request,
        endpoint="expression.drafts.approve",
        error=_ERROR_CODE,
        replacement_path=_REPLACEMENT_PATH,
        message=_RETIRED_MESSAGE,
    )


@router.get(
    "/drafts/{draft_id}/release-eligibility",
    response_model=ReleaseGateResult,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ExpressionError},
        status.HTTP_404_NOT_FOUND: {"model": ExpressionError},
    },
)
async def get_release_eligibility(
    service: ExpressionServiceDep,
    subject: SubjectDep,
    draft_id: str,
    run_id: str | None = None,
) -> ReleaseGateResult:
    """Evaluate whether an expression artifact is eligible for publication.

    Eligibility depends on the expression gate, human approval, linked workflow
    run success, open human todos, and active upstream objects.
    """
    try:
        return service.evaluate_release_eligibility(subject, draft_id, run_id=run_id)
    except ExpressionServiceError as exc:
        raise _expression_error(
            status.HTTP_404_NOT_FOUND, "draft_not_found", str(exc)
        ) from exc


@router.post(
    "/drafts/{draft_id}/publish",
    status_code=status.HTTP_410_GONE,
    responses={status.HTTP_410_GONE: {"model": ExpressionError}},
)
async def publish_expression_artifact(
    request: Request,
    subject: SubjectDep,
    draft_id: str,
) -> None:
    """Publish an expression artifact. Retired; returns 410."""
    raise_retired_capability(
        request,
        endpoint="expression.drafts.publish",
        error=_ERROR_CODE,
        replacement_path=_REPLACEMENT_PATH,
        message=_RETIRED_MESSAGE,
    )


@router.post(
    "/drafts/compare",
    status_code=status.HTTP_410_GONE,
    responses={status.HTTP_410_GONE: {"model": ExpressionError}},
)
async def compare_expression_versions(
    request: Request,
    subject: SubjectDep,
) -> None:
    """Compare expression draft versions. Retired; returns 410."""
    raise_retired_capability(
        request,
        endpoint="expression.drafts.compare",
        error=_ERROR_CODE,
        replacement_path=_REPLACEMENT_PATH,
        message=_RETIRED_MESSAGE,
    )
