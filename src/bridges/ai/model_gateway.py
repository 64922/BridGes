"""Model gateway: resolve logical capabilities and produce immutable run locks.

The gateway is the only place that maps a logical capability name to a concrete
vendor model. It enforces:

- registered, verified capabilities only;
- bounded retry for rate-limit and transient failures;
- same-region, verified fallbacks only;
- no cross-region fallback and no fallback to unverified/disabled models;
- immutable ``ModelRunLock`` records for every attempt path.
"""

from __future__ import annotations

import random
import secrets
import time
from datetime import UTC, datetime
from typing import Any

from bridges.ai.adapters import (
    AdapterError,
    AuthError,
    CapabilityAdapter,
    RateLimitError,
    RegionError,
    TransientError,
)
from bridges.ai.capability_registry import CapabilityRegistry, CapabilityRegistryError
from bridges.contracts.ai import (
    CapabilityRecord,
    CapabilityStatus,
    FallbackPolicy,
    ModelCallResult,
    ModelCallStatus,
    ModelRunLock,
)
from bridges.contracts.workflows import RunContextEnvelope


class ModelGatewayError(Exception):
    """Domain error for gateway-level failures."""


class ModelGateway:
    """Resolve capabilities, invoke adapters, and record run locks."""

    def __init__(self, registry: CapabilityRegistry) -> None:
        self._registry = registry
        self._adapters: dict[tuple[str, str], CapabilityAdapter] = {}

    def register_adapter(
        self,
        capability_name: str,
        capability_version: str,
        adapter: CapabilityAdapter,
    ) -> None:
        """Bind an adapter to a registered capability.

        Multiple capabilities may share the same adapter instance.
        """
        self._adapters[(capability_name, capability_version)] = adapter

    def is_adapter_registered(self, capability_name: str, capability_version: str) -> bool:
        """Return whether an adapter has already been bound to a capability."""
        return (capability_name, capability_version) in self._adapters

    def invoke(
        self,
        capability_name: str,
        capability_version: str,
        run_context: RunContextEnvelope,
        payload: dict[str, Any] | None = None,
    ) -> ModelCallResult:
        """Invoke a capability and return a result with an immutable run lock.

        The gateway never silently crosses regions or swaps to an unverified
        model. All attempted capabilities are recorded in the lock's fallback
        path.
        """
        payload = payload or {}
        try:
            primary = self._registry.get(capability_name, capability_version)
        except CapabilityRegistryError as exc:
            lock = ModelRunLock(
                lock_id=secrets.token_urlsafe(16),
                run_id=run_context.run_id,
                account_id=run_context.account_id,
                project_id=run_context.project_id,
                capability_name=capability_name,
                capability_version=capability_version,
                actual_model_id=None,
                region="unknown",
                parameters={},
                prompt_version="unknown",
                input_output_contract="unknown",
                fallback_path=[f"{capability_name}@{capability_version}"],
                status=ModelCallStatus.BLOCKED,
                retry_count=0,
                degradation_reason=str(exc),
                error_code="unregistered_capability",
                error_message=str(exc),
                created_at=datetime.now(UTC),
            )
            return ModelCallResult(
                status=ModelCallStatus.BLOCKED,
                lock=lock,
                error_code="unregistered_capability",
                error_message=str(exc),
                degradation_reason=str(exc),
            )

        if primary.status != CapabilityStatus.VERIFIED:
            return self._blocked_result(
                run_context,
                primary,
                "capability_not_verified",
                f"能力未通过验证：{capability_name}@{capability_version}。",
            )

        adapter = self._adapters.get((capability_name, capability_version))
        if adapter is None:
            return self._blocked_result(
                run_context,
                primary,
                "no_adapter",
                f"能力没有绑定适配器：{capability_name}@{capability_version}。",
            )

        attempted: list[str] = []
        primary_result, primary_lock = self._invoke_capability(
            primary,
            adapter,
            run_context,
            payload,
            attempted,
        )
        if primary_result.status in {ModelCallStatus.SUCCESS, ModelCallStatus.DEGRADED}:
            return primary_result

        # Only retryable failures (rate-limit / transient) may fall back.
        # Region, auth and safety failures fail closed immediately.
        if primary_result.status != ModelCallStatus.RETRYABLE_FAIL:
            return primary_result

        # Primary exhausted its retry budget; attempt a verified same-region fallback.
        # Check prohibited conditions first — if any match the primary's error,
        # no fallback is allowed regardless of availability.
        if primary.fallback_policy.prohibited_when:
            for condition in primary.fallback_policy.prohibited_when:
                if condition in (primary_result.error_code or "") or condition in (
                    primary_result.degradation_reason or ""
                ):
                    return self._blocked_result(
                        run_context,
                        primary,
                        "fallback_prohibited",
                        f"禁止降级条件触发：{condition}，不允许使用备选能力。",
                        attempted=attempted,
                    )

        fallback = self._resolve_fallback(primary.fallback_policy, attempted)
        if fallback is None:
            return primary_result

        fallback_adapter = self._adapters.get((fallback.name, fallback.version))
        if fallback_adapter is None:
            return self._blocked_result(
                run_context,
                primary,
                "fallback_no_adapter",
                f"备选能力没有绑定适配器：{fallback.name}@{fallback.version}。",
                attempted=attempted,
            )

        fallback_result, _ = self._invoke_capability(
            fallback,
            fallback_adapter,
            run_context,
            payload,
            attempted,
        )
        return fallback_result

    def _resolve_fallback(
        self,
        policy: FallbackPolicy,
        attempted: list[str],
    ) -> CapabilityRecord | None:
        if not policy.fallback_capability_name or not policy.fallback_capability_version:
            return None
        try:
            candidate = self._registry.get(
                policy.fallback_capability_name,
                policy.fallback_capability_version,
            )
        except CapabilityRegistryError:
            return None
        if candidate.status != CapabilityStatus.VERIFIED:
            return None
        if policy.allow_same_region_only:
            primary_name = attempted[0] if attempted else None
            if primary_name:
                try:
                    primary = self._registry.get(*primary_name.rsplit("@", 1))
                except CapabilityRegistryError:
                    return None
                if candidate.region != primary.region:
                    return None
        return candidate

    def _invoke_capability(
        self,
        capability: CapabilityRecord,
        adapter: CapabilityAdapter,
        run_context: RunContextEnvelope,
        payload: dict[str, Any],
        attempted: list[str],
    ) -> tuple[ModelCallResult, ModelRunLock]:
        attempted.append(f"{capability.name}@{capability.version}")
        retry_policy = capability.retry_policy
        retry_count = 0

        for attempt in range(1, retry_policy.max_attempts + 1):
            try:
                adapter_result = adapter.call(capability, run_context, payload)
            except (RateLimitError, TransientError) as exc:
                retry_count = attempt - 1
                if attempt < retry_policy.max_attempts:
                    if retry_policy.backoff_seconds > 0:
                        backoff = retry_policy.backoff_seconds * (2 ** (attempt - 1))
                        if retry_policy.jitter:
                            backoff *= random.uniform(0.5, 1.5)
                        time.sleep(backoff)
                    continue
                # Exhausted retries on this capability.
                lock = self._build_lock(
                    run_context,
                    capability,
                    ModelCallStatus.RETRYABLE_FAIL,
                    attempted,
                    retry_count,
                    degradation_reason=str(exc),
                    error_code=exc.code,
                    error_message=exc.message,
                    payload=payload,
                )
                return (
                    ModelCallResult(
                        status=ModelCallStatus.RETRYABLE_FAIL,
                        lock=lock,
                        error_code=exc.code,
                        error_message=exc.message,
                        degradation_reason=str(exc),
                    ),
                    lock,
                )
            except (RegionError, AuthError) as exc:
                lock = self._build_lock(
                    run_context,
                    capability,
                    ModelCallStatus.BLOCKED,
                    attempted,
                    retry_count=attempt - 1,
                    degradation_reason=str(exc),
                    error_code=exc.code,
                    error_message=exc.message,
                    payload=payload,
                )
                return (
                    ModelCallResult(
                        status=ModelCallStatus.BLOCKED,
                        lock=lock,
                        error_code=exc.code,
                        error_message=exc.message,
                        degradation_reason=str(exc),
                    ),
                    lock,
                )
            except AdapterError as exc:
                lock = self._build_lock(
                    run_context,
                    capability,
                    ModelCallStatus.BLOCKED,
                    attempted,
                    retry_count=attempt - 1,
                    degradation_reason=str(exc),
                    error_code=exc.code,
                    error_message=exc.message,
                    payload=payload,
                )
                return (
                    ModelCallResult(
                        status=ModelCallStatus.BLOCKED,
                        lock=lock,
                        error_code=exc.code,
                        error_message=exc.message,
                        degradation_reason=str(exc),
                    ),
                    lock,
                )

            lock = self._build_lock(
                run_context,
                capability,
                ModelCallStatus.SUCCESS,
                attempted,
                retry_count=attempt - 1,
                actual_model_id=adapter_result.actual_model_id,
                usage=adapter_result.usage,
                payload=payload,
            )
            return (
                ModelCallResult(
                    status=ModelCallStatus.SUCCESS,
                    lock=lock,
                    output=adapter_result.output,
                ),
                lock,
            )

        # Unreachable, but keeps mypy happy when the loop has no normal exit.
        lock = self._build_lock(
            run_context,
            capability,
            ModelCallStatus.BLOCKED,
            attempted,
            retry_count,
            degradation_reason="unexpected empty invocation",
            error_code="unexpected",
            error_message="Unexpected empty invocation path.",
            payload=payload,
        )
        return (
            ModelCallResult(
                status=ModelCallStatus.BLOCKED,
                lock=lock,
                error_code="unexpected",
                error_message="Unexpected empty invocation path.",
            ),
            lock,
        )

    def _build_lock(
        self,
        run_context: RunContextEnvelope,
        capability: CapabilityRecord,
        status: ModelCallStatus,
        fallback_path: list[str],
        retry_count: int,
        degradation_reason: str | None = None,
        actual_model_id: str | None = None,
        usage: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> ModelRunLock:
        return ModelRunLock(
            lock_id=secrets.token_urlsafe(16),
            run_id=run_context.run_id,
            account_id=run_context.account_id,
            project_id=run_context.project_id,
            capability_name=capability.name,
            capability_version=capability.version,
            actual_model_id=actual_model_id or capability.model_id,
            region=capability.region,
            parameters=self._capture_parameters(payload),
            prompt_version=capability.prompt_version,
            input_output_contract=f"{capability.name}:{capability.input_schema_version}->{capability.output_schema_version}",
            fallback_path=list(fallback_path),
            status=status,
            retry_count=retry_count,
            degradation_reason=degradation_reason,
            error_code=error_code,
            error_message=error_message,
            created_at=datetime.now(UTC),
            usage=usage,
        )

    @staticmethod
    def _capture_parameters(payload: dict[str, Any] | None) -> dict[str, Any]:
        """Extract non-secret invocation parameters from payload.

        Only well-known parameter keys are captured; unknown keys are
        silently ignored to avoid leaking sensitive payload fields into
        the immutable run lock.
        """
        params: dict[str, Any] = {}
        for key in ("temperature", "max_tokens", "top_p"):
            if payload and key in payload:
                params[key] = payload[key]
        # TTS-specific parameters (T062). These are captured when present so
        # the run lock records the voice, language and format used.
        for key in ("voice", "language_type", "format", "sample_rate"):
            if payload and key in payload:
                params[key] = payload[key]
        if not params:
            params = {"temperature": 0.7, "max_tokens": 1024}
        return params

    def _blocked_result(
        self,
        run_context: RunContextEnvelope,
        capability: CapabilityRecord,
        error_code: str,
        error_message: str,
        attempted: list[str] | None = None,
    ) -> ModelCallResult:
        lock = self._build_lock(
            run_context,
            capability,
            ModelCallStatus.BLOCKED,
            attempted or [f"{capability.name}@{capability.version}"],
            retry_count=0,
            degradation_reason=error_message,
            error_code=error_code,
            error_message=error_message,
        )
        return ModelCallResult(
            status=ModelCallStatus.BLOCKED,
            lock=lock,
            error_code=error_code,
            error_message=error_message,
            degradation_reason=error_message,
        )
