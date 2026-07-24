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

__all__ = [
    "Account",
    "AccountRegistration",
    "AuthError",
    "AuthMethod",
    "AuthResponse",
    "DependencyHealth",
    "HealthProjection",
    "HealthStatus",
    "LoginCredential",
    "RecoveryRequest",
    "RecoveryReset",
    "Session",
    "SessionResponse",
    "SubjectContext",
]
