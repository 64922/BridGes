"""进行中事项区 API 路由（画像工单 13）。

投影只读明确事实（目标 + 同组进展/待续问题），暂停/完成/继续作用于精确
条目（乐观锁），领域引用只存引用、状态由领域实时解析；使用开关关闭时仍可
管理事项，但不代表回答会使用它们。路由并入 :mod:`bridges.profiles.api` 的
``/profiles`` 前缀。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status

from bridges.api.auth import SubjectDep
from bridges.contracts.ongoing_profile import (
    OngoingDomainReferenceUpdateRequest,
    OngoingItemsProjection,
    OngoingItemStateUpdateRequest,
)
from bridges.contracts.profiles import ProfileError
from bridges.profiles.api_errors import atomic_error
from bridges.profiles.atomic import AtomicProfileError
from bridges.profiles.ongoing import OngoingItemsService

# 路由并入 bridges.profiles.api 的 /profiles 前缀与 profiles 标签：含前缀的
# 父路由 include 本无前缀无标签子路由，避免双前缀与标签重复。
router = APIRouter()


def _get_ongoing_items_service(request: Request) -> OngoingItemsService:
    service: OngoingItemsService | None = getattr(
        request.app.state, "ongoing_items_service", None
    )
    if service is None:
        raise RuntimeError("OngoingItemsService not attached to application state.")
    return service


OngoingItemsServiceDep = Annotated[
    OngoingItemsService, Depends(_get_ongoing_items_service)
]


@router.get(
    "/ongoing",
    response_model=OngoingItemsProjection,
    responses={status.HTTP_401_UNAUTHORIZED: {"model": ProfileError}},
)
async def list_ongoing_items(
    service: OngoingItemsServiceDep,
    subject: SubjectDep,
) -> OngoingItemsProjection:
    """当前账户的进行中事项（目标、进展、待续问题与领域引用）。"""

    return service.list_items(subject.account_id)


@router.post(
    "/ongoing/{item_id}/state",
    response_model=OngoingItemsProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProfileError},
        status.HTTP_404_NOT_FOUND: {"model": ProfileError},
        status.HTTP_409_CONFLICT: {"model": ProfileError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ProfileError},
    },
)
async def update_ongoing_item_state(
    service: OngoingItemsServiceDep,
    subject: SubjectDep,
    item_id: str,
    request: OngoingItemStateUpdateRequest,
) -> OngoingItemsProjection:
    """暂停/完成/继续进行中目标；返回刷新后的事项区。"""

    try:
        service.set_state(
            subject.account_id, item_id, request.action, version=request.version
        )
    except AtomicProfileError as exc:
        raise atomic_error(exc, "ongoing_item_state_failed") from exc
    return service.list_items(subject.account_id)


@router.put(
    "/ongoing/{item_id}/domain-reference",
    response_model=OngoingItemsProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProfileError},
        status.HTTP_404_NOT_FOUND: {"model": ProfileError},
        status.HTTP_409_CONFLICT: {"model": ProfileError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ProfileError},
    },
)
async def update_ongoing_domain_reference(
    service: OngoingItemsServiceDep,
    subject: SubjectDep,
    item_id: str,
    request: OngoingDomainReferenceUpdateRequest,
) -> OngoingItemsProjection:
    """设置/清除目标的领域引用；领域状态仍在读取时由领域提供方解析。"""

    try:
        service.set_domain_reference(
            subject.account_id,
            item_id,
            request.reference,
            version=request.version,
        )
    except AtomicProfileError as exc:
        raise atomic_error(exc, "ongoing_domain_reference_failed") from exc
    return service.list_items(subject.account_id)


__all__ = ["router"]
