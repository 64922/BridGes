"""SKILL 插件中心 API 路由（Issue 34）。

插件中心是账户作用域能力：内置插件随应用发布、账户可启停但不可卸载；
用户上传声明式 zip 包先安全检查再确认安装，检查与安装都不携带包内容
进入响应与审计。跨账户访问一律 404，不泄漏插件存在性。zip 上传走
原始字节 + X-Bridges-Filename 头（与附件上传同一合同）。
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Request, status

from bridges.api.auth import SubjectDep
from bridges.contracts.identity import AuthError
from bridges.contracts.plugins import (
    PluginCheckResult,
    PluginDemoProjection,
    PluginError,
    PluginListProjection,
    UserPluginProjection,
)
from bridges.plugins.checker import MAX_PLUGIN_BYTES
from bridges.plugins.service import PluginService

router = APIRouter(prefix="/plugins", tags=["plugins"])

# 包体上限与检查器同源（单一事实源）；演示附件复用聊天附件 10MB 上限。
MAX_PLUGIN_UPLOAD_BYTES = MAX_PLUGIN_BYTES
MAX_DEMO_UPLOAD_BYTES = 10 * 1024 * 1024


def _get_plugin_service(request: Request) -> PluginService:
    service: PluginService | None = getattr(request.app.state, "plugin_service", None)
    if service is None:
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "plugin_unavailable",
            "插件中心未启用，当前实例拒绝插件操作。",
        )
    return service


PluginServiceDep = Annotated[PluginService, Depends(_get_plugin_service)]


def _error(http_status: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=http_status,
        detail={"error": code, "message": message},
    )


def _from_error(exc: PluginError) -> HTTPException:
    return _error(exc.status_code, exc.code, exc.message)


async def _read_upload(
    request: Request, *, max_bytes: int = MAX_PLUGIN_UPLOAD_BYTES
) -> tuple[str, bytes]:
    """读取原始字节与文件名（与附件上传同一合同，含大小闭锁）。"""
    declared_length = request.headers.get("content-length")
    if declared_length is not None:
        try:
            if int(declared_length) > max_bytes:
                raise _error(
                    status.HTTP_413_CONTENT_TOO_LARGE,
                    "upload_too_large",
                    f"上传内容超过 {max_bytes // 1024 // 1024} MB 大小限制，请压缩后重试。",
                )
        except ValueError as exc:
            raise _error(
                status.HTTP_400_BAD_REQUEST, "invalid_request", "上传请求大小无效。"
            ) from exc
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            raise _error(
                status.HTTP_413_CONTENT_TOO_LARGE,
                "upload_too_large",
                f"上传内容超过 {max_bytes // 1024 // 1024} MB 大小限制，请压缩后重试。",
            )
        chunks.append(chunk)
    filename_header = request.headers.get("x-bridges-filename")
    if not filename_header:
        raise _error(
            status.HTTP_400_BAD_REQUEST, "invalid_filename", "缺少文件名，无法上传。"
        )
    return unquote(filename_header), b"".join(chunks)


@router.get(
    "",
    response_model=PluginListProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": AuthError},
    },
)
async def list_plugins(
    service: PluginServiceDep,
    subject: SubjectDep,
) -> PluginListProjection:
    """返回内置插件（含当前账户启停状态）与当前账户用户包列表。"""
    return service.list_plugins(subject.account_id)


@router.post(
    "/check",
    response_model=PluginCheckResult,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": AuthError},
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_413_CONTENT_TOO_LARGE: {"model": AuthError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": AuthError},
    },
)
async def check_package(
    request: Request,
    service: PluginServiceDep,
    subject: SubjectDep,
) -> PluginCheckResult:
    """安装前检查：安全闭锁 + 内容清单预览；纯检查，无副作用。"""
    filename, content = await _read_upload(request)
    try:
        return service.check_package(filename, content)
    except PluginError as exc:
        raise _from_error(exc) from exc


@router.post(
    "/install",
    response_model=UserPluginProjection,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": AuthError},
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_409_CONFLICT: {"model": AuthError},
        status.HTTP_413_CONTENT_TOO_LARGE: {"model": AuthError},
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"model": AuthError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": AuthError},
    },
)
async def install_package(
    request: Request,
    service: PluginServiceDep,
    subject: SubjectDep,
) -> UserPluginProjection:
    """确认安装：重跑安全闭锁，通过后按账户持久化并审计。"""
    filename, content = await _read_upload(request)
    try:
        return service.install_package(subject.account_id, filename, content)
    except PluginError as exc:
        raise _from_error(exc) from exc


@router.post(
    "/{plugin_id}/enable",
    response_model=PluginListProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_404_NOT_FOUND: {"model": AuthError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": AuthError},
    },
)
async def enable_plugin(
    plugin_id: str,
    service: PluginServiceDep,
    subject: SubjectDep,
) -> PluginListProjection:
    """启用插件（内置与用户包统一入口）。"""
    try:
        service.set_enabled(subject.account_id, plugin_id, enabled=True)
        return service.list_plugins(subject.account_id)
    except PluginError as exc:
        raise _from_error(exc) from exc


@router.post(
    "/{plugin_id}/disable",
    response_model=PluginListProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_404_NOT_FOUND: {"model": AuthError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": AuthError},
    },
)
async def disable_plugin(
    plugin_id: str,
    service: PluginServiceDep,
    subject: SubjectDep,
) -> PluginListProjection:
    """停用插件（内置与用户包统一入口）。

    停用后插件立即从「可用集合」（选择器/工具调用）消失；对话中已选
    择的该项在读取/发送时被清洗并解释影响（Issue 36 AC7：立即从可用
    集合移除并解释影响）——「选择随对话持久化」保留用户未主动清除的
    选择，重新启用后恢复属持久化语义。
    """
    try:
        service.set_enabled(subject.account_id, plugin_id, enabled=False)
        return service.list_plugins(subject.account_id)
    except PluginError as exc:
        raise _from_error(exc) from exc


@router.delete(
    "/{plugin_id}",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_403_FORBIDDEN: {"model": AuthError},
        status.HTTP_404_NOT_FOUND: {"model": AuthError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": AuthError},
    },
)
async def uninstall_plugin(
    plugin_id: str,
    service: PluginServiceDep,
    subject: SubjectDep,
) -> PluginListProjection:
    """卸载用户包（内置插件拒绝，只能停用）。"""
    try:
        service.uninstall(subject.account_id, plugin_id)
        return service.list_plugins(subject.account_id)
    except PluginError as exc:
        raise _from_error(exc) from exc


@router.post(
    "/builtin/{skill_id}/demo",
    response_model=PluginDemoProjection,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": AuthError},
        status.HTTP_401_UNAUTHORIZED: {"model": AuthError},
        status.HTTP_409_CONFLICT: {"model": AuthError},
        status.HTTP_413_CONTENT_TOO_LARGE: {"model": AuthError},
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"model": AuthError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": AuthError},
    },
)
async def demo_builtin(
    skill_id: str,
    request: Request,
    service: PluginServiceDep,
    subject: SubjectDep,
) -> PluginDemoProjection:
    """内置 PDF/Documents 演示：对附件执行真实解析并返回统计与预览。"""
    filename, content = await _read_upload(
        request, max_bytes=MAX_DEMO_UPLOAD_BYTES
    )
    try:
        return service.demo(subject.account_id, skill_id, filename, content)
    except PluginError as exc:
        raise _from_error(exc) from exc
