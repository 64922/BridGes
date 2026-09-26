"""产品内置 SKILL 注册表与兼容窗口观察接口。"""

from __future__ import annotations

from fastapi import APIRouter, Request

from bridges.plugins.registry import create_builtin_plugin_manifests
from bridges.retirement import compatibility_metrics_for

router = APIRouter(tags=["skills"])


@router.get("/skills")
def list_builtin_skills(request: Request) -> list[dict[str, object]]:
    return [item.model_dump() for item in create_builtin_plugin_manifests()]


@router.get("/compatibility/observations")
def compatibility_observations(request: Request) -> dict[str, object]:
    return compatibility_metrics_for(request).snapshot()


__all__ = ["router"]
