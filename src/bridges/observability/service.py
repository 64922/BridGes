"""审计事件流：业务服务使用的观测接口（深模块）。

应用代码只通过本模块发出与查询审计事件、生成运行摘要；SLI/SLO 注册
与告警由 ``sli_registry`` / ``alert_manager`` 独立模块直接消费，不再
经门面转发（删除纯委托层，调用方只见一个接口）。
"""

from __future__ import annotations

from typing import Any

from bridges.contracts.observability import (
    AuditAction,
    AuditEvent,
    AuditResult,
    RunSummary,
    TelemetryCorrelation,
)
from bridges.contracts.scope import ScopeEnvelope
from bridges.contracts.workflows import RunProjection
from bridges.observability.audit_event import AuditEventLogger
from bridges.observability.run_summary import build_run_summary


class ObservabilityService:
    """审计事件流接口：记录（脱敏）、查询与运行摘要。"""

    def __init__(self, audit_logger: AuditEventLogger | None = None) -> None:
        self._audit = audit_logger or AuditEventLogger()

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
