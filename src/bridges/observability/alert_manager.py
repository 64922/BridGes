"""Basic alert manager with owner, runbook, deduplication and closure evidence."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any

from bridges.contracts.observability import (
    AlertRecord,
    AlertState,
    SLISeverity,
)


class AlertManagerError(Exception):
    """Domain error for alert lifecycle failures."""


class AlertManager:
    """In-memory alert manager.

    Alerts are keyed by a stable dedup_key so the same breach does not produce
    multiple firing records. Resolution requires evidence; status changes alone
    are rejected.
    """

    def __init__(self) -> None:
        self._alerts: dict[str, AlertRecord] = {}

    def fire(
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
        now = datetime.now(timezone.utc)
        existing = self._alerts.get(dedup_key)
        if existing is not None and existing.state in {AlertState.FIRING, AlertState.ACKNOWLEDGED}:
            return existing

        alert = AlertRecord(
            alert_id=secrets.token_urlsafe(16),
            dedup_key=dedup_key,
            sli_id=sli_id,
            severity=severity,
            owner=owner,
            summary=summary,
            runbook_url=runbook_url,
            state=AlertState.FIRING,
            evidence_refs=list(evidence_refs or []),
            created_at=now,
        )
        self._alerts[dedup_key] = alert
        return alert

    def get_alert(self, dedup_key: str) -> AlertRecord:
        """Return an alert by dedup key."""
        alert = self._alerts.get(dedup_key)
        if alert is None:
            raise AlertManagerError(f"Alert not found: {dedup_key}")
        return alert

    def acknowledge(
        self,
        *,
        dedup_key: str,
        acknowledged_by: str,
    ) -> AlertRecord:
        """Acknowledge a firing alert."""
        alert = self.get_alert(dedup_key)
        if alert.state != AlertState.FIRING:
            raise AlertManagerError("Only firing alerts can be acknowledged.")
        alert.state = AlertState.ACKNOWLEDGED
        alert.acknowledged_at = datetime.now(timezone.utc)
        alert.acknowledged_by = acknowledged_by
        return alert

    def resolve(
        self,
        *,
        dedup_key: str,
        resolution_evidence: str,
    ) -> AlertRecord:
        """Resolve an alert with required closure evidence."""
        if not resolution_evidence or not resolution_evidence.strip():
            raise AlertManagerError("Resolution evidence is required.")
        alert = self.get_alert(dedup_key)
        if alert.state not in {AlertState.FIRING, AlertState.ACKNOWLEDGED}:
            raise AlertManagerError("Only firing or acknowledged alerts can be resolved.")
        alert.state = AlertState.RESOLVED
        alert.resolved_at = datetime.now(timezone.utc)
        alert.resolution_evidence = resolution_evidence
        return alert

    def suppress(
        self,
        *,
        dedup_key: str,
        reason: str,
    ) -> AlertRecord:
        """Suppress an alert with a documented reason."""
        alert = self.get_alert(dedup_key)
        if alert.state == AlertState.RESOLVED:
            raise AlertManagerError("Resolved alerts cannot be suppressed.")
        alert.state = AlertState.SUPPRESSED
        alert.resolution_evidence = reason
        return alert

    def list_alerts(
        self,
        *,
        state: AlertState | None = None,
        owner: str | None = None,
    ) -> list[AlertRecord]:
        """Return alerts, optionally filtered by state or owner."""
        results = list(self._alerts.values())
        if state is not None:
            results = [a for a in results if a.state == state]
        if owner is not None:
            results = [a for a in results if a.owner == owner]
        return results
