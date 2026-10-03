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
from bridges.state_copy import MODEL_CALL_ERROR_TEMPLATES, client_error_template
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


#: 模型调用稳定错误码 → 用户可见中文文案（唯一来源：``bridges.state_copy``
#: 的固定文案注册表；与聊天链路的领域码分开登记，供应商层原始 message 是
#: 内部诊断，绝不原样透传给用户）。未映射的 code 由调用方回退自身文案。
MODEL_CALL_ERROR_MESSAGES_ZH: dict[str, str] = MODEL_CALL_ERROR_TEMPLATES


def user_facing_model_error(code: str | None, fallback: str) -> str:
    """把模型调用稳定错误码映射为中文文案（Issue 06 第七轮：真实错误透传）。

    ``client_error_<status>`` 形态由固定文案注册表按状态码生成文案：4xx
    里的请求拒绝（重试不会恢复）与瞬时状态分开措辞；未映射的 code 使用
    调用方提供的回退文案，绝不把供应商原始 message 原样透传。
    """
    mapped = MODEL_CALL_ERROR_MESSAGES_ZH.get(code)  # type: ignore[arg-type]
    if mapped is not None:
        return mapped
    template = client_error_template(code)
    if template is not None:
        return template.text
    return fallback
