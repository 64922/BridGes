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
