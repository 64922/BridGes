"""Domain errors for model run lock recording.

These errors carry stable codes so callers and operators can reason about them
without parsing human-readable text.
"""

from __future__ import annotations

from bridges.contracts.ai import (
    MODEL_RUN_LOCK_CONFLICT,
    MODEL_RUN_LOCK_LINK_FAILED,
    MODEL_RUN_LOCK_PERSIST_FAILED,
    MODEL_RUN_LOCK_RECOVERY_REQUIRED,
    MODEL_RUN_LOCK_SCOPE_VIOLATION,
)
from bridges.storage.errors import StorageError


class ModelRunLockError(StorageError):
    """Base class for recorder persistence errors."""

    code: str = MODEL_RUN_LOCK_PERSIST_FAILED

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code or self.code


class ModelRunLockPersistError(ModelRunLockError):
    """Generic recorder persistence failure."""


class ModelRunLockConflictError(ModelRunLockError):
    """Same lock_id already exists with different canonical content."""

    code: str = MODEL_RUN_LOCK_CONFLICT


class ModelRunLockLinkError(ModelRunLockError):
    """Lock row was written but association could not be linked."""

    code: str = MODEL_RUN_LOCK_LINK_FAILED


class ModelRunLockRecoveryError(ModelRunLockError):
    """Business state committed but lock persistence needs recovery."""

    code: str = MODEL_RUN_LOCK_RECOVERY_REQUIRED


class ModelRunLockScopeError(ModelRunLockError):
    """Account scope was violated."""

    code: str = MODEL_RUN_LOCK_SCOPE_VIOLATION


class ModelRunLockSecurityError(ModelRunLockError):
    """Lock contained secrets or private body and was rejected."""

    code: str = "model_run_lock_security_rejected"


#: 模型调用稳定错误码 → 用户可见中文文案（结构化技能终态复用；与
#: ``bridges.chat.turn`` 的聊天流式映射同一来源，独立存放避免编排包
#: 循环依赖）。供应商层原始 message 是内部诊断，绝不原样透传给用户；
#: 未映射的 code 由调用方回退自身文案。
MODEL_CALL_ERROR_MESSAGES_ZH: dict[str, str] = {
    "rate_limit": "请求过于频繁（已触发限流），请稍后重试。",
    "transient": "连接中断或服务暂时不可用，请检查网络后重试。",
    "region_error": "无法连接 Qwen 服务，请检查网络后重试。",
    # Issue 03：ConnectError 细分（qwen_client.classify_connect_error）——
    # DNS 解析失败、代理不可达/被拒、TLS 证书校验失败三类可操作文案；
    # 无法判定时回落 region_error。
    "region_dns": "无法解析 Qwen 服务域名，请检查 DNS 或代理设置。",
    "region_proxy": "连接被代理拒绝，请检查代理配置。",
    "region_tls": "安全证书校验失败，可能存在 SSL 审查软件，请检查网络环境。",
    "auth_error": "Qwen API Key 无效或已失效，请检查启动服务的全局百炼配置与权限。",
    "provider_rejected": "供应商拒绝了本次请求，请稍后重试。",
    "safety_refusal": "模型拒绝了本次请求，请调整内容后重试。",
    "empty_response": "模型返回内容为空，请重试。",
    "cassette_missing": "离线回放模式缺少请求录像，请检查配置。",
    "structured_output_parse_failed": "模型输出不是合法 JSON，请重试。",
    "unsupported_structured_output_format": "结构化输出格式不受支持，请检查任务配置。",
    "invalid_response_format": "结构化输出格式参数无效，请检查任务配置。",
}


def user_facing_model_error(code: str | None, fallback: str) -> str:
    """把模型调用稳定错误码映射为中文文案（Issue 06 第七轮：真实错误透传）。

    ``client_error_<status>`` 形态按状态码生成通用文案；未映射的 code
    使用调用方提供的回退文案，绝不把供应商原始 message 原样透传。
    """
    if code in MODEL_CALL_ERROR_MESSAGES_ZH:
        return MODEL_CALL_ERROR_MESSAGES_ZH[code]
    if code is not None and code.startswith("client_error_"):
        status = code.removeprefix("client_error_")
        return f"模型服务返回错误（HTTP {status}），请稍后重试。"
    return fallback
