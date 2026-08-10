"""兼容窗口的页面重定向观测入口。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status

from bridges.retirement import record_compatibility_observation

router = APIRouter(prefix="/compatibility", tags=["compatibility"])

_RETIRED_PAGE_ENDPOINTS = frozenset(
    {
        "legacy.pages.learning_projects.list",
        "legacy.pages.learning_projects.detail",
        "legacy.pages.tasks",
        "legacy.pages.plugins",
        "legacy.pages.mcp",
    }
)


@router.post("/pages/{endpoint_id}", status_code=status.HTTP_204_NO_CONTENT)
def observe_retired_page_redirect(endpoint_id: str, request: Request) -> Response:
    """记录一次旧页面重定向，不读取账户、查询参数或请求正文。"""

    if endpoint_id not in _RETIRED_PAGE_ENDPOINTS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "compatibility_route_not_found", "message": "兼容路由不存在。"},
        )
    record_compatibility_observation(request, endpoint_id, status_code=307)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
