"""显式授权 MCP 插件管理 API 路由（Issue 35）。

MCP 插件中心是账户作用域能力：安装描述（单个 MCP.yaml）先安全闭锁再
确认安装，未锁版本/描述损坏/来源不匹配一律闭锁；安装前逐项预览权限
清单；调用只接收当前消息明确授权的数据切片；敏感操作经再次确认（目标
与影响）后才执行，拒绝即安全终止；撤权/启停/卸载全部账户作用域并审计。
跨账户访问一律 404，不泄漏插件存在性。安装描述与调用正文不进入响应
与审计（只保留标识与计数）。
"""

from __future__ import annotations

import contextlib
from typing import Annotated
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Request, status

from bridges.api.auth import SubjectDep
from bridges.chat.selections import ChatSelectionsService
from bridges.contracts.identity import AuthError
from bridges.contracts.mcp import (
    McpCallRecord,
    McpCallRequest,
    McpCallResult,
    McpCheckResult,
    McpError,
    McpListProjection,
    McpPermissionManifest,
    McpServerProjection,
)
from bridges.mcp.checker import MAX_DESCRIPTOR_BYTES
from bridges.mcp.service import McpService

router = APIRouter(prefix="/mcp", tags=["mcp"])


def _get_mcp_service(request: Request) -> McpService:
    service: McpService | None = getattr(request.app.state, "mcp_service", None)
    if service is None:
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "mcp_unavailable",
            "MCP 插件中心未启用，当前实例拒绝 MCP 操作。",
        )
    return service


McpServiceDep = Annotated[McpService, Depends(_get_mcp_service)]


def _error(http_status: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=http_status,
        detail={"error": code, "message": message},
    )


def _from_error(exc: McpError) -> HTTPException:
    return _error(exc.status_code, exc.code, exc.message)


async def _read_upload(request: Request) -> tuple[str, bytes]:
    """读取原始字节与文件名（与附件/插件上传同一合同，含大小闭锁）。"""
    declared_length = request.headers.get("content-length")
    if declared_length is not None:
        try:
            if int(declared_length) > MAX_DESCRIPTOR_BYTES:
                raise _error(
                    status.HTTP_413_CONTENT_TOO_LARGE,
                    "upload_too_large",
                    f"上传内容超过 {MAX_DESCRIPTOR_BYTES // 1024} KB 大小限制，请精简后重试。",
                )
        except ValueError as exc:
            raise _error(
                status.HTTP_400_BAD_REQUEST, "invalid_request", "上传请求大小无效。"
            ) from exc
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_DESCRIPTOR_BYTES:
            raise _error(
                status.HTTP_413_CONTENT_TOO_LARGE,
                "upload_too_large",
                f"上传内容超过 {MAX_DESCRIPTOR_BYTES // 1024} KB 大小限制，请精简后重试。",
            )
        chunks.append(chunk)
    filename_header = request.headers.get("x-bridges-filename")
    if not filename_header:
        raise _error(status.HTTP_400_BAD_REQUEST, "invalid_filename", "缺少文件名，无法上传。")
    return unquote(filename_header), b"".join(chunks)


@router.get(
    "",
    response_model=McpListProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": AuthError},
    },
)
def list_mcp_servers(
    service: McpServiceDep,
    subject: SubjectDep,
) -> McpListProjection:
    """返回当前账户的全部 MCP 服务器与真实调用统计。"""
    return service.list_servers(subject.account_id)


@router.post(
    "/check",
    response_model=McpCheckResult,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": AuthError},
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_413_CONTENT_TOO_LARGE: {"model": AuthError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": AuthError},
    },
)
async def check_descriptor(
    request: Request,
    service: McpServiceDep,
    subject: SubjectDep,
) -> McpCheckResult:
    """安装前检查：安全闭锁 + 权限清单预览；纯检查，无副作用。"""
    filename, content = await _read_upload(request)
    try:
        return service.check(filename, content)
    except McpError as exc:
        raise _from_error(exc) from exc


@router.post(
    "/install",
    response_model=McpServerProjection,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": AuthError},
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_409_CONFLICT: {"model": AuthError},
        status.HTTP_413_CONTENT_TOO_LARGE: {"model": AuthError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": AuthError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": AuthError},
    },
)
async def install_descriptor(
    request: Request,
    service: McpServiceDep,
    subject: SubjectDep,
) -> McpServerProjection:
    """确认安装：重跑安全闭锁，锁定描述哈希后按账户持久化并审计。"""
    filename, content = await _read_upload(request)
    try:
        return service.install(subject.account_id, filename, content)
    except McpError as exc:
        raise _from_error(exc) from exc


@router.post(
    "/{mcp_id}/enable",
    response_model=McpListProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_404_NOT_FOUND: {"model": AuthError},
        status.HTTP_409_CONFLICT: {"model": AuthError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": AuthError},
    },
)
def enable_mcp(
    mcp_id: str,
    service: McpServiceDep,
    subject: SubjectDep,
) -> McpListProjection:
    """启用 MCP（恢复调用能力，进程惰性启动）。"""
    try:
        service.set_enabled(subject.account_id, mcp_id, enabled=True)
        return service.list_servers(subject.account_id)
    except McpError as exc:
        raise _from_error(exc) from exc


