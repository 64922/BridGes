"""Unified observability service facade.

Combines telemetry context, audit logging, run summary generation, SLI/SLO
registry and alert management behind a single interface used by API routes,
services and workers.
"""

from __future__ import annotations

from typing import Any

from bridges.contracts.observability import (
    AlertRecord,
    AlertState,
    AuditAction,
    AuditEvent,
    AuditResult,
    RunSummary,
    SLIMetricKind,
    SLIRecord,
    SLISeverity,
    SLORecord,
    TelemetryCorrelation,
)
from bridges.contracts.scope import ScopeEnvelope
from bridges.contracts.workflows import RunProjection
from bridges.observability.alert_manager import AlertManager, AlertManagerError
from bridges.observability.audit_event import AuditEventLogger
from bridges.observability.run_summary import build_run_summary
from bridges.observability.sli_registry import SLIRegistry, SLIRegistryError
from bridges.observability.telemetry_context import build_correlation


class ObservabilityService:
    """Facade used by application code to emit telemetry and manage SLOs/alerts."""

    def __init__(
        self,
        audit_logger: AuditEventLogger | None = None,
        sli_registry: SLIRegistry | None = None,
        alert_manager: AlertManager | None = None,
    ) -> None:
        self._audit = audit_logger or AuditEventLogger()
        self._sli = sli_registry or SLIRegistry()
        self._alerts = alert_manager or AlertManager()

    # Audit

    def log_audit(
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
        """Emit a privacy-scrubbed audit event."""
        return self._audit.log(
            actor_account_id=actor_account_id,
            action=action,
            result=result,
            actor_session_id=actor_session_id,
            object_refs=object_refs,
            scope_envelope=scope_envelope,
            reason=reason,
            correlation=correlation,
            details=details,
        )

    def get_run_audit_events(self, run_id: str) -> list[AuditEvent]:
        """Return all audit events for a run."""
        return self._audit.get_events_for_run(run_id)

    def list_audit_events(
        self,
        *,
        account_id: str | None = None,
        action: AuditAction | None = None,
    ) -> list[AuditEvent]:
        """Return audit events filtered by account and/or action."""
        return self._audit.list_events(account_id=account_id, action=action)

    # Run summaries

    def summarize_run(
        self,
        projection: RunProjection,
        *,
        audit_event_refs: list[str] | None = None,
        terminal_reason: str | None = None,
        correlation: TelemetryCorrelation | None = None,
    ) -> RunSummary:
        """Build a privacy-preserving run summary."""
        return build_run_summary(
            projection,
            audit_event_refs=audit_event_refs,
            terminal_reason=terminal_reason,
            correlation=correlation,
        )

    # SLI/SLO

    def register_sli(
        self,
        *,
        workload_name: str,
        metric_kind: SLIMetricKind,
        description: str,
        unit: str,
        window: str,
        owner: str,
        runbook_url: str | None = None,
        sli_id: str | None = None,
    ) -> SLIRecord:
        """Register a workload SLI."""
        return self._sli.register_sli(
            workload_name=workload_name,
            metric_kind=metric_kind,
            description=description,
            unit=unit,
            window=window,
            owner=owner,
            runbook_url=runbook_url,
            sli_id=sli_id,
        )

    def register_slo(
        self,
        *,
        sli_id: str,
        target: float,
        alert_threshold: float,
        severity: SLISeverity = SLISeverity.HIGH,
        slo_id: str | None = None,
    ) -> SLORecord:
        """Register an SLO bound to an SLI."""
        return self._sli.register_slo(
            sli_id=sli_id,
            target=target,
            alert_threshold=alert_threshold,
            severity=severity,
            slo_id=slo_id,
        )

    def list_slis(self, workload_name: str | None = None) -> list[SLIRecord]:
        """Return registered SLIs."""
        return self._sli.list_slis(workload_name=workload_name)

    # Alerts

    def fire_alert(
        self,
        *,
        dedup_key: str,
        sli_id: str,
        severity: SLISeverity,
        owner: str,
        summary: str,
        runbook_url: str | None = None,
        evidence_refs: list[str] | None = None,
    ) -> AlertRecord:
        """Fire or deduplicate an alert."""
        return self._alerts.fire(
            dedup_key=dedup_key,
            sli_id=sli_id,
            severity=severity,
            owner=owner,
            summary=summary,
            runbook_url=runbook_url,
            evidence_refs=evidence_refs,
        )

    def acknowledge_alert(self, *, dedup_key: str, acknowledged_by: str) -> AlertRecord:
        """Acknowledge an alert."""
        return self._alerts.acknowledge(dedup_key=dedup_key, acknowledged_by=acknowledged_by)

    def resolve_alert(self, *, dedup_key: str, resolution_evidence: str) -> AlertRecord:
        """Resolve an alert with required evidence."""
        return self._alerts.resolve(
            dedup_key=dedup_key,
            resolution_evidence=resolution_evidence,
        )

    def list_alerts(
        self,
        *,
        state: AlertState | None = None,
        owner: str | None = None,
    ) -> list[AlertRecord]:
        """Return alerts."""
        return self._alerts.list_alerts(state=state, owner=owner)
