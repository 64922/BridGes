"""图片生成与编辑 API 路由（Issue 31，GQ-04 迁移；Issue 21 退役写入口）。

图片生成是 ADR-0030 列明的退役能力：本路由只保留历史只读面——任务查询、
资产投影（版本链与替代文本）与版本图片字节流，供旧会话继续查看与导出。
取消、同输入重试、替代文本修改与资产删除一律稳定返回 410，历史链接
不会误触发新执行。全部资源按账户+对话双重作用域校验，跨账户一律 404
不泄漏存在性；图片字节响应附加私有缓存头，杜绝缓存跨账户复用。
"""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from bridges.api.auth import SubjectDep
from bridges.contracts.chat import ChatError
from bridges.contracts.image import (
    ImageAssetProjection,
    ImageError,
    ImageTaskProjection,
)
from bridges.image.service import ImageService
from bridges.retirement import raise_retired_capability

router = APIRouter(prefix="/chat", tags=["image"])

_RETIRED_ERROR = "legacy_image_retired"
_RETIRED_MESSAGE = "图片生成与编辑已退役，历史结果仍可查看与导出。"
_RETIRED_RESPONSES = {status.HTTP_410_GONE: {"model": ChatError}}


def _get_image_service(request: Request) -> ImageService:
    service = getattr(request.app.state, "image_service", None)
    if service is None:
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "image_unavailable",
            "图片服务未启用，当前实例拒绝图片操作。",
        )
    return cast(ImageService, service)


ImageServiceDep = Annotated[ImageService, Depends(_get_image_service)]


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=ChatError(error=code, message=message).model_dump(),
    )


def _handle_image_error(exc: ImageError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail=ChatError(error=exc.code, message=exc.message).model_dump(),
    )


# ---------------------------------------------------------------------------
# 任务操作面
# ---------------------------------------------------------------------------


@router.get(
    "/conversations/{conversation_id}/image-tasks/{task_id}",
    response_model=ImageTaskProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_404_NOT_FOUND: {"model": ChatError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ChatError},
    },
)
def get_image_task(
    conversation_id: str,
    task_id: str,
    service: ImageServiceDep,
    subject: SubjectDep,
) -> ImageTaskProjection:
    """返回任务投影（刷新/重登/重启后据此恢复任务状态）。

    呈现状态含 recovery（租约过期、后台恢复中）；不存在或跨账户一律
    404。
    """
    try:
        return service.get_task(subject.account_id, conversation_id, task_id)
    except ImageError as exc:
        raise _handle_image_error(exc) from exc


@router.post(
    "/conversations/{conversation_id}/image-tasks/{task_id}/cancel",
    status_code=status.HTTP_410_GONE,
    responses=_RETIRED_RESPONSES,
)
def cancel_image_task(
    conversation_id: str,
    task_id: str,
    request: Request,
    _subject: SubjectDep,
) -> None:
    """取消任务。已退役：不再接受取消请求，稳定返回 410。"""
    raise_retired_capability(
        request,
        endpoint="legacy.image.cancel",
        error=_RETIRED_ERROR,
        replacement_path="/",
        message=_RETIRED_MESSAGE,
    )


@router.post(
    "/conversations/{conversation_id}/image-tasks/{task_id}/retry",
    status_code=status.HTTP_410_GONE,
    responses=_RETIRED_RESPONSES,
)
def retry_image_task(
    conversation_id: str,
    task_id: str,
    request: Request,
    _subject: SubjectDep,
) -> None:
    """重试失败任务。已退役：不再接受重试请求，稳定返回 410。"""
    raise_retired_capability(
        request,
        endpoint="legacy.image.retry",
        error=_RETIRED_ERROR,
        replacement_path="/",
        message=_RETIRED_MESSAGE,
    )


# ---------------------------------------------------------------------------
# 资产操作面
# ---------------------------------------------------------------------------


@router.get(
    "/conversations/{conversation_id}/image-assets/{asset_id}",
    response_model=ImageAssetProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_404_NOT_FOUND: {"model": ChatError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ChatError},
    },
)
def get_image_asset(
    conversation_id: str,
    asset_id: str,
    service: ImageServiceDep,
    subject: SubjectDep,
) -> ImageAssetProjection:
    """返回资产投影：版本链（来源/提示/模型/时间）、替代文本与当前版本。"""
    try:
        return service.get_asset(subject.account_id, conversation_id, asset_id)
    except ImageError as exc:
        raise _handle_image_error(exc) from exc


@router.put(
    "/conversations/{conversation_id}/image-assets/{asset_id}/alt-text",
    status_code=status.HTTP_410_GONE,
    responses=_RETIRED_RESPONSES,
)
def update_image_alt_text(
    conversation_id: str,
    asset_id: str,
    request: Request,
    _subject: SubjectDep,
) -> None:
    """修改资产替代文本。已退役：不再接受写入，稳定返回 410。"""
    raise_retired_capability(
        request,
        endpoint="legacy.image.alt_text",
        error=_RETIRED_ERROR,
        replacement_path="/",
        message=_RETIRED_MESSAGE,
    )


@router.get(
    "/conversations/{conversation_id}/image-assets/{asset_id}"
    "/versions/{version_id}/image",
    responses={
        status.HTTP_200_OK: {
            "content": {"image/png": {}, "image/jpeg": {}},
            "description": "指定版本的图片字节流；下载内容与所选版本一致。",
        },
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_404_NOT_FOUND: {"model": ChatError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ChatError},
    },
)
def get_image_version_bytes(
    conversation_id: str,
    asset_id: str,
    version_id: str,
    service: ImageServiceDep,
    subject: SubjectDep,
    download: int = 0,
) -> Response:
    """返回指定版本图片字节（流式，账户授权校验 + 私有缓存头）。

    ``download=1`` 时附加附件下载头（下载内容与所选版本一致）；默认
    内联显示。跨账户、已删除资产或版本不存在一律 404；缓存私有化杜绝
    跨账户缓存复用。
    """
    try:
        content, media_type, length = service.get_version_image_bytes(
            subject.account_id, conversation_id, asset_id, version_id
        )
    except ImageError as exc:
        raise _handle_image_error(exc) from exc
    headers = {
        "Content-Type": media_type,
        "Content-Length": str(length),
        "Cache-Control": "private, max-age=0, no-store",
    }
    if download:
        headers["Content-Disposition"] = (
            f'attachment; filename="bridges-image-{version_id}.png"'
        )
    return Response(content=content, headers=headers)


@router.delete(
    "/conversations/{conversation_id}/image-assets/{asset_id}",
    status_code=status.HTTP_410_GONE,
    responses=_RETIRED_RESPONSES,
)
def delete_image_asset(
    conversation_id: str,
    asset_id: str,
    request: Request,
    _subject: SubjectDep,
) -> None:
    """删除资产。已退役：不再接受删除请求，稳定返回 410。

    历史资产随旧会话保留，按账户导出与账户删除合同处置。
    """
    raise_retired_capability(
        request,
        endpoint="legacy.image.delete",
        error=_RETIRED_ERROR,
        replacement_path="/",
        message=_RETIRED_MESSAGE,
    )
