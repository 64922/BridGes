"""Authentication API routes and session dependency.

Routes implement registration, login, logout, recovery, and session introspection.
The session cookie is HttpOnly, SameSite=lax, and secure only in production so that
local development can exercise the flow over plain HTTP.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Protocol

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response, status

from bridges.contracts.identity import (
    Account,
    AccountProfileUpdate,
    AccountRegistration,
    AuthError,
    AuthResponse,
    DeviceAccountsResponse,
    DeviceLogoutResponse,
    DeviceReauthenticationRequest,
    DeviceSwitchRequest,
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
DEVICE_COOKIE_NAME = "bridges_device"

router = APIRouter(prefix="/auth", tags=["authentication"])


def _get_identity_service(request: Request) -> IdentityService:
    service: IdentityService | None = getattr(request.app.state, "identity_service", None)
    if service is None:
        raise RuntimeError("IdentityService not attached to application state.")
    return service


IdentityServiceDep = Annotated[IdentityService, Depends(_get_identity_service)]


def _cookie_secure(request: Request) -> bool:
    """决定会话/设备 Cookie 的 Secure 标志。

    显式配置 ``BRIDGES_SESSION_COOKIE_SECURE`` 时强制启用（覆盖反代 TLS
    终止导致 scheme 为 http 的场景）；未配置时跟随请求协议（HTTPS 才标记）。
    """
    settings = getattr(request.app.state, "settings", None)
    if settings is not None and settings.session_cookie_secure:
        return True
    return request.url.scheme == "https"


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


def _clear_session_cookie(response: Response, *, secure: bool = False) -> None:
    # 删除 Cookie 时保持与设置一致的属性（HttpOnly/Secure/路径），保证
    # 无论当初在 HTTP 还是 HTTPS 下签发都能被浏览器按同一键清除。
    response.delete_cookie(
        key=SESSION_COOKIE_NAME, path="/", httponly=True, secure=secure, samesite="lax"
    )


def _set_device_cookie(response: Response, token: str, *, secure: bool) -> None:
    response.set_cookie(
        key=DEVICE_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=30 * 24 * 60 * 60,
        path="/",
    )


def _clear_device_cookie(response: Response, *, secure: bool = False) -> None:
    response.delete_cookie(
        key=DEVICE_COOKIE_NAME, path="/", httponly=True, secure=secure, samesite="lax"
    )


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


def _clear_device_site_data(response: Response) -> None:
    """Clear all browser-local account state when every device account exits."""
    response.headers["Clear-Site-Data"] = '"cache", "storage"'
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
    "device_account": status.HTTP_403_FORBIDDEN,
    "reauth_required": status.HTTP_403_FORBIDDEN,
    "device_operation_stale": status.HTTP_409_CONFLICT,
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


class RecentAuthService(Protocol):
    """敏感操作路由需要的近期认证判定（IdentityService 的窄接口）。"""

    def requires_recent_auth(self, session_id: str) -> bool: ...


def _get_recent_auth_service(request: Request) -> RecentAuthService:
    service: RecentAuthService | None = getattr(
        request.app.state, "identity_service", None
    )
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "identity_unavailable",
                "message": "身份服务未启用，当前实例拒绝敏感设置操作。",
            },
        )
    return service


def _require_recent_auth_dependency(request: Request, subject: SubjectDep) -> None:
    """FastAPI dependency：敏感路由声明即门控（近期密码确认，统一入口）。"""
    try:
        needs_reauthentication = _get_recent_auth_service(
            request
        ).requires_recent_auth(subject.session_id)
    except IdentityError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "unauthenticated", "message": str(exc)},
        ) from exc
    if needs_reauthentication:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "reauth_required",
                "message": "此页面包含敏感设置，请重新输入当前账户密码。",
            },
        )


RecentAuthRequired = Annotated[None, Depends(_require_recent_auth_dependency)]


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
        response, result.session_token, secure=_cookie_secure(request)
    )
    device_token, _ = service.ensure_device(
        request.cookies.get(DEVICE_COOKIE_NAME), result.session.id
    )
    _set_device_cookie(response, device_token, secure=_cookie_secure(request))
    return result.public_response()


def _claim_device_operation(
    service: IdentityService, device_token: str, operation_id: int | None
) -> None:
    """Prevent an older browser response from changing the session cookie."""
    if operation_id is not None and not service.claim_device_operation(
        device_token, operation_id
    ):
        raise _auth_error(
            status.HTTP_409_CONFLICT,
            "device_operation_stale",
            "设备账户操作已过期。",
        )


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
        response, result.session_token, secure=_cookie_secure(request)
    )
    device_token, _ = service.ensure_device(
        request.cookies.get(DEVICE_COOKIE_NAME), result.session.id
    )
    _set_device_cookie(response, device_token, secure=_cookie_secure(request))
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
    _clear_session_cookie(response, secure=_cookie_secure(request))
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


@router.delete(
    "/profile/avatar",
    response_model=Account,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_404_NOT_FOUND: {"model": AuthError},
    },
)
async def remove_avatar(
    service: IdentityServiceDep,
    subject: SubjectDep,
) -> Account:
    """Remove the current owner's uploaded avatar and fall back to static choice."""
    try:
        return service.remove_avatar(subject.account_id)
    except IdentityError as exc:
        raise _auth_error(
            status.HTTP_404_NOT_FOUND,
            "avatar_not_found",
            str(exc),
        ) from exc


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
        response, result.session_token, secure=_cookie_secure(request)
    )
    device_token, _ = service.ensure_device(
        request.cookies.get(DEVICE_COOKIE_NAME), result.session.id
    )
    _set_device_cookie(response, device_token, secure=_cookie_secure(request))
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