@router.post(
    "/{mcp_id}/disable",
    response_model=McpListProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_404_NOT_FOUND: {"model": AuthError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": AuthError},
    },
)
def disable_mcp(
    mcp_id: str,
    service: McpServiceDep,
    subject: SubjectDep,
) -> McpListProjection:
    """停用 MCP：停止新调用并终止运行中的进程。"""
    try:
        service.set_enabled(subject.account_id, mcp_id, enabled=False)
        return service.list_servers(subject.account_id)
    except McpError as exc:
        raise _from_error(exc) from exc


@router.put(
    "/{mcp_id}/permissions",
    response_model=McpServerProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_404_NOT_FOUND: {"model": AuthError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": AuthError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": AuthError},
    },
)
def revoke_permissions(
    mcp_id: str,
    manifest: McpPermissionManifest,
    service: McpServiceDep,
    subject: SubjectDep,
    request: Request,
) -> McpServerProjection:
    """撤权：以新权限清单替换；移除敏感权限时终止依赖该权限的运行。

    撤权成功后把该 MCP 从当前账户全部会话的插件选择中移除（Issue 36
    AC7：撤权后立即从可用集合移除）——此前对话对旧权限清单的选择授权
    不再成立，需重新选择后才能再次调用。
    """
    try:
        projection = service.revoke_permissions(subject.account_id, mcp_id, manifest)
    except McpError as exc:
        raise _from_error(exc) from exc
    selections: ChatSelectionsService | None = getattr(
        request.app.state, "chat_selections_service", None
    )
    if selections is not None:
        # 选择联动失败不阻断撤权主流程（仅影响后续对话可用集合）。
        with contextlib.suppress(Exception):  # noqa: BLE001
            selections.revoke_mcp_selection(subject.account_id, mcp_id)
    return projection


@router.delete(
    "/{mcp_id}",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_404_NOT_FOUND: {"model": AuthError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": AuthError},
    },
)
def uninstall_mcp(
    mcp_id: str,
    service: McpServiceDep,
    subject: SubjectDep,
) -> McpListProjection:
    """卸载 MCP：停止进程、删除记录与描述对象。"""
    try:
        service.uninstall(subject.account_id, mcp_id)
        return service.list_servers(subject.account_id)
    except McpError as exc:
        raise _from_error(exc) from exc


@router.post(
    "/{mcp_id}/invoke",
    response_model=McpCallResult,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_403_FORBIDDEN: {"model": AuthError},
        status.HTTP_404_NOT_FOUND: {"model": AuthError},
        status.HTTP_409_CONFLICT: {"model": AuthError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": AuthError},
        status.HTTP_502_BAD_GATEWAY: {"model": AuthError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": AuthError},
    },
)
def invoke_mcp(
    mcp_id: str,
    body: McpCallRequest,
    service: McpServiceDep,
    subject: SubjectDep,
) -> McpCallResult:
    """执行一次真实调用；敏感操作挂起返回确认载荷（前端弹窗）。"""
    try:
        return service.invoke(subject.account_id, mcp_id, body)
    except McpError as exc:
        raise _from_error(exc) from exc


@router.post(
    "/{mcp_id}/confirmations/{confirmation_id}/approve",
    response_model=McpCallResult,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_404_NOT_FOUND: {"model": AuthError},
        status.HTTP_409_CONFLICT: {"model": AuthError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": AuthError},
    },
)
def approve_confirmation(
    mcp_id: str,
    confirmation_id: str,
    service: McpServiceDep,
    subject: SubjectDep,
) -> McpCallResult:
    """确认敏感操作：仅对本次调用有效，不扩展成永久授权。"""
    try:
        return service.approve(subject.account_id, mcp_id, confirmation_id)
    except McpError as exc:
        raise _from_error(exc) from exc


@router.post(
    "/{mcp_id}/confirmations/{confirmation_id}/deny",
    response_model=McpCallResult,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_404_NOT_FOUND: {"model": AuthError},
        status.HTTP_409_CONFLICT: {"model": AuthError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": AuthError},
    },
)
def deny_confirmation(
    mcp_id: str,
    confirmation_id: str,
    service: McpServiceDep,
    subject: SubjectDep,
) -> McpCallResult:
    """拒绝敏感操作：调用安全终止，不执行任何操作。"""
    try:
        return service.deny(subject.account_id, mcp_id, confirmation_id)
    except McpError as exc:
        raise _from_error(exc) from exc


@router.get(
    "/{mcp_id}/calls",
    response_model=list[McpCallRecord],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_404_NOT_FOUND: {"model": AuthError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": AuthError},
    },
)
def list_mcp_calls(
    mcp_id: str,
    service: McpServiceDep,
    subject: SubjectDep,
) -> list[McpCallRecord]:
    """返回最近调用记录（真实次数/最近结果/失败原因，不含正文）。"""
    try:
        return service.get_calls(subject.account_id, mcp_id)
    except McpError as exc:
        raise _from_error(exc) from exc
