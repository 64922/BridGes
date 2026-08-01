"""Capability adapters translate logical capability invocations into vendor calls.

Adapters are intentionally stateless and scoped to a single capability call. The
model gateway decides whether and how to retry; adapters only report success or
a classified failure.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from bridges.contracts.ai import CapabilityRecord
from bridges.contracts.workflows import RunContextEnvelope


class AdapterError(Exception):
    """Base class for adapter failures.

    Attributes:
        code: Stable error code for the gateway to classify the failure.
        message: Human-readable, non-leaking explanation.
        retryable: Whether the gateway may retry this failure.
    """

    def __init__(self, *, code: str, message: str, retryable: bool = False) -> None:
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(message)


class RateLimitError(AdapterError):
    """Vendor returned a rate-limit (429) response."""

    def __init__(self, message: str = "Rate limited.") -> None:
        super().__init__(code="rate_limit", message=message, retryable=True)


class TransientError(AdapterError):
    """Transient vendor or network failure (5xx, timeout)."""

    def __init__(self, message: str = "Transient failure.") -> None:
        super().__init__(code="transient", message=message, retryable=True)


class RegionError(AdapterError):
    """Region misconfiguration or regional unavailability."""

    def __init__(self, message: str = "Region error.") -> None:
        super().__init__(code="region_error", message=message, retryable=False)


class AuthError(AdapterError):
    """Authentication or authorization failure (401/403)."""

    def __init__(self, message: str = "Authentication failed.") -> None:
        super().__init__(code="auth_error", message=message, retryable=False)


class AdapterResult:
    """Normalized result of a single adapter call.

    This is a plain object rather than a Pydantic model because it is an
    internal adapter/gateway boundary, not a public contract.
    """

    def __init__(
        self,
        *,
        actual_model_id: str | None,
        output: dict[str, Any],
        usage: dict[str, Any] | None = None,
    ) -> None:
        self.actual_model_id = actual_model_id
        self.output = output
        self.usage = usage


@runtime_checkable
class CapabilityAdapter(Protocol):
    """Protocol for capability adapters.

    Implementations receive the resolved capability and the immutable run
    context, then either return an ``AdapterResult`` or raise an
    ``AdapterError`` subclass.
    """

    def call(
        self,
        capability: CapabilityRecord,
        run_context: RunContextEnvelope,
        payload: dict[str, Any],
    ) -> AdapterResult:
        ...


class StubQwenAdapter:
    """Deterministic adapter for tests and local stubs.

    Returns a fixed structured output without network access. The actual model id
    is taken from the capability record so the resulting lock remains honest.
    """

    def call(
        self,
        capability: CapabilityRecord,
        run_context: RunContextEnvelope,
        payload: dict[str, Any],
    ) -> AdapterResult:
        return AdapterResult(
            actual_model_id=capability.model_id,
            output={
                "capability": capability.name,
                "version": capability.version,
                "run_id": run_context.run_id,
                "payload_keys": sorted(payload.keys()),
                "stub": True,
            },
            usage={"prompt_tokens": 0, "completion_tokens": 0},
        )
