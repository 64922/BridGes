"""Scope isolation API routes.

Routes expose the compiled scope context and background-task envelope validation
so that API consumers, workers and future data channels can share the same scope
interpreter without duplicating authorization logic.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from science_companion.api.auth import SubjectDep
from science_companion.contracts.scope import (
    BackgroundTaskEnvelope,
    ScopeAction,
    ScopeEnvelope,
    ScopeIsolationError,
    ScopeViolationReport,
)
from science_companion.scope import ScopeEnforcer

router = APIRouter(prefix="/scope", tags=["scope"])


def _get_scope_enforcer(request: Request) -> ScopeEnforcer:
    enforcer: ScopeEnforcer | None = getattr(request.app.state, "scope_enforcer", None)
    if enforcer is None:
        raise RuntimeError("ScopeEnforcer not attached to application state.")
    return enforcer


ScopeEnforcerDep = Annotated[ScopeEnforcer, Depends(_get_scope_enforcer)]


def _scope_error(status_code: int, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"error": "scope_isolation", "message": message},
    )


@router.get(
    "/context",
    response_model=dict[str, Any],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": dict[str, Any]},
    },
)
async def get_scope_context(
    subject: SubjectDep,
    enforcer: ScopeEnforcerDep,
) -> dict[str, Any]:
    """Return the compiled scope envelope and RLS context for the current request.

    This endpoint is read-only and is used by storage, index and cache layers to
    align their own scope checks with the API's authorization interpreter.
    """
    scope = enforcer.compile_scope(subject)
    rls_context = enforcer.set_rls_context(subject, scope)
    return {
        "scope_envelope": scope.model_dump(),
        "rls_context": {
            "subject_account_id": rls_context.subject.account_id,
            "subject_session_id": rls_context.subject.session_id,
            "object_domain": rls_context.scope_envelope.object_domain.value,
            "authorization_version": rls_context.scope_envelope.authorization_version,
            "key_epoch": rls_context.scope_envelope.key_epoch,
            "set_at": rls_context.set_at.isoformat(),
        },
    }


@router.post(
    "/validate-task",
    response_model=dict[str, Any],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": dict[str, Any]},
        status.HTTP_403_FORBIDDEN: {"model": dict[str, Any]},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": dict[str, Any]},
    },
)
async def validate_background_task(
    envelope: BackgroundTaskEnvelope,
    subject: SubjectDep,
    enforcer: ScopeEnforcerDep,
) -> dict[str, Any]:
    """Validate that a background task carries all required scope fields.

    Missing subject, object domain, authorization version or key epoch causes a
    deterministic failure closure rather than running with guessed context.
    """
    if envelope.subject.account_id != subject.account_id:
        raise _scope_error(
            status.HTTP_403_FORBIDDEN,
            "后台任务主体与当前会话不一致。",
        )
    try:
        enforcer.validate_background_task(envelope)
    except ScopeIsolationError as exc:
        raise _scope_error(
            status.HTTP_403_FORBIDDEN,
            str(exc),
        ) from exc

    # Also verify the task can compile a scope envelope for audit.
    scope = enforcer.compile_scope(
        envelope.subject,
        tenant_id=None,
        project_id=envelope.project_id,
        object_domain=envelope.object_domain,
        purpose=envelope.purpose,
        authorization_version=envelope.authorization_version,
        key_epoch=envelope.key_epoch,
    )
    return {
        "valid": True,
        "scope_envelope": scope.model_dump(),
    }


@router.post(
    "/report-violation",
    response_model=ScopeViolationReport,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": dict[str, Any]},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": dict[str, Any]},
    },
)
async def report_scope_violation(
    scope: ScopeEnvelope,
    action: ScopeAction,
    reason: str,
    subject: SubjectDep,
    enforcer: ScopeEnforcerDep,
) -> ScopeViolationReport:
    """Accept an audit-safe scope violation report.

    The report intentionally does not include private content or keys.
    """
    return enforcer.report_violation(subject, action, scope, reason)
