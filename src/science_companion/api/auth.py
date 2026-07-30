"""Authentication API routes and session dependency.

Routes implement registration, login, logout, recovery, and session introspection.
The session cookie is HttpOnly, SameSite=lax, and secure only in production so that
local development can exercise the flow over plain HTTP.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status

from science_companion.contracts.identity import (
    AccountRegistration,
    AuthError,
    AuthResponse,
    LoginCredential,
    RecoveryRequest,
    RecoveryReset,
    SessionResponse,
    SubjectContext,
)
from science_companion.contracts.institution import MembershipContext
from science_companion.identity import IdentityError, IdentityService
from science_companion.scope import ScopeEnforcer

if TYPE_CHECKING:
    from science_companion.institution import InstitutionService

SESSION_COOKIE_NAME = "science_companion_session"

router = APIRouter(prefix="/auth", tags=["authentication"])


def _get_identity_service(request: Request) -> IdentityService:
    service: IdentityService | None = getattr(request.app.state, "identity_service", None)
    if service is None:
        raise RuntimeError("IdentityService not attached to application state.")
    return service


IdentityServiceDep = Annotated[IdentityService, Depends(_get_identity_service)]


def _set_session_cookie(
    response: Response, token: str, *, secure: bool, max_age: int = 8 * 60 * 60
) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=max_age,
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")


def _clear_site_data(response: Response) -> None:
    """Tell the browser to drop cached state when switching accounts.

    T007 uses this to ensure that pages, suggestions, notifications and task
    state from a previous account do not leak into a new session.
    """
    response.headers["Clear-Site-Data"] = '"cache"'


def _revoke_existing_session_if_present(
    service: IdentityService,
    request: Request,
) -> None:
    """Revoke the old session cookie when a new authentication starts.

    This prevents stale sessions from remaining active across account switches.
    """
    old_token: str | None = request.cookies.get(SESSION_COOKIE_NAME)
    if not old_token:
        return
    try:
        resolved = service.resolve_session(old_token)
    except IdentityError:
        return
    service.revoke_session(resolved.subject.session_id)


def _auth_error(status_code: int, error: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=AuthError(error=error, message=message).model_dump(),
    )


async def require_subject(
    request: Request,
    service: IdentityServiceDep,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
) -> SubjectContext:
    """FastAPI dependency that resolves the current subject from the session cookie.

    Attaches the resolved SubjectContext, compiled scope envelope and RLS context
    to request.state so that route handlers and downstream dependencies can reuse
    them without re-resolving.
    """
    if session_token is None:
        raise _auth_error(
            status.HTTP_401_UNAUTHORIZED,
            "unauthenticated",
            "请先登录。",
        )

    try:
        resolved = service.resolve_session(session_token)
    except IdentityError as exc:
        raise _auth_error(
            status.HTTP_401_UNAUTHORIZED,
            "unauthenticated",
            str(exc),
        ) from exc

    subject = resolved.subject

    # T037: populate institution memberships so downstream routes and services
    # can evaluate institution-scoped access without re-querying identity.
    institution_service: InstitutionService | None = getattr(
        request.app.state, "institution_service", None
    )
    if institution_service is not None:
        memberships = institution_service.list_memberships_for_account(subject.account_id)
        subject = subject.model_copy(
            update={
                "memberships": [
                    MembershipContext(
                        institution_id=m.institution_id, role=m.role
                    )
                    for m in memberships
                ]
            }
        )

    request.state.subject = subject

    # Compile the base scope envelope and RLS context for every authenticated
    # request. Later routes can narrow the scope with project/object domain.
    enforcer: ScopeEnforcer | None = getattr(request.app.state, "scope_enforcer", None)
    if enforcer is not None:
        scope = enforcer.compile_scope(subject)
        request.state.scope_envelope = scope
        request.state.rls_context = enforcer.set_rls_context(subject, scope)

    return subject


SubjectDep = Annotated[SubjectContext, Depends(require_subject)]


@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": AuthError},
        status.HTTP_409_CONFLICT: {"model": AuthError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": AuthError},
    },
)
async def register(
    request: Request,
    response: Response,
    service: IdentityServiceDep,
    registration: AccountRegistration,
) -> AuthResponse:
    """Register a new account and establish a session."""
    _revoke_existing_session_if_present(service, request)
    _clear_site_data(response)

    try:
        result = service.register(registration)
    except IdentityError as exc:
        # Duplicate email or missing terms; do not distinguish duplicate email.
        raise _auth_error(
            status.HTTP_409_CONFLICT,
            "registration_failed",
            str(exc),
        ) from exc

    _set_session_cookie(
        response, result.session_token, secure=request.url.scheme == "https"
    )
    return result


@router.post(
    "/login",
    response_model=AuthResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": AuthError},
    },
)
async def login(
    request: Request,
    response: Response,
    service: IdentityServiceDep,
    credentials: LoginCredential,
) -> AuthResponse:
    """Authenticate and establish a new session."""
    _revoke_existing_session_if_present(service, request)
    _clear_site_data(response)

    try:
        result = service.authenticate(credentials)
    except IdentityError as exc:
        # Uniform error for unknown email or wrong password.
        raise _auth_error(
            status.HTTP_401_UNAUTHORIZED,
            "invalid_credentials",
            str(exc),
        ) from exc

    _set_session_cookie(
        response, result.session_token, secure=request.url.scheme == "https"
    )
    return result


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def logout(
    response: Response,
    service: IdentityServiceDep,
    subject: SubjectDep,
) -> None:
    """Revoke the current session and clear the cookie."""
    service.revoke_session(subject.session_id)
    _clear_session_cookie(response)


@router.post(
    "/recover",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": AuthError},
    },
)
async def recover(
    service: IdentityServiceDep,
    request: RecoveryRequest,
) -> dict[str, str]:
    """Request a credential recovery flow.

    The response is identical whether the email is registered or not, to prevent
    account enumeration.
    """
    service.request_recovery(request)
    return {"status": "accepted"}


@router.post(
    "/recover/reset",
    response_model=AuthResponse,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": AuthError},
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": AuthError},
    },
)
async def recover_reset(
    request: Request,
    response: Response,
    service: IdentityServiceDep,
    reset: RecoveryReset,
) -> AuthResponse:
    """Reset password using a recovery token and establish a new session."""
    _revoke_existing_session_if_present(service, request)
    _clear_site_data(response)

    try:
        result = service.reset_password_with_recovery(reset)
    except IdentityError as exc:
        raise _auth_error(
            status.HTTP_401_UNAUTHORIZED,
            "invalid_recovery",
            str(exc),
        ) from exc

    _set_session_cookie(
        response, result.session_token, secure=request.url.scheme == "https"
    )
    return result


@router.get(
    "/session",
    response_model=SessionResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
    },
)
async def get_session(
    service: IdentityServiceDep,
    subject: SubjectDep,
) -> SessionResponse:
    """Return the current session and subject context."""
    session = service.get_session(subject.session_id)
    if session is None:
        raise _auth_error(
            status.HTTP_401_UNAUTHORIZED,
            "unauthenticated",
            "会话已失效。",
        )
    account = service.get_account(subject.account_id)
    if account is None:
        raise _auth_error(
            status.HTTP_401_UNAUTHORIZED,
            "unauthenticated",
            "会话已失效。",
        )
    return SessionResponse(account=account, session=session, subject=subject)
