"""已退役的旧插件管理路由。"""

from __future__ import annotations

from fastapi import APIRouter, Request

from bridges.retirement import plugin_endpoint, raise_retired_capability

router = APIRouter(prefix="/plugins", tags=["plugins"])


@router.api_route(
    "",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
def retired_plugins(request: Request) -> None:
    raise_retired_capability(
        request,
        endpoint=plugin_endpoint(request.method, ""),
    )


@router.api_route(
    "/{legacy_path:path}",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
def retired_plugin_path(request: Request, legacy_path: str = "") -> None:
    """不解析路径参数、认证信息或请求正文，统一返回稳定 410。"""
    raise_retired_capability(
        request,
        endpoint=plugin_endpoint(request.method, legacy_path),
    )


__all__ = ["router"]
