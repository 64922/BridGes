"""文生视频 API 路由（Issue 32，GQ-04 迁移）。

提交路径与聊天发送合一（``ChatMessageCreateRequest.video`` 载荷走 SSE）；
本路由只承载任务与资产的操作面：取消、同输入重试、任务查询、资产
投影（可访问文字说明）、说明修改、视频字节流与带影响说明的删除。全部
资源按账户+对话双重作用域校验，跨账户一律 404 不泄漏存在性；视频字节
响应附加私有缓存头，杜绝缓存跨账户复用。能力门只接受「视频生成」固定
绑定（wan2.7-t2v-2026-06-12，ADR-0007 Wan 例外），界面不提供模型选择；
任务操作面不检查账户凭据或探测快照（GQ-04），新账户无需任何个人
Qwen 配置即可重试任务。
"""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from bridges.api.auth import SubjectDep
from bridges.contracts.chat import ChatError
from bridges.contracts.video import (
    VideoAssetProjection,
    VideoDeletionProjection,
    VideoDescriptionUpdateRequest,
    VideoError,
    VideoTaskProjection,
)
from bridges.video.service import VideoService

router = APIRouter(prefix="/chat", tags=["video"])


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
    response_model=VideoTaskProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_404_NOT_FOUND: {"model": ChatError},
        status.HTTP_409_CONFLICT: {"model": ChatError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ChatError},
    },
)
def cancel_video_task(
    conversation_id: str,
    task_id: str,
    service: VideoServiceDep,
    subject: SubjectDep,
) -> VideoTaskProjection:
    """取消任务：本地标记为「取消中」，worker 收敛为已取消；迟到结果不发布。

    已成功/已取消的任务幂等返回当前投影；不存在或跨账户一律 404。
    """
    try:
        return service.cancel(subject.account_id, conversation_id, task_id)
    except VideoError as exc:
        raise _handle_video_error(exc) from exc


@router.post(
    "/conversations/{conversation_id}/video-tasks/{task_id}/retry",
    response_model=VideoTaskProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_404_NOT_FOUND: {"model": ChatError},
        status.HTTP_409_CONFLICT: {"model": ChatError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ChatError},
    },
)
def retry_video_task(
    conversation_id: str,
    task_id: str,
    service: VideoServiceDep,
    subject: SubjectDep,
) -> VideoTaskProjection:
    """重试失败任务：同输入（提示不变）重新入队，固定同一快照。

    不检查账户凭据或探测快照（GQ-04）：新账户无需任何个人 Qwen 配置
    即可重试，云端调用由已注册的全局模型网关固定适配器执行。
    """
    try:
        return service.retry(subject.account_id, conversation_id, task_id)
    except VideoError as exc:
        raise _handle_video_error(exc) from exc


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
    response_model=VideoAssetProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_404_NOT_FOUND: {"model": ChatError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ChatError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ChatError},
    },
)
def update_video_description(
    conversation_id: str,
    asset_id: str,
    body: VideoDescriptionUpdateRequest,
    service: VideoServiceDep,
    subject: SubjectDep,
) -> VideoAssetProjection:
    """修改资产可访问文字说明（来源标记为 manual，替代提示词默认值）。"""
    try:
        return service.update_description(
            subject.account_id, conversation_id, asset_id, body.description
        )
    except VideoError as exc:
        raise _handle_video_error(exc) from exc


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
    response_model=VideoDeletionProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_404_NOT_FOUND: {"model": ChatError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ChatError},
    },
)
def delete_video_asset(
    conversation_id: str,
    asset_id: str,
    service: VideoServiceDep,
    subject: SubjectDep,
) -> VideoDeletionProjection:
    """删除资产并返回影响说明：移除对象数、更新的消息引用与对象处置状态。

    删除同时维护消息引用、资产元数据与本地对象一致性：对象物理清理
    失败时保留待清理记录（``pending_cleanup``），由后台清理轮重试，
    可观察可恢复；幂等，已删除资产返回零计数投影。
    """
    try:
        return service.delete_asset(subject.account_id, conversation_id, asset_id)
    except VideoError as exc:
        raise _handle_video_error(exc) from exc
