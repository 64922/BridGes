"""文生视频 API 路由（Issue 32，GQ-04 迁移；Issue 21 退役写入口）。

文生视频是 ADR-0030 列明的退役能力：本路由只保留历史只读面——任务查询、
资产投影（可访问文字说明）与视频字节流，供旧会话继续查看与导出。取消、
同输入重试、说明修改与资产删除一律稳定返回 410，历史链接不会误触发新
执行。全部资源按账户+对话双重作用域校验，跨账户一律 404 不泄漏存在性；
视频字节响应附加私有缓存头，杜绝缓存跨账户复用。
"""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from bridges.api.auth import SubjectDep
from bridges.contracts.chat import ChatError
from bridges.contracts.video import (
    VideoAssetProjection,
    VideoError,
    VideoTaskProjection,
)
from bridges.retirement import raise_retired_capability
from bridges.video.service import VideoService

router = APIRouter(prefix="/chat", tags=["video"])

_RETIRED_ERROR = "legacy_video_retired"
_RETIRED_MESSAGE = "视频生成已退役，历史结果仍可查看与导出。"
_RETIRED_RESPONSES = {status.HTTP_410_GONE: {"model": ChatError}}


def _get_video_service(request: Request) -> VideoService:
    service = getattr(request.app.state, "video_service", None)
    if service is None:
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "video_unavailable",
            "视频服务未启用，当前实例拒绝视频操作。",
        )
    return cast(VideoService, service)


VideoServiceDep = Annotated[VideoService, Depends(_get_video_service)]


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=ChatError(error=code, message=message).model_dump(),
    )


def _handle_video_error(exc: VideoError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail=ChatError(error=exc.code, message=exc.message).model_dump(),
    )


# ---------------------------------------------------------------------------
# 任务操作面
# ---------------------------------------------------------------------------


@router.get(
    "/conversations/{conversation_id}/video-tasks/{task_id}",
    response_model=VideoTaskProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_404_NOT_FOUND: {"model": ChatError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ChatError},
    },
)
def get_video_task(
    conversation_id: str,
    task_id: str,
    service: VideoServiceDep,
    subject: SubjectDep,
) -> VideoTaskProjection:
    """返回任务投影（刷新/重登/重启后据此恢复任务状态）。

    呈现状态含 recovery（租约过期、后台恢复中）与 cancelling（取消
    收敛中）；不存在或跨账户一律 404。
    """
    try:
        return service.get_task(subject.account_id, conversation_id, task_id)
    except VideoError as exc:
        raise _handle_video_error(exc) from exc


@router.post(
    "/conversations/{conversation_id}/video-tasks/{task_id}/cancel",
    status_code=status.HTTP_410_GONE,
    responses=_RETIRED_RESPONSES,
)
def cancel_video_task(
    conversation_id: str,
    task_id: str,
    request: Request,
    _subject: SubjectDep,
) -> None:
    """取消任务。已退役：不再接受取消请求，稳定返回 410。"""
    raise_retired_capability(
        request,
        endpoint="legacy.video.cancel",
        error=_RETIRED_ERROR,
        replacement_path="/",
        message=_RETIRED_MESSAGE,
    )


@router.post(
    "/conversations/{conversation_id}/video-tasks/{task_id}/retry",
    status_code=status.HTTP_410_GONE,
    responses=_RETIRED_RESPONSES,
)
def retry_video_task(
    conversation_id: str,
    task_id: str,
    request: Request,
    _subject: SubjectDep,
) -> None:
    """重试失败任务。已退役：不再接受重试请求，稳定返回 410。"""
    raise_retired_capability(
        request,
        endpoint="legacy.video.retry",
        error=_RETIRED_ERROR,
        replacement_path="/",
        message=_RETIRED_MESSAGE,
    )


# ---------------------------------------------------------------------------
# 资产操作面
# ---------------------------------------------------------------------------


@router.get(
    "/conversations/{conversation_id}/video-assets/{asset_id}",
    response_model=VideoAssetProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_404_NOT_FOUND: {"model": ChatError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ChatError},
    },
)
def get_video_asset(
    conversation_id: str,
    asset_id: str,
    service: VideoServiceDep,
    subject: SubjectDep,
) -> VideoAssetProjection:
    """返回资产投影：可访问文字说明、提示、模型、供应商任务标识与时间。"""
    try:
        return service.get_asset(subject.account_id, conversation_id, asset_id)
    except VideoError as exc:
        raise _handle_video_error(exc) from exc


@router.put(
    "/conversations/{conversation_id}/video-assets/{asset_id}/description",
    status_code=status.HTTP_410_GONE,
    responses=_RETIRED_RESPONSES,
)
def update_video_description(
    conversation_id: str,
    asset_id: str,
    request: Request,
    _subject: SubjectDep,
) -> None:
    """修改资产可访问文字说明。已退役：不再接受写入，稳定返回 410。"""
    raise_retired_capability(
        request,
        endpoint="legacy.video.description",
        error=_RETIRED_ERROR,
        replacement_path="/",
        message=_RETIRED_MESSAGE,
    )


@router.get(
    "/conversations/{conversation_id}/video-assets/{asset_id}/video",
    responses={
        status.HTTP_200_OK: {
            "content": {"video/mp4": {}},
            "description": "视频字节流（内联预览或附件下载）。",
        },
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_404_NOT_FOUND: {"model": ChatError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ChatError},
    },
)
def get_video_bytes(
    conversation_id: str,
    asset_id: str,
    service: VideoServiceDep,
    subject: SubjectDep,
    download: int = 0,
) -> Response:
    """返回视频字节（流式，账户授权校验 + 私有缓存头）。

    ``download=1`` 时附加附件下载头；默认内联预览。跨账户或已删除
    资产一律 404；缓存私有化杜绝跨账户缓存复用。
    """
    try:
        content, media_type, length = service.get_video_bytes(
            subject.account_id, conversation_id, asset_id
        )
    except VideoError as exc:
        raise _handle_video_error(exc) from exc
    headers = {
        "Content-Type": media_type,
        "Content-Length": str(length),
        "Cache-Control": "private, max-age=0, no-store",
    }
    if download:
        headers["Content-Disposition"] = (
            f'attachment; filename="bridges-video-{asset_id}.mp4"'
        )
    return Response(content=content, headers=headers)


@router.delete(
    "/conversations/{conversation_id}/video-assets/{asset_id}",
    status_code=status.HTTP_410_GONE,
    responses=_RETIRED_RESPONSES,
)
def delete_video_asset(
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
        endpoint="legacy.video.delete",
        error=_RETIRED_ERROR,
        replacement_path="/",
        message=_RETIRED_MESSAGE,
    )
