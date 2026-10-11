"""贴吧专用浏览器手动控制端点的 410 兼容合同。"""

from typing import Any

from fastapi import APIRouter, Request, status

from bridges.api.auth import SubjectDep
from bridges.contracts.retirement import RetiredCapabilityError
from bridges.retirement import raise_retired_capability

router = APIRouter(prefix="/tieba/browser", tags=["贴吧信息搜集"])
_RETIRED_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_410_GONE: {"model": RetiredCapabilityError, "description": "Gone"},
    status.HTTP_401_UNAUTHORIZED: {"model": dict},
}


def _retired(request: Request, endpoint: str) -> None:
    raise_retired_capability(
        request,
        endpoint=endpoint,
        error="tieba_browser_controls_retired",
        replacement_path="/",
        message="手动打开或关闭贴吧浏览器的接口已退役；请从聊天入口使用贴吧信息搜集，登录检查与任务续接会自动处理。",
    )


@router.post(
    "/open",
    response_model=None,
    status_code=status.HTTP_410_GONE,
    responses=_RETIRED_RESPONSES,
)
def open_browser(request: Request, _subject: SubjectDep) -> None:
    """稳定退役旧的手动打开登录窗口入口。"""
    _retired(request, "legacy.tieba.browser.open")


@router.post(
    "/complete",
    response_model=None,
    status_code=status.HTTP_410_GONE,
    responses=_RETIRED_RESPONSES,
)
def complete_browser(request: Request, _subject: SubjectDep) -> None:
    """稳定退役旧的手动完成登录入口。"""
    _retired(request, "legacy.tieba.browser.complete")


__all__ = ["router"]
