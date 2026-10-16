"""记忆页学习引用区路由（画像工单 14）。

只读投影：本人学习目标与未被删除的学情判断（目标与记录的所有权在学习
领域，这里只引用实时状态与版本）。未接通学习领域时如实返回 503，不伪造
空列表冒充「没有学情」。路由并入 :mod:`bridges.profiles.api` 的
``/profiles`` 前缀。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status

from bridges.api.auth import SubjectDep
from bridges.contracts.learning_references import LearningReferencesProjection
from bridges.contracts.profiles import ProfileError
from bridges.profiles.api_errors import profile_error
from bridges.profiles.learning_references import (
    LearningReferencesService,
    LearningReferencesUnavailableError,
)

# 路由并入 bridges.profiles.api 的 /profiles 前缀与 profiles 标签：含前缀的
# 父路由 include 本无前缀无标签子路由，避免双前缀与标签重复。
router = APIRouter()


def _get_learning_references_service(request: Request) -> LearningReferencesService:
    service: LearningReferencesService | None = getattr(
        request.app.state, "learning_references_service", None
    )
    if service is None:
        raise RuntimeError(
            "LearningReferencesService not attached to application state."
        )
    return service


LearningReferencesServiceDep = Annotated[
    LearningReferencesService, Depends(_get_learning_references_service)
]


@router.get(
    "/learning-references",
    response_model=LearningReferencesProjection,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ProfileError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ProfileError},
    },
)
async def get_learning_references(
    service: LearningReferencesServiceDep,
    subject: SubjectDep,
) -> LearningReferencesProjection:
    """当前账户的学习引用：真实目标与学情判断的只读实时投影。"""

    try:
        return service.read(subject.account_id)
    except LearningReferencesUnavailableError as exc:
        raise profile_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "learning_references_unavailable",
            str(exc),
        ) from exc


__all__ = ["router"]
