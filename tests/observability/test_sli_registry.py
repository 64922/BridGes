"""Tests for the SLI/SLO registry."""

import pytest

from bridges.contracts.observability import SLIMetricKind, SLISeverity
from bridges.observability.sli_registry import SLIRegistry, SLIRegistryError


def test_register_and_list_sli() -> None:
    registry = SLIRegistry()
    sli = registry.register_sli(
        workload_name="interactive",
        metric_kind=SLIMetricKind.LATENCY,
        description="P95 latency for interactive tasks",
        unit="ms",
        window="1m",
        owner="platform",
    )
    assert sli.sli_id
    assert sli.workload_name == "interactive"
    assert sli.metric_kind == SLIMetricKind.LATENCY

    all_slis = registry.list_slis()
    assert len(all_slis) == 1
    assert all_slis[0].sli_id == sli.sli_id


def test_list_slis_filters_by_workload() -> None:
    registry = SLIRegistry()
    registry.register_sli(
        workload_name="interactive",
        metric_kind=SLIMetricKind.LATENCY,
        description="interactive latency",
        unit="ms",
        window="1m",
        owner="platform",
    )
    registry.register_sli(
        workload_name="ingestion",
        metric_kind=SLIMetricKind.AVAILABILITY,
        description="ingestion availability",
        unit="ratio",
        window="5m",
        owner="data",
    )

    assert len(registry.list_slis(workload_name="interactive")) == 1
    assert len(registry.list_slis(workload_name="ingestion")) == 1
    assert len(registry.list_slis(workload_name="missing")) == 0


def test_register_slo_bound_to_sli() -> None:
    registry = SLIRegistry()
    sli = registry.register_sli(
        workload_name="interactive",
        metric_kind=SLIMetricKind.AVAILABILITY,
        description="availability",
        unit="ratio",
        window="1m",
        owner="platform",
    )
    slo = registry.register_slo(
        sli_id=sli.sli_id,
        target=0.99,
        alert_threshold=0.95,
        severity=SLISeverity.HIGH,
    )
    assert slo.sli_id == sli.sli_id
    assert slo.target == 0.99
    assert slo.alert_threshold == 0.95
    assert slo.severity == SLISeverity.HIGH


def test_register_slo_for_unknown_sli_fails() -> None:
    registry = SLIRegistry()
    with pytest.raises(SLIRegistryError, match="unknown SLI"):
        registry.register_slo(
            sli_id="missing",
            target=0.99,
            alert_threshold=0.95,
        )
