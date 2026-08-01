"""Telemetry context propagation.

Provides context-local correlation identifiers so that traces, metrics, logs and
audit events emitted from any layer share the same run/subject/project/object
identifiers without threading them through every call signature.
"""

from __future__ import annotations

import contextvars
import hashlib
import secrets
from typing import Any

from bridges.contracts.observability import TelemetryCorrelation
from bridges.contracts.projects import ObjectDomain


CURRENT_CORRELATION: contextvars.ContextVar[TelemetryCorrelation | None] = contextvars.ContextVar(
    "bridges_observability_correlation",
    default=None,
)


def _hash_identifier(value: str) -> str:
    """Return a stable pseudonymized identifier."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def new_trace_id() -> str:
    """Generate a new trace identifier."""
    return secrets.token_hex(16)


def new_span_id() -> str:
    """Generate a new span identifier."""
    return secrets.token_hex(8)


def build_correlation(
    *,
    trace_id: str | None = None,
    span_id: str | None = None,
    run_id: str | None = None,
    account_id: str | None = None,
    project_id: str | None = None,
    tenant_id: str | None = None,
    object_domain: ObjectDomain | None = None,
    workflow_name: str | None = None,
    workflow_version: str | None = None,
    authorization_version: str | None = None,
    key_epoch: str | None = None,
) -> TelemetryCorrelation:
    """Build a correlation object with pseudonymized identifiers."""
    return TelemetryCorrelation(
        trace_id=trace_id or new_trace_id(),
        span_id=span_id or new_span_id(),
        run_id=run_id,
        account_hash=_hash_identifier(account_id) if account_id else None,
        project_hash=_hash_identifier(project_id) if project_id else None,
        tenant_hash=_hash_identifier(tenant_id) if tenant_id else None,
        object_domain=object_domain,
        workflow_name=workflow_name,
        workflow_version=workflow_version,
        authorization_version=authorization_version,
        key_epoch=key_epoch,
    )


def set_correlation(correlation: TelemetryCorrelation | None) -> contextvars.Token[TelemetryCorrelation | None]:
    """Set the current telemetry correlation context."""
    return CURRENT_CORRELATION.set(correlation)


def get_correlation() -> TelemetryCorrelation | None:
    """Return the current telemetry correlation context, if any."""
    return CURRENT_CORRELATION.get()


def reset_correlation(token: contextvars.Token[TelemetryCorrelation | None]) -> None:
    """Reset the correlation context from a token."""
    CURRENT_CORRELATION.reset(token)


class TelemetryCorrelationScope:
    """Context manager for correlation scope.

    Usage:
        with TelemetryCorrelationScope(run_id="...", account_id="..."):
            # all telemetry emitted here shares the same correlation
            audit.log(...)
    """

    def __init__(self, **kwargs: Any) -> None:
        self._correlation = build_correlation(**kwargs)
        self._token: contextvars.Token[TelemetryCorrelation | None] | None = None

    def __enter__(self) -> TelemetryCorrelation:
        self._token = set_correlation(self._correlation)
        return self._correlation

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        if self._token is not None:
            reset_correlation(self._token)
