"""账户级模型凭据与能力探测 API 路由。

密钥保存、替换、删除与重试探测均要求当前账户近期密码确认（Issue 09 的
再认证合同）：未确认时返回 403 ``reauth_required``。任何响应、审计与
日志都不包含 Key 正文；凭据存储不可用时返回 503，绝不静默降级。
"""

from __future__ import annotations

from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, HTTPException, Request, status

from bridges.api.auth import SubjectDep
from bridges.contracts.credentials import (
    KeySaveRequest,
    KeySettingsProjection,
)
from bridges.contracts.identity import AuthError
from bridges.credentials.service import KeyCredentialError, KeyCredentialService
from bridges.credentials.store import CredentialStoreError
from bridges.identity import IdentityError

router = APIRouter(prefix="/auth", tags=["credentials"])


def _get_credential_service(request: Request) -> KeyCredentialService:
    service: KeyCredentialService | None = getattr(
        request.app.state, "credential_service", None
    )
    if service is None:
        raise RuntimeError("KeyCredentialService not attached to application state.")
    return service


CredentialServiceDep = Annotated[
    KeyCredentialService, Depends(_get_credential_service)
]


def _error(http_status: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=http_status,
        detail={"error": code, "message": message},
    )


class RecentAuthService(Protocol):
    """敏感操作路由需要的近期认证判定（IdentityService 的窄接口）。"""

    def requires_recent_auth(self, session_id: str) -> bool: ...


def _get_identity_service(request: Request) -> RecentAuthService:
    service: RecentAuthService | None = getattr(
        request.app.state, "identity_service", None
    )
    if service is None:
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "identity_unavailable",
            "身份服务未启用，当前实例拒绝敏感设置操作。",
        )
    return service


def _require_recent_auth(
    identity_service: RecentAuthService, session_id: str
) -> None:
    """强制当前账户近期密码确认（敏感设置门）。"""
    try:
        needs_reauthentication = identity_service.requires_recent_auth(session_id)
    except IdentityError as exc:
        raise _error(
            status.HTTP_401_UNAUTHORIZED, "unauthenticated", str(exc)
        ) from exc
    if needs_reauthentication:
        raise _error(
            status.HTTP_403_FORBIDDEN,
            "reauth_required",
            "此页面包含敏感设置，请重新输入当前账户密码。",
        )


def _require_recent_auth_dependency(request: Request, subject: SubjectDep) -> None:
    """FastAPI dependency：敏感路由声明即门控（统一入口，删除逐路由内联）。"""
    _require_recent_auth(_get_identity_service(request), subject.session_id)


RecentAuthRequired = Annotated[None, Depends(_require_recent_auth_dependency)]


@router.get(
    "/key-settings",
    response_model=KeySettingsProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_403_FORBIDDEN: {"model": AuthError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": AuthError},
    },
)
async def get_key_settings(
    _recent_auth: RecentAuthRequired,
    service: CredentialServiceDep,
    subject: SubjectDep,
) -> KeySettingsProjection:
    """返回当前账户密钥配置与固定能力探测状态（不含秘密正文）。"""
    try:
        return service.get_projection(subject.account_id)
    except CredentialStoreError as exc:
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE, "credential_store_unavailable", str(exc)
        ) from exc


@router.put(
    "/key-settings",
    response_model=KeySettingsProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_403_FORBIDDEN: {"model": AuthError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": AuthError},
    },
)
async def save_key_settings(
    _recent_auth: RecentAuthRequired,
    body: KeySaveRequest,
    service: CredentialServiceDep,
    subject: SubjectDep,
) -> KeySettingsProjection:
    """保存或替换当前账户百炼 Key，并触发固定能力真实探测。"""
    try:
        return service.save(
            subject.account_id, body.key, session_id=subject.session_id
        )
    except CredentialStoreError as exc:
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE, "credential_store_unavailable", str(exc)
        ) from exc


@router.delete(
    "/key-settings",
    response_model=KeySettingsProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_403_FORBIDDEN: {"model": AuthError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": AuthError},
    },
)
async def delete_key_settings(
    _recent_auth: RecentAuthRequired,
    service: CredentialServiceDep,
    subject: SubjectDep,
) -> KeySettingsProjection:
    """删除当前账户百炼 Key 并复位探测状态。"""
    try:
        return service.delete(subject.account_id, session_id=subject.session_id)
    except CredentialStoreError as exc:
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE, "credential_store_unavailable", str(exc)
        ) from exc


@router.post(
    "/key-settings/probes",
    response_model=KeySettingsProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_403_FORBIDDEN: {"model": AuthError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": AuthError},
    },
)
async def probe_all_capabilities(
    _recent_auth: RecentAuthRequired,
    service: CredentialServiceDep,
    subject: SubjectDep,
) -> KeySettingsProjection:
    """对固定能力矩阵重新执行全量真实探测。"""
    try:
        service.schedule_probes(subject.account_id, session_id=subject.session_id)
        return service.get_projection(subject.account_id)
    except CredentialStoreError as exc:
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE, "credential_store_unavailable", str(exc)
        ) from exc


@router.post(
    "/key-settings/probes/{capability_id}/retry",
    response_model=KeySettingsProjection,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": AuthError},
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_403_FORBIDDEN: {"model": AuthError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": AuthError},
    },
)
async def retry_capability_probe(
    capability_id: str,
    _recent_auth: RecentAuthRequired,
    service: CredentialServiceDep,
    subject: SubjectDep,
) -> KeySettingsProjection:
    """对单项能力执行同模型重试（无备用模型、无隐藏降级）。"""
    try:
        service.schedule_retry(
            subject.account_id, capability_id, session_id=subject.session_id
        )
        return service.get_projection(subject.account_id)
    except KeyCredentialError as exc:
        raise _error(status.HTTP_400_BAD_REQUEST, "key_not_configured", str(exc)) from exc
    except CredentialStoreError as exc:
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE, "credential_store_unavailable", str(exc)
        ) from exc
