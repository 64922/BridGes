"""Scope isolation module.

Provides the `ScopeEnforcer` used by API routes, domain services, cache builders
and background-task validators, plus a reusable `ScopeFixtureFactory` for
multi-user isolation tests.
"""

from bridges.contracts.scope import (
    BackgroundTaskEnvelope,
    RLSContext,
    ScopeAction,
    ScopeCacheKey,
    ScopeEnvelope,
    ScopeIsolationError,
    ScopeViolationReport,
)
from bridges.scope.service import ScopeEnforcer, ScopeFixtureFactory

__all__ = [
    "BackgroundTaskEnvelope",
    "RLSContext",
    "ScopeAction",
    "ScopeCacheKey",
    "ScopeEnforcer",
    "ScopeEnvelope",
    "ScopeFixtureFactory",
    "ScopeIsolationError",
    "ScopeViolationReport",
]
