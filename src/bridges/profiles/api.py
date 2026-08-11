"""四维画像的公开读、改、撤回合同。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from bridges.api.auth import SubjectDep
from bridges.contracts.profile_extraction import ProfileStatusProjection
from bridges.contracts.profiles import (
    FourDimensionProfileDeleteRequest,
    FourDimensionProfileModifyRequest,
    FourDimensionProfileProjection,
    FourDimensionProfileRecord,
    FourDimensionProfileWithdrawRequest,
    ProfileError,
)
from bridges.profiles.automatic import AutomaticProfileService
from bridges.profiles.four_dimensions import (
    FourDimensionProfileError,
    FourDimensionProfileService,
)

router = APIRouter(prefix="/profiles", tags=["profiles"])


def _get_four_dimension_profile_service(request: Request) -> FourDimensionProfileService:
    service: FourDimensionProfileService | None = getattr(
        request.app.state, "four_dimension_profile_service", None
    )
    if service is None:
        raise RuntimeError("FourDimensionProfileService not attached to application state.")
    return service


FourDimensionProfileServiceDep = Annotated[
    FourDimensionProfileService, Depends(_get_four_dimension_profile_service)
]


def _get_automatic_profile_service(request: Request) -> AutomaticProfileService:
    service: AutomaticProfileService | None = getattr(
        request.app.state, "automatic_profile_service", None
    )
    if service is None:
        raise RuntimeError("AutomaticProfileService not attached to application state.")
    return service


AutomaticProfileServiceDep = Annotated[
    AutomaticProfileService, Depends(_get_automatic_profile_service)
]


def _profile_error(status_code: int, error: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=ProfileError(error=error, message=message).model_dump(),
    )


def _four_dimension_error(
    exc: FourDimensionProfileError, failure_code: str
) -> HTTPException:
    message = str(exc)
    if "对象不存在" in message or "访问权限" in message:
        return _profile_error(status.HTTP_404_NOT_FOUND, "four_dimension_not_found", message)
    if "版本冲突" in message or "已撤回" in message:
        return _profile_error(status.HTTP_409_CONFLICT, "four_dimension_conflict", message)
    return _profile_error(status.HTTP_422_UNPROCESSABLE_CONTENT, failure_code, message)


@router.get(
    "/four-dimensions",
    response_model=list[FourDimensionProfileProjection],
    responses={status.HTTP_401_UNAUTHORIZED: {"model": ProfileError}},
)
async def list_four_dimension_records(
    service: FourDimensionProfileServiceDep,
    subject: SubjectDep,
) -> list[FourDimensionProfileRecord]:
    """列出当前账户的活动四维画像记录。"""
    return service.list_records(subject.account_id)


@router.get(
    "/status",
    response_model=ProfileStatusProjection,
    responses={status.HTTP_401_UNAUTHORIZED: {"model": ProfileError}},
)
async def profile_status(
    request: Request,
    service: AutomaticProfileServiceDep,
    subject: SubjectDep,
) -> ProfileStatusProjection:
    """Return only the current account's extraction and record status."""

    projection = service.profile_status(subject.account_id)
    observability = getattr(request.app.state, "observability_service", None)
    if observability is not None:
        observability.record_profile_page_status(projection.status.value)
    return projection


@router.patch(
    "/four-dimensions/{record_id}",
    response_model=FourDimensionProfileProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProfileError},
        status.HTTP_404_NOT_FOUND: {"model": ProfileError},
        status.HTTP_409_CONFLICT: {"model": ProfileError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ProfileError},
    },
)
async def modify_four_dimension_record(
    service: FourDimensionProfileServiceDep,
    subject: SubjectDep,
    record_id: str,
    request: FourDimensionProfileModifyRequest,
) -> FourDimensionProfileRecord:
    """修改一条已有记录，且不重置首次稳定记录时间。"""
    try:
        return service.modify_record(subject.account_id, record_id, request)
    except FourDimensionProfileError as exc:
        raise _four_dimension_error(exc, "four_dimension_modify_failed") from exc


@router.post(
    "/four-dimensions/{record_id}/withdraw",
    response_model=FourDimensionProfileProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProfileError},
        status.HTTP_404_NOT_FOUND: {"model": ProfileError},
        status.HTTP_409_CONFLICT: {"model": ProfileError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ProfileError},
    },
)
async def withdraw_four_dimension_record(
    service: FourDimensionProfileServiceDep,
    subject: SubjectDep,
    record_id: str,
    request: FourDimensionProfileWithdrawRequest,
) -> FourDimensionProfileRecord:
    """撤回一条记录，同时保留内部撤回账本。"""
    try:
        return service.withdraw_record(subject.account_id, record_id, request.version)
    except FourDimensionProfileError as exc:
        raise _four_dimension_error(exc, "four_dimension_withdraw_failed") from exc


@router.delete(
    "/four-dimensions/{record_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProfileError},
        status.HTTP_404_NOT_FOUND: {"model": ProfileError},
        status.HTTP_409_CONFLICT: {"model": ProfileError},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ProfileError},
    },
)
async def delete_four_dimension_record(
    service: FourDimensionProfileServiceDep,
    subject: SubjectDep,
    record_id: str,
    request: FourDimensionProfileDeleteRequest,
) -> Response:
    """永久删除一条记录及其观察，不写入撤回墓碑。"""
    try:
        service.delete_record(subject.account_id, record_id, request.version)
    except FourDimensionProfileError as exc:
        raise _four_dimension_error(exc, "four_dimension_delete_failed") from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
