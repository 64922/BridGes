"""Authentication API routes and session dependency.

Routes implement registration, login, logout, recovery, and session introspection.
The session cookie is HttpOnly, SameSite=lax, and secure only in production so that
local development can exercise the flow over plain HTTP.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status

from bridges.contracts.identity import (
    Account,
    AccountProfileUpdate,
    AccountRegistration,
    AuthError,
    AuthResponse,
    KeySettingsProjection,
    KeySettingsStatus,
    LoginCredential,
    ReauthenticationRequest,
    RecoveryRequest,
    RecoveryReset,
    SessionResponse,
    SubjectContext,
)
from bridges.contracts.institution import MembershipContext
from bridges.identity import MAX_AVATAR_BYTES, IdentityError, IdentityService
from bridges.scope import ScopeEnforcer

if TYPE_CHECKING:
    from bridges.institution import InstitutionService

SESSION_COOKIE_NAME = "bridges_session"

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


def _clear_account_site_data(response: Response) -> None:
    """Clear cached account views without deleting device-wide origin storage."""
    response.headers["Clear-Site-Data"] = '"cache"'
    response.headers["Cache-Control"] = "no-store"


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


def _auth_error(
    status_code: int,
    error: str,
    message: str,
    *,
    headers: dict[str, str] | None = None,
) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=AuthError(error=error, message=message).model_dump(),
        headers=headers,
    )


def _expired_session_headers() -> dict[str, str]:
    """Clear the exact session cookie on a 401 response without a JS race."""
    response = Response()
    _clear_session_cookie(response)
    return {
        "Set-Cookie": response.headers["set-cookie"],
        "Cache-Control": "no-store",
    }


_IDENTITY_ERROR_STATUS = {
    "validation": status.HTTP_400_BAD_REQUEST,
    "conflict": status.HTTP_409_CONFLICT,
    "credentials": status.HTTP_401_UNAUTHORIZED,
    "session": status.HTTP_401_UNAUTHORIZED,
    "too_large": status.HTTP_413_CONTENT_TOO_LARGE,
}


def _identity_error(exc: IdentityError, error: str) -> HTTPException:
    """Map a domain error to a uniform HTTP error without message parsing."""
    return _auth_error(
        _IDENTITY_ERROR_STATUS.get(exc.code, status.HTTP_400_BAD_REQUEST),
        error,
        str(exc),
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
            headers=_expired_session_headers(),
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
        raise _identity_error(exc, "registration_failed") from exc

    _set_session_cookie(
        response, result.session_token, secure=request.url.scheme == "https"
    )
    return result.public_response()


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
        # Uniform error for unknown identifier or wrong password.
        raise _identity_error(exc, "invalid_credentials") from exc

    _set_session_cookie(
        response, result.session_token, secure=request.url.scheme == "https"
    )
    return result.public_response()


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def logout(
    request: Request,
    response: Response,
    service: IdentityServiceDep,
) -> None:
    """Revoke any resolvable session and always clear the browser cookie."""
    _revoke_existing_session_if_present(service, request)
    _clear_session_cookie(response)
    _clear_account_site_data(response)


@router.get(
    "/profile",
    response_model=Account,
    responses={status.HTTP_401_UNAUTHORIZED: {"model": AuthError}},
)
async def get_profile(
    service: IdentityServiceDep,
    subject: SubjectDep,
) -> Account:
    """Return only the current account's mutable profile projection."""
    account = service.get_account(subject.account_id)
    if account is None:
        raise _auth_error(
            status.HTTP_401_UNAUTHORIZED,
            "unauthenticated",
            "会话已失效。",
        )
    return account


@router.patch(
    "/profile",
    response_model=Account,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": AuthError},
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_409_CONFLICT: {"model": AuthError},
    },
)
async def update_profile(
    update: AccountProfileUpdate,
    service: IdentityServiceDep,
    subject: SubjectDep,
) -> Account:
    """Update username/avatar choice for the authenticated owner only."""
    try:
        return service.update_profile(subject.account_id, update)
    except IdentityError as exc:
        raise _identity_error(exc, "profile_update_failed") from exc