def _device_accounts_response(
    service: IdentityService, device_token: str, current_session_id: str | None
) -> DeviceAccountsResponse:
    accounts = (
        service.list_device_accounts(device_token, current_session_id)
        if current_session_id is not None
        else []
    )
    current_account = None
    if current_session_id is not None:
        current_session = service.get_session(current_session_id)
        if current_session is not None:
            current_account = service.get_account(current_session.account_id)
    return DeviceAccountsResponse(
        accounts=accounts,
        current_account=current_account,
        current_session_id=current_session_id,
    )


@router.get(
    "/device/accounts",
    response_model=DeviceAccountsResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_403_FORBIDDEN: {"model": AuthError},
    },
)
async def list_device_accounts(
    request: Request,
    response: Response,
    service: IdentityServiceDep,
    subject: SubjectDep,
) -> DeviceAccountsResponse:
    """List the accounts explicitly authenticated in this browser device."""
    device_token, _ = service.ensure_device(
        request.cookies.get(DEVICE_COOKIE_NAME), subject.session_id
    )
    _set_device_cookie(response, device_token, secure=_cookie_secure(request))
    return _device_accounts_response(service, device_token, subject.session_id)


@router.post(
    "/device/accounts/add",
    response_model=DeviceAccountsResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_403_FORBIDDEN: {"model": AuthError},
    },
)
async def add_device_account(
    request: Request,
    response: Response,
    credentials: LoginCredential,
    service: IdentityServiceDep,
    subject: SubjectDep,
    operation_id: int | None = Header(default=None, alias="X-Bridges-Account-Operation"),
) -> DeviceAccountsResponse:
    """Authenticate a new account, preserve existing device sessions, and activate it."""
    device_token, _ = service.ensure_device(
        request.cookies.get(DEVICE_COOKIE_NAME), subject.session_id
    )
    _claim_device_operation(service, device_token, operation_id)
    try:
        result = service.authenticate(credentials)
        service.attach_session_to_device(device_token, result.session.id)
    except IdentityError as exc:
        raise _identity_error(exc, "device_account_add_failed") from exc
    _set_session_cookie(response, result.session_token, secure=_cookie_secure(request))
    _set_device_cookie(response, device_token, secure=_cookie_secure(request))
    _clear_account_site_data(response)
    return _device_accounts_response(service, device_token, result.session.id)


@router.post(
    "/device/switch",
    response_model=DeviceAccountsResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_403_FORBIDDEN: {"model": AuthError},
    },
)
async def switch_device_account(
    request: Request,
    response: Response,
    selection: DeviceSwitchRequest,
    service: IdentityServiceDep,
    subject: SubjectDep,
    operation_id: int | None = Header(default=None, alias="X-Bridges-Account-Operation"),
) -> DeviceAccountsResponse:
    """Switch only to an active session previously registered by this device."""
    device_token = request.cookies.get(DEVICE_COOKIE_NAME)
    if not device_token:
        raise _auth_error(
            status.HTTP_403_FORBIDDEN,
            "device_account_unavailable",
            "该账户无法在此设备上切换，请重新登录。",
        )
    if not service.is_session_registered_on_device(device_token, subject.session_id):
        raise _auth_error(
            status.HTTP_403_FORBIDDEN,
            "device_account_unavailable",
            "该设备账户不可用，请重新登录。",
        )
    try:
        if operation_id is not None:
            _claim_device_operation(service, device_token, operation_id)
        result = service.activate_device_session(device_token, selection.session_id)
    except IdentityError as exc:
        raise _auth_error(
            status.HTTP_403_FORBIDDEN,
            "device_account_unavailable",
            "该设备账户不可用，请重新登录。",
        ) from exc
    _set_session_cookie(response, result.session_token, secure=_cookie_secure(request))
    _clear_account_site_data(response)
    return _device_accounts_response(service, device_token, result.session.id)


