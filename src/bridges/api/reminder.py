"""QQ SMTP 提醒旧合同的 410 兼容路由。

提醒能力已退出产品。路由仍在兼容窗口内保留，但不解析请求体、不访问
提醒服务、不读取账户数据，也不产生队列、邮件或审计副作用；历史投递
只能通过现有账户导出流程读取。
"""

from __future__ import annotations

from typing import NoReturn

from fastapi import APIRouter, Request, status

from bridges.api.auth import SubjectDep
from bridges.contracts.retirement import RetiredCapabilityError
from bridges.retirement import raise_retired_capability

router = APIRouter(prefix="/reminders", tags=["reminders-retired"])

_GONE_RESPONSES = {
    status.HTTP_410_GONE: {"model": RetiredCapabilityError},
}


def _gone(request: Request, endpoint: str) -> NoReturn:
    raise_retired_capability(
        request,
        endpoint=endpoint,
        error="reminders_retired",
    )


@router.get("/smtp", responses=_GONE_RESPONSES)
async def get_smtp_settings(request: Request, _subject: SubjectDep) -> None:
    _gone(request, "reminders.smtp.read")


@router.put("/smtp", responses=_GONE_RESPONSES)
async def save_smtp_code(request: Request, _subject: SubjectDep) -> None:
    _gone(request, "reminders.smtp.write")


@router.post("/smtp/verify", responses=_GONE_RESPONSES)
async def verify_smtp(request: Request, _subject: SubjectDep) -> None:
    _gone(request, "reminders.smtp.verify")


@router.delete("/smtp", responses=_GONE_RESPONSES)
async def delete_smtp_code(request: Request, _subject: SubjectDep) -> None:
    _gone(request, "reminders.smtp.delete")


@router.get("/settings", responses=_GONE_RESPONSES)
async def get_reminder_settings(request: Request, _subject: SubjectDep) -> None:
    _gone(request, "reminders.settings.read")


@router.put("/settings", responses=_GONE_RESPONSES)
async def update_reminder_settings(request: Request, _subject: SubjectDep) -> None:
    _gone(request, "reminders.settings.write")


@router.post("/parse", responses=_GONE_RESPONSES)
async def parse_reminder(request: Request, _subject: SubjectDep) -> None:
    _gone(request, "reminders.parse")


@router.post("", responses=_GONE_RESPONSES)
async def create_reminder(request: Request, _subject: SubjectDep) -> None:
    _gone(request, "reminders.create")


@router.get("", responses=_GONE_RESPONSES)
async def list_reminders(request: Request, _subject: SubjectDep) -> None:
    _gone(request, "reminders.list")


@router.get("/{reminder_id}", responses=_GONE_RESPONSES)
async def get_reminder(reminder_id: str, request: Request, _subject: SubjectDep) -> None:
    _gone(request, "reminders.detail")


@router.put("/{reminder_id}", responses=_GONE_RESPONSES)
async def update_reminder(reminder_id: str, request: Request, _subject: SubjectDep) -> None:
    _gone(request, "reminders.update")


@router.post("/{reminder_id}/pause", responses=_GONE_RESPONSES)
async def pause_reminder(reminder_id: str, request: Request, _subject: SubjectDep) -> None:
    _gone(request, "reminders.pause")


@router.post("/{reminder_id}/resume", responses=_GONE_RESPONSES)
async def resume_reminder(reminder_id: str, request: Request, _subject: SubjectDep) -> None:
    _gone(request, "reminders.resume")


@router.post("/{reminder_id}/send-now", responses=_GONE_RESPONSES)
async def send_reminder_now(reminder_id: str, request: Request, _subject: SubjectDep) -> None:
    _gone(request, "reminders.send-now")


@router.delete("/{reminder_id}", responses=_GONE_RESPONSES)
async def cancel_reminder(reminder_id: str, request: Request, _subject: SubjectDep) -> None:
    _gone(request, "reminders.cancel")


@router.get("/{reminder_id}/deliveries", responses=_GONE_RESPONSES)
async def list_reminder_deliveries(
    reminder_id: str, request: Request, _subject: SubjectDep
) -> None:
    _gone(request, "reminders.deliveries")


__all__ = ["router"]
