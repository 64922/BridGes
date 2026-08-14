"""Observability, audit, SLO and alert contracts.

These models define the public surface of T010: unified trace/metric/log/audit
correlation, privacy-preserving run summaries, SLI/SLO registration, and basic
alert lifecycle. They are the authoritative shape of RunSummary, audit events,
OpenTelemetry correlation, SLI/SLO registry and AlertOwnership.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from bridges.contracts.projects import ObjectDomain
from bridges.contracts.scope import ScopeEnvelope

#: 媒体边缘动作（图片替代文本 / 图片/视频供应商取消）的稳定错误码
#: （Issue 16 Observability 合同）。这些动作必须与主生成链一样为每次
#: 真实供应商调用持久化模型运行锁；以下码用于审计 details，说明"本地
#: 已取消但云端通知未确认"或"远端调用发生但锁缺失/落库失败"，绝不冒充
#: 供应商成功。
MEDIA_EDGE_MISSING_RUN_LOCK = "media_edge_missing_run_lock"
MEDIA_EDGE_LOCK_PERSIST_FAILED = "media_edge_lock_persist_failed"
MEDIA_EDGE_CANCEL_PROVIDER_UNCONFIRMED = "media_cancel_provider_unconfirmed"
MEDIA_EDGE_CALL_COUNT_MISMATCH = "media_edge_call_count_mismatch"


class AuditAction(str, Enum):
    """Actions that can produce an audit event."""

    WORKORDER_SUBMIT = "workorder_submit"
    WORKORDER_CONFIRM = "workorder_confirm"
    RUN_ADVANCE = "run_advance"
    RUN_CANCEL = "run_cancel"
    HUMAN_TODO_RESOLVE = "human_todo_resolve"
    MODEL_INVOCATION = "model_invocation"
    SCOPE_DENY = "scope_deny"
    CAPABILITY_REGISTER = "capability_register"
    SLI_REGISTER = "sli_register"
    SLO_REGISTER = "slo_register"
    ALERT_FIRE = "alert_fire"
    ALERT_ACKNOWLEDGE = "alert_acknowledge"
    ALERT_RESOLVE = "alert_resolve"
    PROFILE_CREATE = "profile_create"
    PROFILE_CANDIDATE_PROPOSE = "profile_candidate_propose"
    PROFILE_CANDIDATE_DECISION = "profile_candidate_decision"
    PROFILE_FREEZE = "profile_freeze"
    PROFILE_WITHDRAW = "profile_withdraw"
    PROFILE_UNFREEZE = "profile_unfreeze"
    PROFILE_MODIFY = "profile_modify"
    PROFILE_DELETE = "profile_delete"
    PROFILE_ROLLBACK = "profile_rollback"
    PROFILE_EXPORT = "profile_export"
    PROFILE_PERMISSION_ENABLE = "profile_permission_enable"
    PROFILE_PERMISSION_DISABLE = "profile_permission_disable"
    PROFILE_AUTO_WRITE = "profile_auto_write"
    PROFILE_AUTO_WRITE_RECALL = "profile_auto_write_recall"
    PROFILE_INTENT = "profile_intent"
    PROFILE_SLICE_USED = "profile_slice_used"
    ANSWER_FEEDBACK = "answer_feedback"
    FEEDBACK_RESOLVED = "feedback_resolved"
    HUMANIZER_GENERATE = "humanizer_generate"
    HUMANIZER_RESULT_VIEW = "humanizer_result_view"
    #: Issue 04 不变量告警：人味化产物中出现画像原文（应不出现，触发即告警）。
    HUMANIZER_PROFILE_LEAK = "humanizer_profile_leak"
    CAREER_PLANNING_GENERATED = "career_planning_generated"
    ASR_TRANSCRIBE = "asr_transcribe"
    READ_ALOUD_GENERATE = "read_aloud_generate"
    READ_ALOUD_DELETE = "read_aloud_delete"
    IMAGE_TASK_SUBMIT = "image_task_submit"
    IMAGE_TASK_COMPLETE = "image_task_complete"
    IMAGE_TASK_CANCEL = "image_task_cancel"
    IMAGE_ASSET_DELETE = "image_asset_delete"
    IMAGE_ALT_TEXT_UPDATE = "image_alt_text_update"
    # Issue 16：图片替代文本的自动生成（核心视觉模型调用；details 只含
    # 来源/锁/稳定错误码，不含替代文本正文与图片内容）。
    IMAGE_ALT_TEXT_GENERATE = "image_alt_text_generate"
    VIDEO_TASK_SUBMIT = "video_task_submit"
    VIDEO_TASK_COMPLETE = "video_task_complete"
    VIDEO_TASK_CANCEL = "video_task_cancel"
    VIDEO_ASSET_DELETE = "video_asset_delete"
    VIDEO_DESCRIPTION_UPDATE = "video_description_update"
    # 旧合同遗留事件（ADR-0005 账户级百炼密钥，GQ-07 已清退）：枚举值
    # 必须保留，供历史审计记录解码；新版本不再产生这些事件。
    KEY_SAVE = "key_save"
    KEY_DELETE = "key_delete"
    KEY_PROBE = "key_probe"
    SMTP_CODE_SAVE = "smtp_code_save"
    SMTP_CODE_DELETE = "smtp_code_delete"
    SMTP_VERIFY = "smtp_verify"
    REMINDER_CREATE = "reminder_create"
    REMINDER_UPDATE = "reminder_update"
    REMINDER_PAUSE = "reminder_pause"
    REMINDER_RESUME = "reminder_resume"
    REMINDER_CANCEL = "reminder_cancel"
    REMINDER_DELIVER = "reminder_deliver"
    REMINDER_MANUAL_SEND = "reminder_manual_send"
    REMINDER_CATCH_UP = "reminder_catch_up"
    WEB_SEARCH = "web_search"
    TEACHING_EVIDENCE_ADJUDICATION = "teaching_evidence_adjudication"
    # Issue 02：学习模式不变量告警（降级轮出现网络来源引用等）。
    LEARNING_INVARIANT = "learning_invariant"
    ARXIV_SEARCH = "arxiv_search"
    PLUGIN_INSTALL = "plugin_install"
    PLUGIN_UNINSTALL = "plugin_uninstall"
    PLUGIN_ENABLE = "plugin_enable"
    PLUGIN_DISABLE = "plugin_disable"
    PLUGIN_INVOKE = "plugin_invoke"
    MCP_INSTALL = "mcp_install"
    MCP_UNINSTALL = "mcp_uninstall"
    MCP_ENABLE = "mcp_enable"
    MCP_DISABLE = "mcp_disable"
    MCP_INVOKE = "mcp_invoke"
    MCP_INVOKE_DENIED = "mcp_invoke_denied"
    MCP_SENSITIVE_APPROVE = "mcp_sensitive_approve"
    MCP_SENSITIVE_DENY = "mcp_sensitive_deny"
    MCP_START_FAILED = "mcp_start_failed"
    MCP_PERMISSIONS_REVOKE = "mcp_permissions_revoke"
    # Issue 37: 数据生命周期——导出/删除/备份/恢复。details 白名单只含
    # 类别计数、版本与统计，绝不包含数据正文、凭据或会话令牌。
    EXPORT_CREATE = "export_create"
    ACCOUNT_DELETE = "account_delete"
    ACCOUNT_DELETE_FAILED = "account_delete_failed"
    BACKUP_CREATE = "backup_create"
    RESTORE_COMPLETE = "restore_complete"
    RESTORE_FAILED = "restore_failed"


class AuditResult(str, Enum):
    """Structured result of an audited action."""

    SUCCESS = "success"
    BLOCKED = "blocked"
    RETRYABLE_FAIL = "retryable_fail"
    DEGRADED = "degraded"
    DENIED = "denied"


class TelemetryCorrelation(BaseModel):
    """OpenTelemetry-style correlation identifiers carried by trace/metric/log.

    Identifiers are pseudonymized/hashed where possible so the observability
    backend cannot be used as a search-by-identity bypass. The trace_id links
    spans; run_id links the workflow execution; object domain and workflow
    versions provide grouping without exposing private content.
    """

    trace_id: str = Field(description="Trace identifier.")
    span_id: str | None = Field(default=None, description="Span identifier within the trace.")
    run_id: str | None = Field(default=None, description="Workflow run identifier when applicable.")
    account_hash: str | None = Field(
        default=None,
        description="Pseudonymized account identifier.",
    )
    project_hash: str | None = Field(
        default=None,
        description="Pseudonymized project identifier.",
    )
    tenant_hash: str | None = Field(
        default=None,
        description="Pseudonymized tenant identifier.",
    )
    object_domain: ObjectDomain | None = Field(
        default=None,
        description="Object domain for grouping.",
    )
    workflow_name: str | None = Field(default=None, description="Workflow template name.")
    workflow_version: str | None = Field(default=None, description="Workflow template version.")
    authorization_version: str | None = Field(default=None, description="Authorization snapshot.")
    key_epoch: str | None = Field(default=None, description="Key epoch for the operation.")


class PrivacyManifest(BaseModel):
    """Declaration of what telemetry data contains and what has been removed.

    The manifest is part of every RunSummary so operators can prove that private
    body, full prompts, keys and unnecessary model output were not copied.
    """

    includes_private_body: bool = Field(
        default=False,
        description="Whether the telemetry payload includes private object body.",
    )
    includes_full_prompt: bool = Field(
        default=False,
        description="Whether the telemetry payload includes the full model prompt.",
    )
    includes_secret: bool = Field(
        default=False,
        description="Whether the telemetry payload includes secrets or keys.",
    )
    includes_model_output: bool = Field(
        default=False,
        description="Whether the telemetry payload includes raw model output.",
    )
    scrubbed_fields: list[str] = Field(
        default_factory=list,
        description="Field names that were removed or hashed before emission.",
    )


class AuditEvent(BaseModel):
    """Append-only audit event.

    Audit events do not copy private body, full prompts, keys or model output.
    They record actor, action, object references, scope, result and correlation
    so security and compliance reviews can reconstruct what happened.
    """

    event_id: str = Field(description="Stable event identifier.")
    occurred_at: datetime = Field(description="When the event occurred.")
    actor_account_id: str = Field(description="Account that performed the action.")
    actor_session_id: str | None = Field(default=None, description="Session when available.")
    action: AuditAction = Field(description="Action being audited.")
    object_refs: list[str] = Field(
        default_factory=list,
        description="Object identifiers affected by the action.",
    )
    scope_envelope: ScopeEnvelope | None = Field(
        default=None,
        description="Scope envelope under which the action ran.",
    )
    result: AuditResult = Field(description="Structured result of the action.")
    reason: str | None = Field(default=None, description="Human-readable reason.")
    correlation: TelemetryCorrelation = Field(description="Correlation identifiers.")
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Opaque details safe for logging; must not contain private body.",
    )


class NodeSummary(BaseModel):
    """Privacy-preserving summary of a workflow node for observability."""

    node_id: str = Field(description="Stable node identifier.")
    node_name: str = Field(description="Human-readable node name.")
    status: str = Field(description="Node status.")
    capability_ref: str | None = Field(default=None, description="Logical capability reference.")
    started_at: datetime | None = Field(default=None)
    completed_at: datetime | None = Field(default=None)
    failure_reason: str | None = Field(
        default=None,
        description="Non-leaking failure reason if the node failed.",
    )
    output_ref: str | None = Field(
        default=None,
        description="Reference to the typed artifact, not the artifact body.",
    )


class ModelLockSummary(BaseModel):
    """Privacy-preserving summary of a model invocation lock."""

    lock_id: str = Field(description="Stable lock identifier.")
    capability_name: str = Field(description="Logical capability name.")
    capability_version: str = Field(description="Capability version.")
    actual_model_id: str | None = Field(default=None, description="Actual vendor model id used.")
    region: str = Field(description="Region where the invocation was routed.")
    status: str = Field(description="Final outcome.")
    retry_count: int = Field(default=0, description="Number of retries consumed.")
    error_code: str | None = Field(default=None, description="Stable error code if failed.")
    degradation_reason: str | None = Field(
        default=None,
        description="Non-leaking degradation reason.",
    )
    created_at: datetime = Field(description="When the lock was produced.")


class RunSummary(BaseModel):
    """Unified run summary used by trace/metric/log and audit reconstruction.

    The RunSummary binds run status, artifact trust status, terminal reason,
    node summaries and model lock summaries under a single correlation object.
    It never includes private body, full prompts, keys or unnecessary model
    output; only references and non-sensitive metadata.
    """

    summary_id: str = Field(description="Stable summary identifier.")
    run_id: str = Field(description="Workflow run identifier.")
    account_hash: str = Field(description="Pseudonymized account identifier.")
    project_hash: str = Field(description="Pseudonymized project identifier.")
    tenant_hash: str | None = Field(default=None, description="Pseudonymized tenant identifier.")
    object_domain: ObjectDomain = Field(description="Object domain of the run.")
    workflow_name: str = Field(description="Workflow template name.")
    workflow_version: str = Field(description="Workflow template version.")
    run_status: str = Field(description="Workflow execution state.")
    artifact_trust_status: str = Field(description="Scientific trust state.")
    terminal_reason: str | None = Field(
        default=None,
        description="Reason the run reached a terminal state.",
    )
    publish_eligible: bool = Field(description="Whether the run is publish eligible.")
    node_summaries: list[NodeSummary] = Field(default_factory=list)
    model_lock_summaries: list[ModelLockSummary] = Field(default_factory=list)
    audit_event_refs: list[str] = Field(
        default_factory=list,
        description="Event identifiers that contributed to this summary.",
    )
    correlation: TelemetryCorrelation = Field(description="Correlation identifiers.")
    privacy_manifest: PrivacyManifest = Field(
        default_factory=PrivacyManifest,
        description="Privacy declaration for this summary.",
    )
    created_at: datetime = Field(description="When the summary was produced.")


class SLIMetricKind(str, Enum):
    """Supported SLI metric kinds."""

    LATENCY = "latency"
    AVAILABILITY = "availability"
    CORRECTNESS = "correctness"
    DEGRADATION = "degradation"
    COST = "cost"


class SLISeverity(str, Enum):
    """Severity of an SLO breach or alert."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SLIRecord(BaseModel):
    """Service Level Indicator registered by a workload."""

    sli_id: str = Field(description="Stable SLI identifier.")
    workload_name: str = Field(
        description="Workload that owns this SLI, e.g. 'interactive' or 'ingestion'.",
    )
    metric_kind: SLIMetricKind = Field(description="Kind of metric.")
    description: str = Field(description="Human-readable description.")
    unit: str = Field(description="Unit of measurement, e.g. 'ms' or 'ratio'.")
    window: str = Field(description="Measurement window, e.g. '1m' or '5m'.")
    owner: str = Field(description="Team or individual responsible for this SLI.")
    runbook_url: str | None = Field(default=None, description="Link to the runbook.")
    created_at: datetime = Field(description="When the SLI was registered.")


