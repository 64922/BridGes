"""Shared cross-cutting contracts."""

from .health import DependencyHealth, HealthProjection, HealthStatus
from .identity import (
    Account,
    AccountRegistration,
    AuthError,
    AuthMethod,
    AuthResponse,
    LoginCredential,
    RecoveryRequest,
    RecoveryReset,
    Session,
    SessionResponse,
    SubjectContext,
)
from .scope import (
    BackgroundTaskEnvelope,
    RLSContext,
    ScopeAction,
    ScopeCacheKey,
    ScopeEnvelope,
    ScopeIsolationError,
    ScopeViolationReport,
)

__all__ = [
    "Account",
    "AccountRegistration",
    "AuthError",
    "AuthMethod",
    "AuthResponse",
    "BackgroundTaskEnvelope",
    "DependencyHealth",
    "HealthProjection",
    "HealthStatus",
    "LoginCredential",
    "RecoveryRequest",
    "RecoveryReset",
    "RLSContext",
    "ScopeAction",
    "ScopeCacheKey",
    "ScopeEnvelope",
    "ScopeIsolationError",
    "ScopeViolationReport",
    "Session",
    "SessionResponse",
    "SubjectContext",
]
