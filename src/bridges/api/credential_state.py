"""凭据与主模型的进程内验证状态（工单 01）。

设置页两张卡（凭据卡与主模型卡）以及地图代理都要读写同一份"最近一次验证
得到了什么结论"的状态，因此把它从路由模块里提出来，避免路由之间互相导入：

- 验证状态只活在**当前进程**里（重启后由凭据库里的值与下次验证重建），
  因此它只用于展示"最近一次验证"与"最近得到的失败原因"，不作为能力可用性
  的判定依据（CONTEXT：不得以探测快照判定能力可用性）；
- 每条状态带可选的 ``runtime_evidence``：写明该结论来自哪条真实请求路径，
  让卡片能区分"保存时验证过"与"真实运行路径上验证过"，只声称已被证明的事。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import Request

#: 凭据验证状态的进程内键（每个凭据项一个）。
_CREDENTIAL_VALIDATION_STATE_KEY = "credential_validation_state"
_MODEL_VALIDATION_STATE_KEY = "model_validation_state"


def validation_state(request: Request) -> dict[str, dict[str, Any]]:
    """返回凭据验证状态的进程内字典（不存在时就地创建）。"""
    state: dict[str, dict[str, Any]] | None = getattr(
        request.app.state, _CREDENTIAL_VALIDATION_STATE_KEY, None
    )
    if state is None:
        state = {}
        setattr(request.app.state, _CREDENTIAL_VALIDATION_STATE_KEY, state)
    return state


def record_validation(
    request: Request,
    name: str,
    *,
    error: str | None = None,
    runtime_evidence: str | None = None,
) -> datetime:
    """记录一次凭据验证结论（成功或失败），返回验证时间。

    ``error`` 为 None 表示这次验证通过；``runtime_evidence`` 只在结论来自真实
    运行路径（例如浏览器发出的地图请求）时给出，文案不得包含凭据正文。
    """
    checked_at = datetime.now(UTC)
    validation_state(request)[name] = {
        "last_validated_at": checked_at,
        "error": error,
        "runtime_evidence": runtime_evidence,
    }
    return checked_at


def validation_snapshot(request: Request, name: str) -> dict[str, Any]:
    """返回某个凭据项最近一次验证的结论（未验证过时空字典）。"""
    return validation_state(request).get(name, {})


__all__ = [
    "record_validation",
    "validation_snapshot",
    "validation_state",
]
