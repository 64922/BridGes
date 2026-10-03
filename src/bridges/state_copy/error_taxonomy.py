"""错误模板分类：稳定码 → 真实失败类别与恢复方式（Issue 23）。

未读取、不支持、未配置、限流/超时、不可核实分开登记；``client_error_*``
形态按 HTTP 状态动态判定（4xx 请求拒绝不承诺重试恢复，408/429 与 5xx
仍属瞬时故障）。聊天、图片、视频、语音与学习流程共用本分类。
"""

from __future__ import annotations

from bridges.state_copy.catalog import ERROR_TEMPLATES
from bridges.state_copy.types import ErrorTemplate, FailureClass, RecoveryAction

_BY_CODE: dict[str, ErrorTemplate] = {
    template.code: template for template in ERROR_TEMPLATES
}

#: 语义上仍是瞬时的 4xx：408 请求超时、429 限流（与 ai.errors 既有分类一致）。
_RETRYABLE_CLIENT_ERROR_STATUSES = frozenset({"408", "429"})


def error_template(code: str | None) -> ErrorTemplate | None:
    """稳定错误码 → 登记的错误模板；未知码返回 ``None``。"""
    if code is None:
        return None
    template = _BY_CODE.get(code)
    if template is not None:
        return template
    return client_error_template(code)


def client_error_template(code: str | None) -> ErrorTemplate | None:
    """``client_error_<status>`` 形态的动态模板；其他码返回 ``None``。

    模型调用兜底只消费共享码与动态 HTTP 码，不能把携带具体上下文的
    聊天领域码（如父图节点错误）覆盖成通用文案。
    """
    if code is not None and code.startswith("client_error_"):
        return _client_error_template(code)
    return None


def error_failure_class(code: str | None) -> FailureClass | None:
    """稳定错误码的真实失败类别；未知码返回 ``None``（不臆造分类）。"""
    template = error_template(code)
    return template.failure_class if template is not None else None


def error_recovery(code: str | None) -> RecoveryAction | None:
    """稳定错误码的真实恢复方式；未知码返回 ``None``。"""
    template = error_template(code)
    return template.recovery if template is not None else None


def _client_error_template(code: str) -> ErrorTemplate:
    status = code.removeprefix("client_error_")
    if status.isdigit() and 400 <= int(status) < 500:
        if status in _RETRYABLE_CLIENT_ERROR_STATUSES:
            return ErrorTemplate(
                code=code,
                text=f"模型服务返回错误（HTTP {status}），请稍后重试。",
                failure_class=FailureClass.RATE_LIMIT_TIMEOUT,
                recovery=RecoveryAction.RETRY,
            )
        return ErrorTemplate(
            code=code,
            text=(
                f"模型拒绝了本次请求（HTTP {status}），重试不会恢复；"
                "请检查请求参数与该模型是否可用。"
            ),
            failure_class=FailureClass.UNSUPPORTED,
            recovery=RecoveryAction.NONE,
        )
    return ErrorTemplate(
        code=code,
        text=f"模型服务返回错误（HTTP {status}），请稍后重试。",
        failure_class=FailureClass.INTERNAL,
        recovery=RecoveryAction.RETRY,
    )


__all__ = [
    "client_error_template",
    "error_failure_class",
    "error_recovery",
    "error_template",
]
