"""图片生成与编辑 API 路由（Issue 31）。

提交路径与聊天发送合一（``ChatMessageCreateRequest.image`` 载荷走 SSE）；
本路由只承载任务与资产的操作面：取消、同输入重试、任务查询、资产
投影（版本链与替代文本）、替代文本修改、版本图片字节流与带影响说明
的删除。全部资源按账户+对话双重作用域校验，跨账户一律 404 不泄漏
存在性；图片字节响应附加私有缓存头，杜绝缓存跨账户复用。
"""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from bridges.api.auth import SubjectDep
from bridges.contracts.chat import ChatError
from bridges.contracts.image import (
    ImageAltTextUpdateRequest,
    ImageAssetProjection,
    ImageDeletionProjection,
    ImageError,
    ImageTaskProjection,
)
from bridges.credentials.service import KeyCredentialService
from bridges.image.service import ImageService

router = APIRouter(prefix="/chat", tags=["image"])


def _get_image_service(request: Request) -> ImageService:
    service = getattr(request.app.state, "image_service", None)
    if service is None:
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "image_unavailable",
            "图片服务未启用，当前实例拒绝图片操作。",
        )
    return cast(ImageService, service)


def _get_credential_service(request: Request) -> KeyCredentialService:
    service = getattr(request.app.state, "credential_service", None)
    if service is None:
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "credential_store_unavailable",
            "凭据存储当前不可用，请稍后重试。",
        )
    return cast(KeyCredentialService, service)


ImageServiceDep = Annotated[ImageService, Depends(_get_image_service)]
CredentialServiceDep = Annotated[
    KeyCredentialService, Depends(_get_credential_service)
]


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


def ensure_image_capability_ready(
    subject: SubjectDep, credential_service: KeyCredentialService
) -> None:
    """任务重试前核对账户级图片能力探测快照（与发送同一语义）。"""
    from bridges.contracts.credentials import ProbeStatus

    try:
        projection = credential_service.get_projection(subject.account_id)
    except Exception as exc:  # noqa: BLE001 - 凭据存储异常统一折叠
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "credential_store_unavailable",
            "凭据存储当前不可用，请稍后重试。",
        ) from exc
    if not projection.configured:
        raise _error(
            status.HTTP_409_CONFLICT,
            "no_api_key",
            "尚未配置 Qwen API Key，请前往「设置」中的密钥页配置后再试。",
        )
    image = next(
        (
            capability
            for capability in projection.capabilities
            if capability.capability_id == "image"
        ),
        None,
    )
    if image is None:
        raise _error(
            status.HTTP_409_CONFLICT,
            "capability_unavailable",
            "图片能力信息缺失，请前往「设置」重新探测。",
        )
    if image.status == ProbeStatus.PROBING:
        raise _error(
            status.HTTP_409_CONFLICT,
            "capability_probing",
            "图片能力正在探测中，请稍候再试。",
        )
    if image.status != ProbeStatus.AVAILABLE:
        reason = image.message or "未知原因"
        raise _error(
            status.HTTP_409_CONFLICT,
            "capability_unavailable",
            f"图片生成与编辑能力当前不可用：{reason}。请前往「设置」重新探测。",
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
    response_model=ImageTaskProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_404_NOT_FOUND: {"model": ChatError},
        status.HTTP_409_CONFLICT: {"model": ChatError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ChatError},
    },
)
def cancel_image_task(
    conversation_id: str,
    task_id: str,
    service: ImageServiceDep,
    subject: SubjectDep,
) -> ImageTaskProjection:
    """取消任务：本地标记为权威，尽力通知云端；迟到结果不发布。

    已成功/已取消的任务幂等返回当前投影；不存在或跨账户一律 404。
    """
    try:
        return service.cancel(subject.account_id, conversation_id, task_id)
    except ImageError as exc:
        raise _handle_image_error(exc) from exc


@router.post(
    "/conversations/{conversation_id}/image-tasks/{task_id}/retry",
    response_model=ImageTaskProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_404_NOT_FOUND: {"model": ChatError},
        status.HTTP_409_CONFLICT: {"model": ChatError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ChatError},
    },
)
def retry_image_task(
    conversation_id: str,
    task_id: str,
    service: ImageServiceDep,
    credential_service: CredentialServiceDep,
    subject: SubjectDep,
) -> ImageTaskProjection:
    """重试失败任务：同输入（提示/来源不变）重新入队，固定同一快照。

    重试需要图片能力仍可用（能力不可用时入口明确拒绝并说明原因）。
    """
    ensure_image_capability_ready(subject, credential_service)
    try:
        return service.retry(subject.account_id, conversation_id, task_id)
    except ImageError as exc:
        raise _handle_image_error(exc) from exc


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
    response_model=ImageAssetProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_404_NOT_FOUND: {"model": ChatError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ChatError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ChatError},
    },
)
def update_image_alt_text(
    conversation_id: str,
    asset_id: str,
    body: ImageAltTextUpdateRequest,
    service: ImageServiceDep,
    subject: SubjectDep,
) -> ImageAssetProjection:
    """修改资产替代文本（来源标记为 manual，替代自动生成值）。"""
    try:
        return service.update_alt_text(
            subject.account_id, conversation_id, asset_id, body.alt_text
        )
    except ImageError as exc:
        raise _handle_image_error(exc) from exc


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
    response_model=ImageDeletionProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ChatError},
        status.HTTP_404_NOT_FOUND: {"model": ChatError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ChatError},
    },
)
def delete_image_asset(
    conversation_id: str,
    asset_id: str,
    service: ImageServiceDep,
    subject: SubjectDep,
) -> ImageDeletionProjection:
    """删除资产并返回影响说明：移除版本数、更新的消息引用与对象处置状态。

    删除同时维护消息引用、资产元数据与本地对象一致性：对象物理清理
    失败时保留待清理记录（``pending_cleanup``），由后台清理轮重试，
    可观察可恢复；幂等，已删除资产返回零计数投影。
    """
    try:
        return service.delete_asset(subject.account_id, conversation_id, asset_id)
    except ImageError as exc:
        raise _handle_image_error(exc) from exc
