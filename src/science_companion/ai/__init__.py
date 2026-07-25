"""AI capability registry, adapters and model gateway."""

from science_companion.ai.adapters import (
    AdapterError,
    AdapterResult,
    AuthError,
    CapabilityAdapter,
    RateLimitError,
    RegionError,
    StubQwenAdapter,
    TransientError,
)
from science_companion.ai.capability_registry import CapabilityRegistry, CapabilityRegistryError
from science_companion.ai.model_gateway import ModelGateway, ModelGatewayError

__all__ = [
    "AdapterError",
    "AdapterResult",
    "AuthError",
    "CapabilityAdapter",
    "CapabilityRegistry",
    "CapabilityRegistryError",
    "ModelGateway",
    "ModelGatewayError",
    "RateLimitError",
    "RegionError",
    "StubQwenAdapter",
    "TransientError",
]
