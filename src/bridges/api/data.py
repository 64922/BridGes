"""数据生命周期 API 路由（Issue 37）：导出、删除、备份与恢复。

导出执行、账户删除、备份创建与恢复全部属于敏感操作（ADR-0018），要求
当前账户近期密码确认（复用 ``RecentAuthRequired`` 敏感门）；导出预览
只返回类别条数与预计大小（不含数据正文），不设门。任何响应、审计与
日志都不包含凭据、会话令牌、数据正文或备份口令；恢复成功后当前会话
随身份替换自动失效，由前端引导重新登录。
"""

from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)

from bridges.api.auth import (
    SubjectDep,
    _clear_device_cookie,
    _clear_session_cookie,
    _cookie_secure,
)
from bridges.api.credentials import RecentAuthRequired
from bridges.chat.service import ChatService
from bridges.contracts.identity import AuthError
from bridges.contracts.lifecycle import (
    AccountDeletionProjection,
    DataLifecycleError,
    DeleteAccountRequest,
    ExportPreviewProjection,
    RestorePreview,
)
from bridges.lifecycle.backup import BackupService
from bridges.lifecycle.deletion import DeletionService
from bridges.lifecycle.exports import ExportService

router = APIRouter(prefix="/data", tags=["data"])

UploadFileFile = Annotated[UploadFile, File()]


def _get_lifecycle_service(request: Request, name: str) -> Any:
    service = getattr(request.app.state, name, None)
    if service is None:
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "data_lifecycle_unavailable",
            "数据生命周期服务未启用，当前实例拒绝数据操作。",
        )
    return service


def _get_export_service(request: Request) -> ExportService:
    return cast(ExportService, _get_lifecycle_service(request, "export_service"))


def _get_deletion_service(request: Request) -> DeletionService:
    return cast(DeletionService, _get_lifecycle_service(request, "deletion_service"))


def _get_backup_service(request: Request) -> BackupService:
    return cast(BackupService, _get_lifecycle_service(request, "backup_service"))


def _get_chat_service(request: Request) -> ChatService | None:
    return getattr(request.app.state, "chat_service", None)


ExportServiceDep = Annotated[ExportService, Depends(_get_export_service)]
DeletionServiceDep = Annotated[DeletionService, Depends(_get_deletion_service)]
BackupServiceDep = Annotated[BackupService, Depends(_get_backup_service)]


def _error(http_status: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=http_status,
        detail={"error": code, "message": message},
    )


def _from_error(exc: DataLifecycleError) -> HTTPException:
    return _error(exc.status_code, exc.code, exc.message)


# ----------------------------------------------------------------------
# 账户数据导出
# ----------------------------------------------------------------------


@router.get(
    "/export-preview",
    response_model=ExportPreviewProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": AuthError},
    },
)
async def get_export_preview(
    service: ExportServiceDep,
    subject: SubjectDep,
) -> ExportPreviewProjection:
    """返回当前账户导出范围与预计大小（确认前可见，不含数据正文）。"""
    return service.preview(subject.account_id)


@router.post(
    "/export",
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": AuthError},
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_403_FORBIDDEN: {"model": AuthError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": AuthError},
    },
)
async def export_account_data(
    _recent_auth: RecentAuthRequired,
    service: ExportServiceDep,
    subject: SubjectDep,
) -> Response:
    """生成当前账户导出 JSON 并以下载附件返回（敏感操作，需再认证）。"""
    try:
        filename, payload = service.export_data(subject.account_id)
    except DataLifecycleError as exc:
        raise _from_error(exc) from exc
    return Response(
        content=payload,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(payload)),
        },
    )


# ----------------------------------------------------------------------
# 账户删除（强确认 + 再认证；部分失败可重试不宣称成功）
# ----------------------------------------------------------------------


