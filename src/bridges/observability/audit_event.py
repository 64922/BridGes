"""Append-only audit event logger.

Audit events are emitted for security-relevant and lifecycle actions. Each event
carries correlation identifiers and a structured result; it never contains
private body, full prompts, keys or raw model output.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any

from bridges.contracts.observability import (
    AuditAction,
    AuditEvent,
    AuditResult,
    TelemetryCorrelation,
)
from bridges.contracts.scope import ScopeEnvelope
from bridges.observability.scrubber import scrub_payload
from bridges.observability.telemetry_context import build_correlation, get_correlation


class AuditEventLogger:
    """In-memory audit event logger.

    Events are stored in insertion order and can be queried by account, run or
    action. A persistent adapter will later replace this implementation while
    keeping the same interface.
    """

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    def log(
        self,
        *,
        actor_account_id: str,
        action: AuditAction,
        result: AuditResult,
        actor_session_id: str | None = None,
        object_refs: list[str] | None = None,
        scope_envelope: ScopeEnvelope | None = None,
        reason: str | None = None,
        correlation: TelemetryCorrelation | None = None,
        details: dict[str, Any] | None = None,
    ) -> AuditEvent:
        """Log a privacy-scrubbed audit event."""
        now = datetime.now(timezone.utc)
        effective_correlation = correlation or get_correlation() or build_correlation()
        scrubbed_details, _ = scrub_payload(details or {})

        event = AuditEvent(
            event_id=secrets.token_urlsafe(16),
            occurred_at=now,
            actor_account_id=actor_account_id,
            actor_session_id=actor_session_id,
            action=action,
            object_refs=list(object_refs or []),
            scope_envelope=scope_envelope,
            result=result,
            reason=reason,
            correlation=effective_correlation,
            details=scrubbed_details,
        )
        self._events.append(event)
        return event

    def list_events(
        self,
        *,
        account_id: str | None = None,
        run_id: str | None = None,
        action: AuditAction | None = None,
    ) -> list[AuditEvent]:
        """Return matching audit events.

        The run_id is matched against the correlation.run_id field.
        """
        results = list(self._events)
        if account_id is not None:
            results = [e for e in results if e.actor_account_id == account_id]
        if run_id is not None:
            results = [e for e in results if e.correlation.run_id == run_id]
        if action is not None:
            results = [e for e in results if e.action == action]
        return results

    def get_events_for_run(self, run_id: str) -> list[AuditEvent]:
        """Return all audit events for a run, ordered by occurrence time."""
        return self.list_events(run_id=run_id)