@router.post(
    "/device/reauthenticate",
    response_model=DeviceAccountsResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_403_FORBIDDEN: {"model": AuthError},
    },
)
async def reauthenticate_device_account(
    request: Request,
    response: Response,
    confirmation: DeviceReauthenticationRequest,
    service: IdentityServiceDep,
    subject: SubjectDep,
    operation_id: int | None = Header(default=None, alias="X-Bridges-Account-Operation"),
) -> DeviceAccountsResponse:
    """Restore a stale device account using that account's password only."""
    device_token = request.cookies.get(DEVICE_COOKIE_NAME)
    if not device_token:
        raise _auth_error(
            status.HTTP_403_FORBIDDEN,
            "device_account_unavailable",
            "该账户无法在此设备上切换，请重新登录。",
        )
    if not service.is_session_registered_on_device(device_token, subject.session_id):
        raise _auth_error(
            status.HTTP_401_UNAUTHORIZED,
            "device_reauthentication_failed",
            "账户信息或密码不正确。",
        )
    _claim_device_operation(service, device_token, operation_id)
    try:
        result = service.reauthenticate_device_session(
            device_token,
            confirmation.session_id,
            confirmation.password.get_secret_value(),
        )
    except IdentityError as exc:
        raise _auth_error(
            status.HTTP_401_UNAUTHORIZED,
            "device_reauthentication_failed",
            "账户信息或密码不正确。",
        ) from exc
    _set_session_cookie(response, result.session_token, secure=_cookie_secure(request))
    _clear_account_site_data(response)
    return _device_accounts_response(service, device_token, result.session.id)


@router.get(
    "/device/accounts/{session_id}/avatar",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_403_FORBIDDEN: {"model": AuthError},
        status.HTTP_404_NOT_FOUND: {"model": AuthError},
    },
)
async def get_device_account_avatar(
    request: Request,
    response: Response,
    session_id: str,
    service: IdentityServiceDep,
    subject: SubjectDep,
) -> Response:
    """按设备作用域读取已注册账户的头像。

    切换器需要展示设备上其他账户的上传头像；普通头像端点只读取当前
    账户，本端点要求目标会话已注册在设备 Cookie 上，且不暴露宿主路径。
    """
    device_token = request.cookies.get(DEVICE_COOKIE_NAME)
    if not device_token or not service.is_session_registered_on_device(
        device_token, session_id
    ):
        raise _auth_error(
            status.HTTP_403_FORBIDDEN,
            "device_account_unavailable",
            "该设备账户不可用，请重新登录。",
        )
    session = service.get_session(session_id)
    if session is None:
        raise _auth_error(
            status.HTTP_404_NOT_FOUND,
            "avatar_not_found",
            "头像不存在或没有访问权限。",
        )
    try:
        avatar = service.get_avatar(session.account_id)
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
    "/device/logout",
    response_model=DeviceLogoutResponse,
    responses={status.HTTP_401_UNAUTHORIZED: {"model": AuthError}},
)
async def logout_device_account(
    request: Request,
    response: Response,
    service: IdentityServiceDep,
    subject: SubjectDep,
    operation_id: int | None = Header(default=None, alias="X-Bridges-Account-Operation"),
) -> DeviceLogoutResponse:
    """Revoke only the active account session and fall back safely if possible."""
    device_token = request.cookies.get(DEVICE_COOKIE_NAME)
    device_bound = bool(
        device_token
        and service.is_session_registered_on_device(device_token, subject.session_id)
    )
    if device_bound and device_token:
        _claim_device_operation(service, device_token, operation_id)
    service.revoke_session(subject.session_id)
    fallback = (
        service.activate_next_device_session(device_token, subject.session_id)
        if device_bound and device_token
        else None
    )
    if fallback is None:
        _clear_session_cookie(response, secure=_cookie_secure(request))
        _clear_account_site_data(response)
        return DeviceLogoutResponse()
    _set_session_cookie(response, fallback.session_token, secure=_cookie_secure(request))
    _clear_account_site_data(response)
    return DeviceLogoutResponse(
        current_account=fallback.account,
        current_session_id=fallback.session.id,
    )


@router.post(
    "/device/logout-all",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={status.HTTP_401_UNAUTHORIZED: {"model": AuthError}},
)
async def logout_all_device_accounts(
    request: Request,
    response: Response,
    service: IdentityServiceDep,
    subject: SubjectDep,
    operation_id: int | None = Header(default=None, alias="X-Bridges-Account-Operation"),
) -> None:
    """Revoke every session registered to this browser and return to login."""
    device_token = request.cookies.get(DEVICE_COOKIE_NAME)
    if device_token and service.is_session_registered_on_device(
        device_token, subject.session_id
    ):
        _claim_device_operation(service, device_token, operation_id)
        service.revoke_device_sessions(device_token)
    # The device cookie is an opaque client-held locator. Revoke the current
    # authenticated session even if it is stale, forged, or missing.
    service.revoke_session(subject.session_id)
    _clear_session_cookie(response, secure=_cookie_secure(request))
    _clear_device_cookie(response, secure=_cookie_secure(request))
    _clear_device_site_data(response)
