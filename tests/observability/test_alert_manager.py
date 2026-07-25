"""Tests for the alert manager lifecycle."""

import pytest

from science_companion.contracts.observability import AlertState, SLISeverity
from science_companion.observability.alert_manager import AlertManager, AlertManagerError


def test_fire_alert_creates_firing_record() -> None:
    manager = AlertManager()
    alert = manager.fire(
        dedup_key="interactive-latency-high",
        sli_id="sli-1",
        severity=SLISeverity.HIGH,
        owner="platform",
        summary="P95 interactive latency exceeded threshold",
        runbook_url="https://runbooks.example/latency",
    )
    assert alert.alert_id
    assert alert.state == AlertState.FIRING
    assert alert.owner == "platform"
    assert alert.runbook_url == "https://runbooks.example/latency"


def test_fire_same_dedup_key_deduplicates() -> None:
    manager = AlertManager()
    first = manager.fire(
        dedup_key="dup",
        sli_id="sli-1",
        severity=SLISeverity.HIGH,
        owner="platform",
        summary="first",
    )
    second = manager.fire(
        dedup_key="dup",
        sli_id="sli-1",
        severity=SLISeverity.HIGH,
        owner="platform",
        summary="second",
    )
    assert first.alert_id == second.alert_id


def test_acknowledge_alert() -> None:
    manager = AlertManager()
    manager.fire(
        dedup_key="alert-1",
        sli_id="sli-1",
        severity=SLISeverity.HIGH,
        owner="platform",
        summary="alert",
    )
    acked = manager.acknowledge(dedup_key="alert-1", acknowledged_by="operator-1")
    assert acked.state == AlertState.ACKNOWLEDGED
    assert acked.acknowledged_by == "operator-1"
    assert acked.acknowledged_at is not None


def test_resolve_alert_requires_evidence() -> None:
    manager = AlertManager()
    manager.fire(
        dedup_key="alert-1",
        sli_id="sli-1",
        severity=SLISeverity.HIGH,
        owner="platform",
        summary="alert",
    )
    with pytest.raises(AlertManagerError, match="Resolution evidence"):
        manager.resolve(dedup_key="alert-1", resolution_evidence="")

    resolved = manager.resolve(
        dedup_key="alert-1",
        resolution_evidence="Rollback deployed; latency restored.",
    )
    assert resolved.state == AlertState.RESOLVED
    assert resolved.resolution_evidence == "Rollback deployed; latency restored."
    assert resolved.resolved_at is not None


def test_resolve_without_acknowledge_fails_for_nonexistent() -> None:
    manager = AlertManager()
    with pytest.raises(AlertManagerError, match="not found"):
        manager.resolve(dedup_key="missing", resolution_evidence="x")


def test_list_alerts_filters_by_state_and_owner() -> None:
    manager = AlertManager()
    manager.fire(
        dedup_key="a1",
        sli_id="sli-1",
        severity=SLISeverity.HIGH,
        owner="platform",
        summary="platform alert",
    )
    manager.fire(
        dedup_key="a2",
        sli_id="sli-2",
        severity=SLISeverity.MEDIUM,
        owner="data",
        summary="data alert",
    )
    assert len(manager.list_alerts(owner="platform")) == 1
    assert len(manager.list_alerts(state=AlertState.FIRING)) == 2
