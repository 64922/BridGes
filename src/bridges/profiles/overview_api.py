"""整体概览 API 路由（画像工单 15）。

概览只读有效条目派生：每条陈述逐段携带条目标识与版本，供页面点击定位
依据与编辑入口；失效与重建守卫在服务端完成，读取时若恰好发生竞争，如实
返回「部分可用」或「整理中」，绝不把旧概览冒充当前事实。路由并入
:mod:`bridges.profiles.api` 的 ``/profiles`` 前缀。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status

from bridges.api.auth import SubjectDep
from bridges.contracts.profile_overview import ProfileOverviewProjection
from bridges.contracts.profiles import ProfileError
from bridges.profiles.overview import ProfileOverviewService

# 路由并入 bridges.profiles.api 的 /profiles 前缀与 profiles 标签：含前缀的
# 父路由 include 本无前缀无标签子路由，避免双前缀与标签重复。
router = APIRouter()


def _get_profile_overview_service(request: Request) -> ProfileOverviewService:
    service: ProfileOverviewService | None = getattr(
        request.app.state, "profile_overview_service", None
    )
    if service is None:
        raise RuntimeError(
            "ProfileOverviewService not attached to application state."
        )
    return service


ProfileOverviewServiceDep = Annotated[
    ProfileOverviewService, Depends(_get_profile_overview_service)
]


@router.get(
    "/overview",
    response_model=ProfileOverviewProjection,
    responses={status.HTTP_401_UNAUTHORIZED: {"model": ProfileError}},
)
async def get_profile_overview(
    service: ProfileOverviewServiceDep,
    subject: SubjectDep,
) -> ProfileOverviewProjection:
    """当前账户的整体概览（主题成组、逐段可定位；状态如实）。"""

    return service.list_overview(subject.account_id)


__all__ = ["router"]
