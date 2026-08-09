"""已退役的旧通用 MCP 管理与调用路由。"""

from __future__ import annotations

from fastapi import APIRouter, Request

from bridges.retirement import mcp_endpoint, raise_retired_capability

router = APIRouter(prefix="/mcp", tags=["mcp"])


@router.api_route(
    "",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
def retired_mcp(request: Request) -> None:
    raise_retired_capability(
        request,
        endpoint=mcp_endpoint(request.method, ""),
    )


@router.api_route(
    "/{legacy_path:path}",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
def retired_mcp_path(request: Request, legacy_path: str = "") -> None:
    """不读取旧 MCP 的描述、调用参数或确认正文。"""
    raise_retired_capability(
        request,
        endpoint=mcp_endpoint(request.method, legacy_path),
    )


__all__ = ["router"]
