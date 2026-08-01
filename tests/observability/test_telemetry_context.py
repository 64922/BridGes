"""Tests for telemetry correlation context propagation."""

from bridges.contracts.projects import ObjectDomain
from bridges.observability.telemetry_context import (
    TelemetryCorrelationScope,
    build_correlation,
    get_correlation,
    new_trace_id,
)


def test_build_correlation_generates_trace_and_span_ids() -> None:
    correlation = build_correlation(run_id="run-1")
    assert correlation.trace_id
    assert correlation.span_id
    assert correlation.run_id == "run-1"


def test_build_correlation_pseudonymizes_identifiers() -> None:
    correlation = build_correlation(
        run_id="run-1",
        account_id="account-alice",
        project_id="project-1",
        tenant_id="tenant-alpha",
    )
    assert correlation.account_hash
    assert correlation.account_hash != "account-alice"
    assert correlation.project_hash
    assert correlation.project_hash != "project-1"
    assert correlation.tenant_hash
    assert correlation.tenant_hash != "tenant-alpha"


def test_build_correlation_preserves_workflow_metadata() -> None:
    correlation = build_correlation(
        run_id="run-1",
        account_id="account-alice",
        object_domain=ObjectDomain.PERSONAL_VAULT,
        workflow_name="demo_lesson",
        workflow_version="1",
        authorization_version="authz-1.0",
        key_epoch="epoch-0",
    )
    assert correlation.object_domain == ObjectDomain.PERSONAL_VAULT
    assert correlation.workflow_name == "demo_lesson"
    assert correlation.workflow_version == "1"
    assert correlation.authorization_version == "authz-1.0"
    assert correlation.key_epoch == "epoch-0"


def test_correlation_scope_sets_context_locally() -> None:
    assert get_correlation() is None
    with TelemetryCorrelationScope(run_id="run-1", account_id="account-alice") as c1:
        current = get_correlation()
        assert current is c1
        assert current is not None
        assert current.run_id == "run-1"
    assert get_correlation() is None


def test_trace_ids_are_unique() -> None:
    assert new_trace_id() != new_trace_id()