@router.put(
    "/profile/avatar",
    response_model=Account,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": AuthError},
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_413_CONTENT_TOO_LARGE: {"model": AuthError},
    },
)
async def upload_avatar(
    request: Request,
    service: IdentityServiceDep,
    subject: SubjectDep,
) -> Account:
    """Store a validated current-account avatar without exposing host paths."""
    declared_length = request.headers.get("content-length")
    if declared_length is not None:
        try:
            if int(declared_length) > MAX_AVATAR_BYTES:
                raise _auth_error(
                    status.HTTP_413_CONTENT_TOO_LARGE,
                    "avatar_too_large",
                    "头像不能超过 2 MiB。",
                )
        except ValueError as exc:
            raise _auth_error(
                status.HTTP_400_BAD_REQUEST,
                "avatar_upload_failed",
                "头像请求大小无效，请重新选择文件。",
            ) from exc

    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > MAX_AVATAR_BYTES:
            raise _auth_error(
                status.HTTP_413_CONTENT_TOO_LARGE,
                "avatar_too_large",
                "头像不能超过 2 MiB。",
            )

    try:
        return service.store_avatar(
            subject.account_id,
            bytes(content),
            request.headers.get("content-type", ""),
        )
    except IdentityError as exc:
        raise _identity_error(exc, "avatar_upload_failed") from exc


@router.get(
    "/profile/avatar",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_404_NOT_FOUND: {"model": AuthError},
    },
)
async def get_avatar(
    service: IdentityServiceDep,
    subject: SubjectDep,
) -> Response:
    """Read the current owner's avatar through the authorized API seam."""
    try:
        avatar = service.get_avatar(subject.account_id)
    except IdentityError as exc:
        raise _auth_error(
            status.HTTP_404_NOT_FOUND,
            "avatar_not_found",
            str(exc),
        ) from exc
    return Response(
        content=avatar.content,
        media_type=avatar.media_type,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/reauthenticate",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
    },
)
async def reauthenticate(
    confirmation: ReauthenticationRequest,
    service: IdentityServiceDep,
    subject: SubjectDep,
) -> None:
    """Confirm the current password before sensitive settings access."""
    try:
        service.reauthenticate(
            subject.account_id,
            subject.session_id,
            confirmation.password.get_secret_value(),
        )
    except IdentityError as exc:
        raise _identity_error(exc, "reauthentication_failed") from exc


@router.get(
    "/key-settings",
    response_model=KeySettingsProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_403_FORBIDDEN: {"model": AuthError},
    },
)
async def get_key_settings(
    service: IdentityServiceDep,
    subject: SubjectDep,
) -> KeySettingsProjection:
    """Return the truthful pre-Issue-10 key state after recent reauthentication."""
    try:
        needs_reauthentication = service.requires_recent_auth(subject.session_id)
    except IdentityError as exc:
        raise _identity_error(exc, "unauthenticated") from exc
    if needs_reauthentication:
        raise _auth_error(
            status.HTTP_403_FORBIDDEN,
            "reauth_required",
            "此页面包含敏感设置，请重新输入当前账户密码。",
        )
    return KeySettingsProjection(
        status=KeySettingsStatus.UNCONFIGURED,
        configured=False,
        message="尚未配置百炼密钥。",
        next_step="完成密钥接入后，可在本页录入并验证；现在请勿在聊天中粘贴密钥。",
    )


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

    The response is identical whether the QQ mailbox is registered or not, to
    prevent account enumeration.
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
        raise _identity_error(exc, "invalid_recovery") from exc

    _set_session_cookie(
        response, result.session_token, secure=request.url.scheme == "https"
    )
    return result.public_response()


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
