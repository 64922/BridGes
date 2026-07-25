"""SLI/SLO registry.

Allows workloads to declare latency, availability, correctness, degradation and
cost indicators with ownership and runbook links. SLOs attach targets and alert
thresholds to registered SLIs.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from science_companion.contracts.observability import (
    SLIMetricKind,
    SLIRecord,
    SLISeverity,
    SLORecord,
)


class SLIRegistryError(Exception):
    """Domain error for SLI/SLO registry failures."""


class SLIRegistry:
    """In-memory SLI/SLO registry.

    Workloads register SLIs with stable identifiers; SLOs are bound to SLIs and
    declare target and alert threshold. Later tickets can replace the storage
    adapter while keeping the same interface.
    """

    def __init__(self) -> None:
        self._slis: dict[str, SLIRecord] = {}
        self._slos: dict[str, SLORecord] = {}

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
        """Register a new SLI."""
        now = datetime.now(timezone.utc)
        record_id = sli_id or secrets.token_urlsafe(16)
        record = SLIRecord(
            sli_id=record_id,
            workload_name=workload_name,
            metric_kind=metric_kind,
            description=description,
            unit=unit,
            window=window,
            owner=owner,
            runbook_url=runbook_url,
            created_at=now,
        )
        self._slis[record_id] = record
        return record

    def get_sli(self, sli_id: str) -> SLIRecord:
        """Return an SLI by identifier."""
        record = self._slis.get(sli_id)
        if record is None:
            raise SLIRegistryError(f"SLI not found: {sli_id}")
        return record

    def list_slis(self, workload_name: str | None = None) -> list[SLIRecord]:
        """Return all registered SLIs, optionally filtered by workload."""
        records = list(self._slis.values())
        if workload_name is not None:
            records = [r for r in records if r.workload_name == workload_name]
        return records

    def register_slo(
        self,
        *,
        sli_id: str,
        target: float,
        alert_threshold: float,
        severity: SLISeverity = SLISeverity.HIGH,
        slo_id: str | None = None,
    ) -> SLORecord:
        """Register an SLO bound to an existing SLI."""
        if sli_id not in self._slis:
            raise SLIRegistryError(f"Cannot register SLO for unknown SLI: {sli_id}")
        now = datetime.now(timezone.utc)
        record_id = slo_id or secrets.token_urlsafe(16)
        record = SLORecord(
            slo_id=record_id,
            sli_id=sli_id,
            target=target,
            alert_threshold=alert_threshold,
            severity=severity,
            created_at=now,
        )
        self._slos[record_id] = record
        return record

    def get_slo(self, slo_id: str) -> SLORecord:
        """Return an SLO by identifier."""
        record = self._slos.get(slo_id)
        if record is None:
            raise SLIRegistryError(f"SLO not found: {slo_id}")
        return record

    def list_slos_for_sli(self, sli_id: str) -> list[SLORecord]:
        """Return all SLOs for a given SLI."""
        return [r for r in self._slos.values() if r.sli_id == sli_id]
