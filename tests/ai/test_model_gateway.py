"""Tests for the model gateway.

The seam: the gateway resolves logical capabilities, applies bounded retry and
same-region fallback, and returns an immutable run lock. Cross-region or
unverified fallback is prohibited.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from bridges.ai import CapabilityRegistry, ModelGateway
from bridges.ai.adapters import (
    AdapterError,
    AdapterResult,
    AuthError,
    CapabilityAdapter,
    RateLimitError,
    RegionError,
    TransientError,
)
from bridges.contracts.ai import (
    CapabilityKind,
    CapabilityRecord,
    CapabilityStatus,
    FallbackPolicy,
    ModelCallStatus,
    RetryPolicy,
)
from bridges.contracts.workflows import RunContextEnvelope


class _ProgrammableAdapter:
    """Test adapter that returns programmed results or raises errors."""

    def __init__(self, responses: list[AdapterResult | AdapterError]) -> None:
        self.responses = list(responses)
        self.call_count = 0

    def call(
        self,
        capability: CapabilityRecord,
        run_context: RunContextEnvelope,
        payload: dict[str, Any],
    ) -> AdapterResult:
        self.call_count += 1
        if not self.responses:
            raise TransientError("exhausted")
        item = self.responses.pop(0)
        if isinstance(item, AdapterError):
            raise item
        return item


def _context(run_id: str = "run-1") -> RunContextEnvelope:
    return RunContextEnvelope(
        run_id=run_id,
        account_id="account-1",
        project_id="project-1",
        workflow_name="wf",
        workflow_version="1",
        submitted_at=datetime.now(timezone.utc),
    )


def _cap(
    name: str,
    version: str = "1",
    *,
    region: str = "cn-beijing",
    status: CapabilityStatus = CapabilityStatus.VERIFIED,
    fallback: FallbackPolicy | None = None,
    retry: RetryPolicy | None = None,
) -> CapabilityRecord:
    return CapabilityRecord(
        name=name,
        version=version,
        kind=CapabilityKind.MODEL,
        vendor="qwen",
        region=region,
        model_id=f"{name}-model",
        input_schema_version="in-v1",
        output_schema_version="out-v1",
        status=status,
        fallback_policy=fallback or FallbackPolicy(),
        retry_policy=retry or RetryPolicy(max_attempts=1),
    )


def _success_result(model_id: str = "model") -> AdapterResult:
    return AdapterResult(actual_model_id=model_id, output={"ok": True})


def test_success_records_model_run_lock() -> None:
    registry = CapabilityRegistry()
    cap = _cap("primary")
    registry.register(cap)
    gateway = ModelGateway(registry)
    adapter = _ProgrammableAdapter([_success_result("primary-model")])
    gateway.register_adapter("primary", "1", adapter)

    result = gateway.invoke("primary", "1", _context())

    assert result.status == ModelCallStatus.SUCCESS
    assert result.lock is not None
    assert result.lock.capability_name == "primary"
    assert result.lock.actual_model_id == "primary-model"
    assert result.lock.region == "cn-beijing"
    assert result.lock.retry_count == 0
    assert result.lock.run_id == "run-1"


def test_unregistered_capability_is_blocked() -> None:
    gateway = ModelGateway(CapabilityRegistry())
    result = gateway.invoke("missing", "1", _context())

    assert result.status == ModelCallStatus.BLOCKED
    assert result.error_code == "unregistered_capability"
    assert "未注册能力" in (result.error_message or "")


def test_capability_without_adapter_is_blocked() -> None:
    registry = CapabilityRegistry()
    registry.register(_cap("primary"))
    gateway = ModelGateway(registry)

    result = gateway.invoke("primary", "1", _context())

    assert result.status == ModelCallStatus.BLOCKED
    assert result.error_code == "no_adapter"


def test_disabled_capability_is_blocked() -> None:
    registry = CapabilityRegistry()
    cap = _cap("primary")
    cap.status = CapabilityStatus.DISABLED
    registry._capabilities[("primary", "1")] = cap
    gateway = ModelGateway(registry)
    gateway.register_adapter("primary", "1", _ProgrammableAdapter([_success_result()]))

    result = gateway.invoke("primary", "1", _context())

    assert result.status == ModelCallStatus.BLOCKED
    assert result.error_code == "capability_not_verified"


def test_rate_limit_retries_then_falls_back_to_same_region() -> None:
    registry = CapabilityRegistry()
    primary = _cap(
        "primary",
        retry=RetryPolicy(max_attempts=3, backoff_seconds=0),
        fallback=FallbackPolicy(fallback_capability_name="fallback", fallback_capability_version="1"),
    )
    fallback = _cap("fallback")
    registry.register(primary)
    registry.register(fallback)

    gateway = ModelGateway(registry)
    primary_adapter = _ProgrammableAdapter(
        [RateLimitError(), RateLimitError(), RateLimitError()]
    )
    fallback_adapter = _ProgrammableAdapter([_success_result("fallback-model")])
    gateway.register_adapter("primary", "1", primary_adapter)
    gateway.register_adapter("fallback", "1", fallback_adapter)

    result = gateway.invoke("primary", "1", _context())

    assert result.status == ModelCallStatus.SUCCESS
    assert result.lock is not None
    assert result.lock.capability_name == "fallback"
    assert result.lock.actual_model_id == "fallback-model"
    assert result.lock.retry_count == 0
    assert result.lock.fallback_path == ["primary@1", "fallback@1"]
    assert primary_adapter.call_count == 3
    assert fallback_adapter.call_count == 1


def test_rate_limit_without_fallback_becomes_blocked() -> None:
    registry = CapabilityRegistry()
    primary = _cap("primary", retry=RetryPolicy(max_attempts=2, backoff_seconds=0))
    registry.register(primary)
    gateway = ModelGateway(registry)
    adapter = _ProgrammableAdapter([RateLimitError(), RateLimitError()])
    gateway.register_adapter("primary", "1", adapter)

    result = gateway.invoke("primary", "1", _context())

    assert result.status == ModelCallStatus.RETRYABLE_FAIL
    assert result.lock is not None
    assert result.lock.status == ModelCallStatus.RETRYABLE_FAIL
    assert result.lock.retry_count == 1
    assert adapter.call_count == 2


def test_transient_error_retries_within_budget() -> None:
    registry = CapabilityRegistry()
    primary = _cap("primary", retry=RetryPolicy(max_attempts=3, backoff_seconds=0))
    registry.register(primary)
    gateway = ModelGateway(registry)
    adapter = _ProgrammableAdapter([TransientError(), TransientError(), _success_result("model")])
    gateway.register_adapter("primary", "1", adapter)

    result = gateway.invoke("primary", "1", _context())

    assert result.status == ModelCallStatus.SUCCESS
    assert result.lock is not None
    assert result.lock.retry_count == 2
    assert adapter.call_count == 3


def test_region_error_blocks_immediately_without_fallback() -> None:
    registry = CapabilityRegistry()
    primary = _cap(
        "primary",
        fallback=FallbackPolicy(fallback_capability_name="fallback", fallback_capability_version="1"),
    )
    fallback = _cap("fallback")
    registry.register(primary)
    registry.register(fallback)
    gateway = ModelGateway(registry)
    primary_adapter = _ProgrammableAdapter([RegionError("region unavailable")])
    fallback_adapter = _ProgrammableAdapter([_success_result()])
    gateway.register_adapter("primary", "1", primary_adapter)
    gateway.register_adapter("fallback", "1", fallback_adapter)

    result = gateway.invoke("primary", "1", _context())

    assert result.status == ModelCallStatus.BLOCKED
    assert result.error_code == "region_error"
    assert primary_adapter.call_count == 1
    assert fallback_adapter.call_count == 0


def test_auth_error_blocks_immediately() -> None:
    registry = CapabilityRegistry()
    primary = _cap("primary")
    registry.register(primary)
    gateway = ModelGateway(registry)
    adapter = _ProgrammableAdapter([AuthError("invalid key")])
    gateway.register_adapter("primary", "1", adapter)

    result = gateway.invoke("primary", "1", _context())

    assert result.status == ModelCallStatus.BLOCKED
    assert result.error_code == "auth_error"


def test_cross_region_fallback_is_prohibited() -> None:
    registry = CapabilityRegistry()
    primary = _cap(
        "primary",
        region="cn-beijing",
        fallback=FallbackPolicy(fallback_capability_name="fallback", fallback_capability_version="1"),
    )
    fallback = _cap("fallback", region="ap-southeast-1")
    registry.register(primary)
    registry.register(fallback)
    gateway = ModelGateway(registry)
    primary_adapter = _ProgrammableAdapter([RateLimitError(), RateLimitError()])
    fallback_adapter = _ProgrammableAdapter([_success_result()])
    gateway.register_adapter("primary", "1", primary_adapter)
    gateway.register_adapter("fallback", "1", fallback_adapter)

    result = gateway.invoke("primary", "1", _context())

    assert result.status == ModelCallStatus.RETRYABLE_FAIL
    assert fallback_adapter.call_count == 0


def test_unverified_fallback_is_prohibited() -> None:
    registry = CapabilityRegistry()
    primary = _cap(
        "primary",
        retry=RetryPolicy(max_attempts=1),
        fallback=FallbackPolicy(fallback_capability_name="fallback", fallback_capability_version="1"),
    )
    fallback = _cap("fallback", status=CapabilityStatus.DEPRECATED)
    registry.register(primary)
    registry.register(fallback)
    gateway = ModelGateway(registry)
    gateway.register_adapter("primary", "1", _ProgrammableAdapter([RateLimitError()]))
    gateway.register_adapter("fallback", "1", _ProgrammableAdapter([_success_result()]))

    result = gateway.invoke("primary", "1", _context())

    assert result.status == ModelCallStatus.RETRYABLE_FAIL
