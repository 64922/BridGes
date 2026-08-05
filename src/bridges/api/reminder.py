"""QQ SMTP 任务提醒 API 路由（Issue 33）。

授权码保存/删除属于敏感设置：要求当前账户近期密码确认（复用
``RecentAuthRequired`` 敏感门，与密钥设置同一合同）。任何响应、审计与
日志都不包含授权码正文或邮件正文；收件人/发件人固定为当前账户 QQ
邮箱，跨账户访问一律 404。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from bridges.api.auth import SubjectDep
from bridges.api.credentials import RecentAuthRequired
from bridges.contracts.identity import AuthError
from bridges.contracts.reminder import (
    ParsedReminderPreview,
    ReminderCreateRequest,
    ReminderDeliveryProjection,
    ReminderError,
    ReminderProjection,
    ReminderSettingsProjection,
    ReminderSettingsUpdateRequest,
    ReminderUpdateRequest,
    SmtpCodeSaveRequest,
    SmtpSettingsProjection,
)
from bridges.credentials.store import CredentialStoreError
from bridges.reminder.service import ReminderService

router = APIRouter(prefix="/reminders", tags=["reminders"])


def _get_reminder_service(request: Request) -> ReminderService:
    service: ReminderService | None = getattr(
        request.app.state, "reminder_service", None
    )
    if service is None:
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "reminder_unavailable",
            "任务提醒服务未启用，当前实例拒绝提醒操作。",
        )
    return service


ReminderServiceDep = Annotated[ReminderService, Depends(_get_reminder_service)]


def _error(http_status: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=http_status,
        detail={"error": code, "message": message},
    )


def _from_error(exc: ReminderError) -> HTTPException:
    return _error(exc.status_code, exc.code, exc.message)


# ----------------------------------------------------------------------
# SMTP 配置与验证（授权码保存/删除需近期密码确认）
# ----------------------------------------------------------------------


@router.get(
    "/smtp",
    response_model=SmtpSettingsProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": AuthError},
    },
)
async def get_smtp_settings(
    service: ReminderServiceDep,
    subject: SubjectDep,
) -> SmtpSettingsProjection:
    """返回当前账户 SMTP 配置与验证状态（不含授权码正文）。"""
    try:
        return service.get_smtp_settings(subject.account_id)
    except ReminderError as exc:
        raise _from_error(exc) from exc


@router.put(
    "/smtp",
    response_model=SmtpSettingsProjection,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": AuthError},
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_403_FORBIDDEN: {"model": AuthError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": AuthError},
    },
)
async def save_smtp_code(
    _recent_auth: RecentAuthRequired,
    body: SmtpCodeSaveRequest,
    service: ReminderServiceDep,
    subject: SubjectDep,
) -> SmtpSettingsProjection:
    """保存或替换当前账户 QQ 邮箱授权码并触发自发自收验证。"""
    try:
        return service.save_smtp_code(
            subject.account_id,
            body.authorization_code.get_secret_value(),
            session_id=subject.session_id,
        )
    except ReminderError as exc:
        raise _from_error(exc) from exc


@router.post(
    "/smtp/verify",
    response_model=SmtpSettingsProjection,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": AuthError},
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_403_FORBIDDEN: {"model": AuthError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": AuthError},
    },
)
async def verify_smtp(
    service: ReminderServiceDep,
    subject: SubjectDep,
) -> SmtpSettingsProjection:
    """用已保存的授权码重新执行自发自收验证。"""
    try:
        return service.verify_smtp_now(
            subject.account_id, session_id=subject.session_id
        )
    except ReminderError as exc:
        raise _from_error(exc) from exc
    except CredentialStoreError as exc:
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE, "credential_store_unavailable", str(exc)
        ) from exc


@router.delete(
    "/smtp",
    response_model=SmtpSettingsProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_403_FORBIDDEN: {"model": AuthError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": AuthError},
    },
)
async def delete_smtp_code(
    _recent_auth: RecentAuthRequired,
    service: ReminderServiceDep,
    subject: SubjectDep,
) -> SmtpSettingsProjection:
    """删除当前账户授权码并复位验证状态（暂停依赖它的启用提醒）。"""
    try:
        return service.delete_smtp_code(
            subject.account_id, session_id=subject.session_id
        )
    except ReminderError as exc:
        raise _from_error(exc) from exc


# ----------------------------------------------------------------------
# 账户设置（时区）
# ----------------------------------------------------------------------


@router.get(
    "/settings",
    response_model=ReminderSettingsProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": AuthError},
    },
)
async def get_reminder_settings(
    service: ReminderServiceDep,
    subject: SubjectDep,
) -> ReminderSettingsProjection:
    """返回账户提醒设置（时区）。"""
    return service.get_settings(subject.account_id)


@router.put(
    "/settings",
    response_model=ReminderSettingsProjection,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": AuthError},
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
    },
)
async def update_reminder_settings(
    body: ReminderSettingsUpdateRequest,
    service: ReminderServiceDep,
    subject: SubjectDep,
) -> ReminderSettingsProjection:
    """更新账户提醒时区。"""
    try:
        return service.update_settings(subject.account_id, body)
    except ReminderError as exc:
        raise _from_error(exc) from exc


# ----------------------------------------------------------------------
# 解析预览与提醒 CRUD
# ----------------------------------------------------------------------


@router.post(
    "/parse",
    response_model=ParsedReminderPreview,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": AuthError},
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": AuthError},
    },
)
async def parse_reminder(
    body: ReminderParseRequest,
    service: ReminderServiceDep,
    subject: SubjectDep,
) -> ParsedReminderPreview:
    """把自然语言解析为带时区结构化日程与邮件预览（不持久化）。"""
    try:
        return service.parse(
            subject.account_id,
            body.raw_text,
            timezone=body.timezone,
            use_profile=body.use_profile,
            session_id=subject.session_id,
        )
    except ReminderError as exc:
        raise _from_error(exc) from exc


@router.post(
    "",
    response_model=ReminderProjection,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": AuthError},
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_403_FORBIDDEN: {"model": AuthError},
        status.HTTP_404_NOT_FOUND: {"model": AuthError},
        status.HTTP_409_CONFLICT: {"model": AuthError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": AuthError},
    },
)
async def create_reminder(
    body: ReminderCreateRequest,
    service: ReminderServiceDep,
    subject: SubjectDep,
) -> ReminderProjection:
    """创建提醒（要求已验证 SMTP；预览内容经确定性复核后冻结）。"""
    try:
        return service.create(
            subject.account_id, body, session_id=subject.session_id
        )
    except ReminderError as exc:
        raise _from_error(exc) from exc


@router.get(
    "",
    response_model=list[ReminderProjection],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
    },
)
async def list_reminders(
    service: ReminderServiceDep,
    subject: SubjectDep,
) -> list[ReminderProjection]:
    """返回当前账户的全部提醒。"""
    return service.list_reminders(subject.account_id)


@router.get(
    "/{reminder_id}",
    response_model=ReminderProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_404_NOT_FOUND: {"model": AuthError},
    },
)
async def get_reminder(
    reminder_id: str,
    service: ReminderServiceDep,
    subject: SubjectDep,
) -> ReminderProjection:
    """返回单条提醒（跨账户 404）。"""
    try:
        return service.get_reminder(subject.account_id, reminder_id)
    except ReminderError as exc:
        raise _from_error(exc) from exc


@router.put(
    "/{reminder_id}",
    response_model=ReminderProjection,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": AuthError},
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_404_NOT_FOUND: {"model": AuthError},
        status.HTTP_409_CONFLICT: {"model": AuthError},
    },
)
async def update_reminder(
    reminder_id: str,
    body: ReminderUpdateRequest,
    service: ReminderServiceDep,
    subject: SubjectDep,
) -> ReminderProjection:
    """编辑提醒（重置日程与冻结快照，保留投递记录）。"""
    try:
        return service.update(
            subject.account_id, reminder_id, body, session_id=subject.session_id
        )
    except ReminderError as exc:
        raise _from_error(exc) from exc


@router.post(
    "/{reminder_id}/pause",
    response_model=ReminderProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_404_NOT_FOUND: {"model": AuthError},
        status.HTTP_409_CONFLICT: {"model": AuthError},
    },
)
async def pause_reminder(
    reminder_id: str,
    service: ReminderServiceDep,
    subject: SubjectDep,
) -> ReminderProjection:
    """暂停提醒。"""
    try:
        return service.pause(
            subject.account_id, reminder_id, session_id=subject.session_id
        )
    except ReminderError as exc:
        raise _from_error(exc) from exc


@router.post(
    "/{reminder_id}/resume",
    response_model=ReminderProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_404_NOT_FOUND: {"model": AuthError},
        status.HTTP_409_CONFLICT: {"model": AuthError},
    },
)
async def resume_reminder(
    reminder_id: str,
    service: ReminderServiceDep,
    subject: SubjectDep,
) -> ReminderProjection:
    """恢复已暂停的提醒（要求 SMTP 已验证）。"""
    try:
        return service.resume(
            subject.account_id, reminder_id, session_id=subject.session_id
        )
    except ReminderError as exc:
        raise _from_error(exc) from exc


@router.post(
    "/{reminder_id}/send-now",
    response_model=ReminderDeliveryProjection,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": AuthError},
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_404_NOT_FOUND: {"model": AuthError},
        status.HTTP_409_CONFLICT: {"model": AuthError},
        status.HTTP_502_BAD_GATEWAY: {"model": AuthError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": AuthError},
    },
)
async def send_reminder_now(
    reminder_id: str,
    service: ReminderServiceDep,
    subject: SubjectDep,
) -> ReminderDeliveryProjection:
    """手动补发：立即发送当前提醒内容（不改变既有日程）。"""
    try:
        return service.send_now(
            subject.account_id, reminder_id, session_id=subject.session_id
        )
    except ReminderError as exc:
        raise _from_error(exc) from exc


@router.delete(
    "/{reminder_id}",
    response_model=ReminderProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_404_NOT_FOUND: {"model": AuthError},
        status.HTTP_409_CONFLICT: {"model": AuthError},
    },
)
async def cancel_reminder(
    reminder_id: str,
    service: ReminderServiceDep,
    subject: SubjectDep,
) -> ReminderProjection:
    """取消提醒（投递记录保留）。"""
    try:
        return service.cancel(
            subject.account_id, reminder_id, session_id=subject.session_id
        )
    except ReminderError as exc:
        raise _from_error(exc) from exc


@router.get(
    "/{reminder_id}/deliveries",
    response_model=list[ReminderDeliveryProjection],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_404_NOT_FOUND: {"model": AuthError},
    },
)
async def list_reminder_deliveries(
    reminder_id: str,
    service: ReminderServiceDep,
    subject: SubjectDep,
) -> list[ReminderDeliveryProjection]:
    """返回一条提醒的投递记录（区分发送/失败/跳过/补发/手动重试）。"""
    try:
        return service.list_deliveries(subject.account_id, reminder_id)
    except ReminderError as exc:
        raise _from_error(exc) from exc


# ----------------------------------------------------------------------
# 解析请求契约（路由层专用：raw_text + 时区 + 画像开关）
# ----------------------------------------------------------------------


class ReminderParseRequest(BaseModel):
    """自然语言解析请求：只带原文、账户时区与画像开关。"""

    raw_text: str = Field(
        min_length=1, max_length=500, description="自然语言提醒描述。"
    )
    timezone: str = Field(
        min_length=1, max_length=64, description="账户时区标识（IANA）。"
    )
    use_profile: bool = Field(
        default=False, description="是否启用画像措辞适配（预览会披露使用类别）。"
    )


__all__ = ["router"]
