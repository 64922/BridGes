"""审计事件流：业务服务使用的观测接口（深模块）。

应用代码只通过本模块发出与查询审计事件、生成运行摘要；SLI/SLO 注册
与告警由 ``sli_registry`` / ``alert_manager`` 独立模块直接消费，不再
经门面转发（删除纯委托层，调用方只见一个接口）。
"""

from __future__ import annotations

import threading
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
        self._compatibility_observations: dict[tuple[str, str, str, int], int] = {}
        self._compatibility_observations_lock = threading.Lock()
        self._profile_metrics: dict[tuple[str, str], int] = {}
        self._profile_queue_depth = 0
        self._profile_metrics_lock = threading.Lock()

    def record_compatibility_410(
        self,
        *,
        endpoint_id: str,
        service_version: str,
        traffic_class: str,
        status_code: int = 410,
    ) -> None:
        """记录兼容窗口 410 计数，不携带账户、对象或请求正文。"""
        self.record_compatibility(
            endpoint_id=endpoint_id,
            service_version=service_version,
            traffic_class=traffic_class,
            status_code=status_code,
        )

    def record_compatibility(
        self,
        *,
        endpoint_id: str,
        service_version: str,
        traffic_class: str,
        status_code: int,
    ) -> None:
        """记录兼容窗口观测，不携带账户、对象或请求正文。"""
        if traffic_class not in {"real", "probe"}:
            raise ValueError("traffic_class 必须是 real 或 probe。")
        key = (endpoint_id, service_version, traffic_class, status_code)
        with self._compatibility_observations_lock:
            self._compatibility_observations[key] = (
                self._compatibility_observations.get(key, 0) + 1
            )

    def compatibility_gate_snapshot(self) -> list[dict[str, str | int]]:
        """返回可写入 COMPATIBILITY-GATE.md 的稳定路由级观察摘要。"""
        with self._compatibility_observations_lock:
            observations = list(self._compatibility_observations.items())
        return [
            {
                "endpoint_id": endpoint_id,
                "service_version": service_version,
                "traffic_class": traffic_class,
                "status_code": status_code,
                "count": count,
            }
            for (endpoint_id, service_version, traffic_class, status_code), count in observations
        ]

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

    def record_profile_outcome(
        self, *, outcome: str, reason: str, exhausted: bool = False
    ) -> None:
        """记录不带账户、消息或正文的画像聚合指标。"""

        safe_reason = (
            reason
            if reason and all(character.isalnum() or character == "_" for character in reason)
            else "other"
        )
        with self._profile_metrics_lock:
            self._increment_profile_metric("profile_extraction_total", outcome)
            self._increment_profile_metric("profile_extraction_reason_total", safe_reason)
            if outcome == "permanent_failure":
                self._increment_profile_metric("profile_permanent_failure_total", "total")
            if outcome == "pending_retry":
                self._increment_profile_metric("profile_transient_retry_total", "total")
            if exhausted:
                self._increment_profile_metric("profile_retry_exhausted_total", "total")

    def record_profile_page_status(self, status: str) -> None:
        """记录画像状态接口请求的稳定状态标签。"""

        with self._profile_metrics_lock:
            self._increment_profile_metric("profile_page_status_request_total", status)

    def record_profile_queue_depth(self, depth: int) -> None:
        """更新画像重试队列深度 gauge。"""

        with self._profile_metrics_lock:
            self._profile_queue_depth = max(0, depth)

    def profile_metrics_snapshot(self) -> dict[str, int]:
        """返回可供监控端消费的脱敏画像指标快照。"""

        with self._profile_metrics_lock:
            snapshot = {
                f"{metric}:{label}": count
                for (metric, label), count in self._profile_metrics.items()
            }
            snapshot["profile_queue_depth"] = self._profile_queue_depth
            return snapshot

    def _increment_profile_metric(self, metric: str, label: str) -> None:
        key = (metric, label)
        self._profile_metrics[key] = self._profile_metrics.get(key, 0) + 1

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
