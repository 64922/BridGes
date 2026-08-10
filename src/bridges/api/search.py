"""跨内容统一桌面搜索 API 路由（Issue 24）。

单端点 ``GET /search`` 覆盖聊天、图片与文档三类账户级内容。
全部实时查询权威数据库，不依赖进程内缓存。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from bridges.api.auth import SubjectDep
from bridges.contracts.search import ALL_RESULT_TYPES, SearchResponse, SearchResultType
from bridges.search import SearchService

router = APIRouter(prefix="/search", tags=["search"])

_VALID_TYPES = frozenset(ALL_RESULT_TYPES)


def _get_search_service(request: Request) -> SearchService:
    service: SearchService | None = getattr(request.app.state, "search_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "search_unavailable",
                "message": "统一搜索服务未启用，当前实例拒绝搜索。",
            },
        )
    return service


SearchServiceDep = Annotated[SearchService, Depends(_get_search_service)]


def _parse_types(raw_types: list[str] | None) -> set[SearchResultType] | None:
    """解析类型筛选：支持重复参数与逗号分隔混用；非法值按 422 中文拒绝。"""
    if not raw_types:
        return None
    requested: set[str] = set()
    for raw in raw_types:
        for token in raw.split(","):
            token = token.strip()
            if token:
                requested.add(token)
    invalid = sorted(requested - _VALID_TYPES)
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "error": "invalid_types",
                "message": (
                    f"不支持的结果类型：{'、'.join(invalid)}；"
                    f"可选值为 {'、'.join(ALL_RESULT_TYPES)}。"
                ),
            },
        )
    return cast(set[SearchResultType], requested & _VALID_TYPES)


@router.get(
    "",
    response_model=SearchResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": dict},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": dict},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": dict},
    },
)
def unified_search(
    service: SearchServiceDep,
    subject: SubjectDep,
    q: str = "",
    types: Annotated[
        list[str] | None,
        Query(
            description=(
                "结果类型筛选：chat/image/document，"
                "支持重复参数（types=chat&types=document）或逗号分隔（types=chat,document）。"
            )
        ),
    ] = None,
    from_: Annotated[
        datetime | None,
        Query(alias="from", description="起始时间（ISO 8601，作用于结果最近更新时间）。"),
    ] = None,
    to: Annotated[
        datetime | None, Query(description="截止时间（ISO 8601，作用于结果最近更新时间）。")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=50, description="合并结果上限，默认 10。")] = 10,
) -> SearchResponse:
    """跨内容统一搜索：空查询返回空结果；索引未就绪时结果照返并标记
    ``index_ready=false``，由前端显示「索引尚未就绪」而非「没有结果」。"""
    return service.search(
        subject.account_id,
        query=q,
        types=_parse_types(types),
        from_=from_,
        to=to,
        limit=limit,
    )