@router.post(
    "/account/delete",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": AuthError},
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_403_FORBIDDEN: {"model": AuthError},
        status.HTTP_409_CONFLICT: {"model": AuthError},
        status.HTTP_502_BAD_GATEWAY: {"model": AuthError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": AuthError},
    },
)
async def delete_account(
    _recent_auth: RecentAuthRequired,
    body: DeleteAccountRequest,
    request: Request,
    response: Response,
    service: DeletionServiceDep,
    subject: SubjectDep,
) -> Response:
    """删除当前账户：先停止流式生成，再执行删除编排（事务+文件清理）。

    成功后该账户全部会话已随身份记录移除，本响应同时清除浏览器会话与
    设备 Cookie；任何部分失败都不会宣称成功，返回可重试的中文错误。
    """
    if body.confirmation != "删除":
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "confirmation_required",
            "请输入「删除」确认删除当前账户及其全部本地数据。",
        )
    chat_service = _get_chat_service(request)
    if chat_service is not None:
        chat_service.stop_account_generations(subject.account_id)
    try:
        service.delete_account(subject.account_id)
    except DataLifecycleError as exc:
        raise _from_error(exc) from exc
    _clear_session_cookie(response, secure=_cookie_secure(request))
    _clear_device_cookie(response, secure=_cookie_secure(request))
    response.headers["Clear-Site-Data"] = '"cache", "storage"'
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get(
    "/account/delete-status",
    response_model=AccountDeletionProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_404_NOT_FOUND: {"model": AuthError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": AuthError},
    },
)
async def get_deletion_status(
    service: DeletionServiceDep,
    subject: SubjectDep,
) -> AccountDeletionProjection:
    """返回当前账户删除状态（失败时含可重试信息）。"""
    projection = service.get_status(subject.account_id)
    if projection is None:
        raise _error(
            status.HTTP_404_NOT_FOUND,
            "deletion_not_found",
            "当前账户没有删除记录。",
        )
    return projection


@router.post(
    "/account/delete/retry",
    response_model=AccountDeletionProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_403_FORBIDDEN: {"model": AuthError},
        status.HTTP_404_NOT_FOUND: {"model": AuthError},
        status.HTTP_409_CONFLICT: {"model": AuthError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": AuthError},
    },
)
async def retry_deletion(
    _recent_auth: RecentAuthRequired,
    service: DeletionServiceDep,
    subject: SubjectDep,
) -> AccountDeletionProjection:
    """重试当前账户失败的删除清理（敏感操作，需再认证）。"""
    projection = service.get_status(subject.account_id)
    if projection is None:
        raise _error(
            status.HTTP_404_NOT_FOUND,
            "deletion_not_found",
            "当前账户没有删除记录。",
        )
    try:
        return service.retry_deletion(projection.deletion_id)
    except DataLifecycleError as exc:
        raise _from_error(exc) from exc


# ----------------------------------------------------------------------
# 本地加密备份与恢复（系统级操作，影响全部账户）
# ----------------------------------------------------------------------


@router.post(
    "/backups",
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": AuthError},
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_403_FORBIDDEN: {"model": AuthError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": AuthError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": AuthError},
    },
)
async def create_backup(
    _recent_auth: RecentAuthRequired,
    passphrase: Annotated[str, Form(min_length=1, max_length=256)],
    service: BackupServiceDep,
) -> Response:
    """创建本地加密备份并以下载附件返回（敏感操作，需再认证）。"""
    try:
        filename, container = service.create_backup(passphrase)
    except DataLifecycleError as exc:
        raise _from_error(exc) from exc
    return Response(
        content=container,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(container)),
        },
    )


@router.post(
    "/restore",
    response_model=RestorePreview,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": AuthError},
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_403_FORBIDDEN: {"model": AuthError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": AuthError},
        status.HTTP_502_BAD_GATEWAY: {"model": AuthError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": AuthError},
    },
)
async def restore_backup(
    _recent_auth: RecentAuthRequired,
    passphrase: Annotated[str, Form(min_length=1, max_length=256)],
    confirmation: Annotated[str, Form(min_length=1, max_length=64)],
    file: Annotated[UploadFileFile, File()],
    request: Request,
    response: Response,
    service: BackupServiceDep,
) -> RestorePreview:
    """预检并恢复备份；失败（损坏/篡改/口令/空间/版本）不破坏现有数据。

    成功后身份账户数据已被替换（全部会话失效）：响应同步清除浏览器
    会话与设备 Cookie（与账户删除一致），前端引导用户以备份账户重新
    登录；外部凭据已清除，需重新配置。
    """
    backup_bytes = await file.read()
    try:
        preview = service.restore_backup(
            passphrase,
            backup_bytes,
            confirmation=confirmation,
        )
    except DataLifecycleError as exc:
        raise _from_error(exc) from exc
    if preview.ok:
        _clear_session_cookie(response, secure=_cookie_secure(request))
        _clear_device_cookie(response, secure=_cookie_secure(request))
        response.headers["Clear-Site-Data"] = '"cache", "storage"'
    return preview