class SLORecord(BaseModel):
    """Service Level Objective bound to an SLI."""

    slo_id: str = Field(description="Stable SLO identifier.")
    sli_id: str = Field(description="SLI this objective measures.")
    target: float = Field(description="Target value, e.g. 0.99 for availability.")
    alert_threshold: float = Field(
        description="Value at which an alert fires, e.g. 0.95 for availability.",
    )
    severity: SLISeverity = Field(description="Severity when the threshold is breached.")
    created_at: datetime = Field(description="When the SLO was registered.")


class AlertState(str, Enum):
    """Lifecycle state of an alert."""

    FIRING = "firing"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    SUPPRESSED = "suppressed"


class AlertRecord(BaseModel):
    """Basic alert with owner, runbook, deduplication and closure evidence.

    Alerts are deduplicated by a stable dedup_key so the same underlying breach
    does not spam on-callers. Resolution requires evidence, not just a status
    change.
    """

    alert_id: str = Field(description="Stable alert identifier.")
    dedup_key: str = Field(description="Stable deduplication key.")
    sli_id: str = Field(description="SLI that produced this alert.")
    severity: SLISeverity = Field(description="Alert severity.")
    owner: str = Field(description="Team or individual responsible.")
    summary: str = Field(description="Human-readable summary.")
    runbook_url: str | None = Field(default=None, description="Link to the runbook.")
    state: AlertState = Field(description="Current alert state.")
    evidence_refs: list[str] = Field(
        default_factory=list,
        description="References to run summaries or metrics supporting the alert.",
    )
    created_at: datetime = Field(description="When the alert was created.")
    acknowledged_at: datetime | None = Field(default=None)
    acknowledged_by: str | None = Field(default=None)
    resolved_at: datetime | None = Field(default=None)
    resolution_evidence: str | None = Field(
        default=None,
        description="Required evidence when the alert is resolved.",
    )
