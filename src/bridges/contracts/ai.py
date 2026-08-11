"""Qwen capability registry, model gateway and run-lock contracts.

These models define the public surface of T009: logical capabilities are
registered independently of vendor model names, and every invocation produces an
immutable model or tool run lock that can be replayed, audited and compared.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum, StrEnum
from typing import Any

from pydantic import BaseModel, Field


class CapabilityKind(str, Enum):
    """Whether a capability is backed by a model or by a deterministic tool."""

    MODEL = "model"
    TOOL = "tool"


class CapabilityStatus(str, Enum):
    """Lifecycle status of a registered logical capability."""

    VERIFIED = "verified"
    DEPRECATED = "deprecated"
    DISABLED = "disabled"


class StructuredOutputFormat(StrEnum):
    """Structured response format declared by a model capability."""

    JSON_OBJECT = "json_object"
    JSON_SCHEMA = "json_schema"


class ModelCallStatus(str, Enum):
    """Outcome of a single model-gateway invocation."""

    SUCCESS = "success"
    DEGRADED = "degraded"
    BLOCKED = "blocked"
    RETRYABLE_FAIL = "retryable_fail"


class RetryPolicy(BaseModel):
    """Bounded retry policy for a capability.

    Only rate-limit and transient failures may be retried; auth/region/prohibited
    degradation failures fail closed immediately.
    """

    max_attempts: int = Field(default=1, ge=1, description="Maximum call attempts including the first.")
    backoff_seconds: float = Field(default=1.0, ge=0, description="Base exponential backoff in seconds.")
    jitter: bool = Field(default=True, description="Apply random jitter to backoff.")


class FallbackPolicy(BaseModel):
    """Rules for falling back when the primary capability cannot be invoked.

    Fallbacks must be verified capabilities in the same region; cross-region or
    unverified replacement is prohibited.
    """

    fallback_capability_name: str | None = Field(
        default=None,
        description="Registered logical capability to use as fallback.",
    )
    fallback_capability_version: str | None = Field(
        default=None,
        description="Version of the fallback capability.",
    )
    allow_same_region_only: bool = Field(
        default=True,
        description="If true, fallback is only allowed when the fallback capability is in the same region.",
    )
    prohibited_when: list[str] = Field(
        default_factory=list,
        description="Conditions that forbid any fallback, e.g. safety_refusal.",
    )


class CapabilityRecord(BaseModel):
    """A registered logical capability.

    Callers depend on the capability name/version and input/output contract, not
    on the underlying vendor model alias.
    """

    name: str = Field(description="Stable logical capability name.")
    version: str = Field(description="Capability version.")
    kind: CapabilityKind = Field(description="Model or tool capability.")
    vendor: str = Field(description="Vendor providing the capability, e.g. qwen.")
    region: str = Field(description="Deployment region, e.g. cn-beijing.")
    endpoint_protocol: str = Field(
        default="openai-compatible",
        description="Protocol used to reach the capability.",
    )
    model_id: str | None = Field(
        default=None,
        description="Actual vendor model id or stable alias; null for pure tools.",
    )
    input_schema_version: str = Field(description="Version of the input contract.")
    output_schema_version: str = Field(description="Version of the output contract.")
    structured_output_format: StructuredOutputFormat | None = Field(
        default=None,
        description="Provider-supported structured response format, when applicable.",
    )
    max_input_tokens: int | None = Field(default=None, description="Maximum input tokens if known.")
    supported_modalities: list[str] = Field(
        default_factory=lambda: ["text"],
        description="Supported input modalities.",
    )
    status: CapabilityStatus = Field(default=CapabilityStatus.VERIFIED)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    fallback_policy: FallbackPolicy = Field(default_factory=FallbackPolicy)
    rpm_limit: int | None = Field(default=None, description="Per-project RPM limit snapshot.")
    tpm_limit: int | None = Field(default=None, description="Per-project TPM limit snapshot.")
    concurrency_budget: int | None = Field(default=None, description="Project-side concurrency budget.")
    cost_snapshot_source: str | None = Field(default=None)
    cost_snapshot_at: datetime | None = Field(default=None)
    prompt_version: str = Field(default="1", description="Prompt/template version used with this capability.")
    validation_probe_version: str | None = Field(default=None)
    data_classification: str | None = Field(
        default=None,
        description="Highest data classification this capability may process.",
    )


class ModelRunLock(BaseModel):
    """Immutable snapshot of one model invocation.

    The lock records the exact capability, model, region, parameters, prompt
    version and contract that were used, so the call can be replayed and audited.
    """

    lock_id: str = Field(description="Stable lock identifier.")
    run_id: str = Field(description="Workflow run that requested the invocation.")
    account_id: str = Field(description="Account that owns the run.")
    project_id: str = Field(description="Project within which the run is scoped.")
    capability_name: str = Field(description="Logical capability name.")
    capability_version: str = Field(description="Logical capability version.")
    actual_model_id: str | None = Field(description="Actual vendor model id or alias used.")
    region: str = Field(description="Region where the invocation was routed.")
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Non-secret invocation parameters such as temperature and max_tokens.",
    )
    prompt_version: str = Field(description="Prompt/template version used.")
    input_output_contract: str = Field(
        description="Identifier of the input/output contract that was honored.",
    )
    fallback_path: list[str] = Field(
        default_factory=list,
        description="Capability names that were attempted, including the primary.",
    )
    status: ModelCallStatus = Field(description="Final outcome of the invocation.")
    retry_count: int = Field(default=0, description="Number of retries consumed.")
    degradation_reason: str | None = Field(default=None)
    error_code: str | None = Field(
        default=None,
        description="Stable error code when the invocation did not succeed.",
    )
    error_message: str | None = Field(
        default=None,
        description="Human-readable, non-leaking error message.")
    created_at: datetime = Field(description="When the lock was produced.")
    usage: dict[str, Any] | None = Field(
        default=None,
        description="Token/cache usage metadata if returned by the adapter.",
    )
    cost_estimate: dict[str, Any] | None = Field(
        default=None,
        description="Cost estimate metadata; not a billing truth.",
    )


class ToolRunLock(BaseModel):
    """Immutable snapshot of one deterministic tool invocation.

    Tool capabilities follow the same registry and gateway path as model
    capabilities, but record tool-specific fields instead of a model id.
    """

    lock_id: str = Field(description="Stable lock identifier.")
    run_id: str = Field(description="Workflow run that requested the invocation.")
    account_id: str = Field(description="Account that owns the run.")
    project_id: str = Field(description="Project within which the run is scoped.")
    capability_name: str = Field(description="Logical capability name.")
    capability_version: str = Field(description="Logical capability version.")
    tool_implementation: str | None = Field(
        default=None,
        description="Concrete tool implementation identifier.",
    )
    region: str = Field(default="local", description="Region where the tool ran.")
    parameters: dict[str, Any] = Field(default_factory=dict)
    input_output_contract: str = Field(description="Identifier of the honored contract.")
    status: ModelCallStatus = Field(description="Final outcome of the invocation.")
    retry_count: int = Field(default=0)
    degradation_reason: str | None = Field(default=None)
    created_at: datetime = Field(description="When the lock was produced.")
    result_summary: dict[str, Any] | None = Field(
        default=None,
        description="Tool result summary; full results are stored as typed artifacts.",
    )


class ModelCallResult(BaseModel):
    """Outcome returned by the model gateway for one invocation attempt.

    The gateway resolves the capability, applies retry/fallback policy, and
    returns a result together with the immutable run lock.
    """

    status: ModelCallStatus = Field(description="Final outcome.")
    lock: ModelRunLock | None = Field(default=None, description="Run lock if a call was attempted.")
    output: dict[str, Any] | None = Field(
        default=None,
        description="Structured output when the call succeeded or degraded.",
    )
    degradation_reason: str | None = Field(default=None)
    error_code: str | None = Field(default=None)
    error_message: str | None = Field(default=None)


class CapabilityReference(BaseModel):
    """Pointer to a registered logical capability used by a workflow node."""

    name: str = Field(description="Logical capability name.")
    version: str = Field(default="1", description="Logical capability version.")
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Node-specific payload passed to the gateway.",
    )
