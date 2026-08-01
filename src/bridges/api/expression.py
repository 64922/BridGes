"""Expression task API routes (T025).

Routes let users create an expression task brief, generate a fact-lock-bound
expression draft from a claim graph, and inspect the draft's claim/citation/fact
lock bindings.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from bridges.api.auth import SubjectDep
from bridges.contracts.expression import (
    ApplyRevisionPatchRequest,
    ApplyRevisionPatchResult,
    ApproveArtifactRequest,
    ApproveArtifactResult,
    CompareVersionsRequest,
    CompareVersionsResult,
    ExpressionDraft,
    ExpressionDraftRequest,
    ExpressionDraftResult,
    ExpressionError,
    PublishArtifactRequest,
    PublishArtifactResult,
    ReleaseGateResult,
    StyleDiagnosticRequest,
    StyleDiagnosticResult,
    SubmitExpressionFeedbackRequest,
    SubmitExpressionFeedbackResult,
)
from bridges.expression import ExpressionService, ExpressionServiceError

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


@router.post(
    "/drafts/{draft_id}/style-diagnostic",
    response_model=StyleDiagnosticResult,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ExpressionError},
        status.HTTP_404_NOT_FOUND: {"model": ExpressionError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ExpressionError},
    },
)
async def run_style_diagnostic(
    service: ExpressionServiceDep,
    subject: SubjectDep,
    draft_id: str,
    request: StyleDiagnosticRequest,
) -> StyleDiagnosticResult:
    """Run or re-run the Chinese human-flavor diagnostic on a draft."""
    try:
        return service.run_style_diagnostic(subject, request)
    except ExpressionServiceError as exc:
        msg = str(exc)
        if "不存在" in msg or "访问权限" in msg:
            raise _expression_error(
                status.HTTP_404_NOT_FOUND, "draft_not_found", msg
            ) from exc
        raise _expression_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "style_diagnostic_failed", msg
        ) from exc


@router.post(
    "/drafts/{draft_id}/patches/{patch_id}/apply",
    response_model=ApplyRevisionPatchResult,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ExpressionError},
        status.HTTP_404_NOT_FOUND: {"model": ExpressionError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ExpressionError},
    },
)
async def apply_revision_patch(
    service: ExpressionServiceDep,
    subject: SubjectDep,
    draft_id: str,
    patch_id: str,
    request: ApplyRevisionPatchRequest,
) -> ApplyRevisionPatchResult:
    """Accept, reject or rewrite a single revision patch."""
    try:
        return service.apply_revision_patch(subject, draft_id, patch_id, request)
    except ExpressionServiceError as exc:
        msg = str(exc)
        if "不存在" in msg or "访问权限" in msg:
            raise _expression_error(
                status.HTTP_404_NOT_FOUND, "draft_or_patch_not_found", msg
            ) from exc
        raise _expression_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "patch_application_failed", msg
        ) from exc


@router.post(
    "/drafts/{draft_id}/feedback",
    response_model=SubmitExpressionFeedbackResult,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ExpressionError},
        status.HTTP_404_NOT_FOUND: {"model": ExpressionError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ExpressionError},
    },
)
async def submit_expression_feedback(
    service: ExpressionServiceDep,
    subject: SubjectDep,
    draft_id: str,
    request: SubmitExpressionFeedbackRequest,
) -> SubmitExpressionFeedbackResult:
    """Submit user feedback for an expression draft.

    Feedback is routed to the current version, a candidate preference, a learning
    record, or a fact review according to its content.
    """
    try:
        return service.submit_user_feedback(subject, draft_id, request)
    except ExpressionServiceError as exc:
        msg = str(exc)
        if "不存在" in msg or "访问权限" in msg:
            raise _expression_error(
                status.HTTP_404_NOT_FOUND, "draft_not_found", msg
            ) from exc
        raise _expression_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "feedback_submission_failed", msg
        ) from exc


@router.post(
    "/drafts/{draft_id}/approve",
    response_model=ApproveArtifactResult,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ExpressionError},
        status.HTTP_404_NOT_FOUND: {"model": ExpressionError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ExpressionError},
    },
)
async def approve_expression_artifact(
    service: ExpressionServiceDep,
    subject: SubjectDep,
    draft_id: str,
    request: ApproveArtifactRequest,
) -> ApproveArtifactResult:
    """Approve, reject or request changes for an expression artifact.

    Approval separates workflow success from scientific trust status and is
    required before publication.
    """
    try:
        return service.approve_artifact(subject, draft_id, request)
    except ExpressionServiceError as exc:
        msg = str(exc)
        if "不存在" in msg or "访问权限" in msg:
            raise _expression_error(
                status.HTTP_404_NOT_FOUND, "draft_not_found", msg
            ) from exc
        raise _expression_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "artifact_approval_failed", msg
        ) from exc


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
    response_model=PublishArtifactResult,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ExpressionError},
        status.HTTP_404_NOT_FOUND: {"model": ExpressionError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ExpressionError},
    },
)
async def publish_expression_artifact(
    service: ExpressionServiceDep,
    subject: SubjectDep,
    draft_id: str,
    request: PublishArtifactRequest,
) -> PublishArtifactResult:
    """Publish an expression artifact when it passes the release gate.

    The publish event is bound to the exact draft version, the authorizing
    account, the release gate result and the approval decision.
    """
    try:
        return service.publish_artifact(subject, draft_id, request)
    except ExpressionServiceError as exc:
        msg = str(exc)
        if "不存在" in msg or "访问权限" in msg:
            raise _expression_error(
                status.HTTP_404_NOT_FOUND, "draft_not_found", msg
            ) from exc
        raise _expression_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "publish_failed", msg
        ) from exc


@router.post(
    "/drafts/compare",
    response_model=CompareVersionsResult,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ExpressionError},
        status.HTTP_404_NOT_FOUND: {"model": ExpressionError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ExpressionError},
    },
)
async def compare_expression_versions(
    service: ExpressionServiceDep,
    subject: SubjectDep,
    request: CompareVersionsRequest,
) -> CompareVersionsResult:
    """Compare two expression drafts and report semantic differences.

    The comparison highlights changes to fact locks, claims, citations, wording
    strength, argument structure, span text, model locks, applied patches and
    artifact trust status, plus the release eligibility of each version.
    """
    try:
        return service.compare_versions(subject, request)
    except ExpressionServiceError as exc:
        msg = str(exc)
        if "不存在" in msg or "访问权限" in msg:
            raise _expression_error(
                status.HTTP_404_NOT_FOUND, "draft_not_found", msg
            ) from exc
        raise _expression_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "version_comparison_failed", msg
        ) from exc
