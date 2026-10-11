"""认证账户的专用贴吧窗口；不接受 URL、账户 ID 或 Cookie。"""

from typing import cast

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from bridges.api.auth import SubjectDep
from bridges.tieba.browser_runtime import TiebaBrowserError, TiebaBrowserRuntime

router = APIRouter(prefix="/tieba/browser", tags=["贴吧信息搜集"])


class TiebaBrowserSetupResponse(BaseModel):
    """窗口状态不等于已验证登录状态，登录可用性由实际检索判定。"""

    state: str
    message: str


def runtime_of(request: Request) -> TiebaBrowserRuntime:
    runtime = getattr(request.app.state, "tieba_browser_runtime", None)
    if runtime is None:
        raise HTTPException(
            503,
            detail={
                "error": "tieba_browser_unavailable",
                "message": "当前运行环境未启用本地专用贴吧浏览器。",
            },
        )
    return cast(TiebaBrowserRuntime, runtime)


@router.post("/open", response_model=TiebaBrowserSetupResponse)
def open_browser(request: Request, subject: SubjectDep) -> TiebaBrowserSetupResponse:
    try:
        runtime_of(request).open(subject.account_id)
    except TiebaBrowserError as error:
        raise HTTPException(503, detail={"error": error.code, "message": str(error)}) from None
    return TiebaBrowserSetupResponse(
        state="opened", message="请在专用 Chrome 中手动完成贴吧验证或登录，再点击完成。"
    )


@router.post("/complete", response_model=TiebaBrowserSetupResponse)
def complete_browser(request: Request, subject: SubjectDep) -> TiebaBrowserSetupResponse:
    runtime_of(request).complete(subject.account_id)
    return TiebaBrowserSetupResponse(
        state="closed", message="专用窗口已关闭，会话已保存；请重新检索以核验是否可用。"
    )
