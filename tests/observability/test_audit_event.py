"""Tests for the audit event logger."""

from science_companion.contracts.observability import AuditAction, AuditResult, TelemetryCorrelation
from science_companion.observability.audit_event import AuditEventLogger
from science_companion.observability.telemetry_context import build_correlation


def test_log_audit_event_with_correlation() -> None:
    logger = AuditEventLogger()
    correlation = build_correlation(run_id="run-1", account_id="account-alice")
    event = logger.log(
        actor_account_id="account-alice",
        action=AuditAction.RUN_ADVANCE,
        result=AuditResult.SUCCESS,
        correlation=correlation,
        details={"node_id": "n1"},
    )
    assert event.event_id
    assert event.action == AuditAction.RUN_ADVANCE
    assert event.result == AuditResult.SUCCESS
    assert event.correlation.run_id == "run-1"
    assert event.details["node_id"] == "n1"


def test_audit_details_are_scrubbed() -> None:
    logger = AuditEventLogger()
    event = logger.log(
        actor_account_id="account-alice",
        action=AuditAction.MODEL_INVOCATION,
        result=AuditResult.SUCCESS,
        details={"full_prompt": "secret prompt", "model_id": "qwen"},
    )
    assert event.details["full_prompt"] == "<redacted>"
    assert event.details["model_id"] == "qwen"


def test_list_events_by_run_id() -> None:
    logger = AuditEventLogger()
    logger.log(
        actor_account_id="account-alice",
        action=AuditAction.WORKORDER_SUBMIT,
        result=AuditResult.SUCCESS,
        correlation=build_correlation(run_id="run-1", account_id="account-alice"),
    )
    logger.log(
        actor_account_id="account-alice",
        action=AuditAction.RUN_ADVANCE,
        result=AuditResult.SUCCESS,
        correlation=build_correlation(run_id="run-1", account_id="account-alice"),
    )
    logger.log(
        actor_account_id="account-bob",
        action=AuditAction.WORKORDER_SUBMIT,
        result=AuditResult.SUCCESS,
        correlation=build_correlation(run_id="run-2", account_id="account-bob"),
    )

    run_events = logger.get_events_for_run("run-1")
    assert len(run_events) == 2
    assert logger.list_events(account_id="account-alice") == run_events
    assert len(logger.list_events(account_id="account-bob")) == 1
