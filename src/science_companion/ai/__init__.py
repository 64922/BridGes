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
from science_companion.ai.qwen_adapters import QwenStructuredOutputAdapter, QwenTextChatAdapter
from science_companion.ai.qwen_client import CassetteStore, QwenApiClient

__all__ = [
    "AdapterError",
    "AdapterResult",
    "AuthError",
    "CapabilityAdapter",
    "CapabilityRegistry",
    "CapabilityRegistryError",
    "CassetteStore",
    "ModelGateway",
    "ModelGatewayError",
    "QwenApiClient",
    "QwenStructuredOutputAdapter",
    "QwenTextChatAdapter",
    "RateLimitError",
    "RegionError",
    "StubQwenAdapter",
    "TransientError",
]
