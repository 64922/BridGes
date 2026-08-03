"""AI capability registry, adapters and model gateway."""

from bridges.ai.adapters import (
    AdapterError,
    AdapterResult,
    AuthError,
    CapabilityAdapter,
    RateLimitError,
    RegionError,
    StubQwenAdapter,
    TransientError,
)
from bridges.ai.capability_registry import CapabilityRegistry, CapabilityRegistryError
from bridges.ai.model_gateway import ModelGateway, ModelGatewayError
from bridges.ai.qwen_adapters import QwenStructuredOutputAdapter, QwenTextChatAdapter
from bridges.ai.qwen_asr_adapter import QwenAsrAdapter
from bridges.ai.qwen_client import CassetteStore, QwenApiClient
from bridges.ai.qwen_tts_adapter import QwenTtsAdapter
from bridges.ai.qwen_vision_adapters import QwenOcrAdapter, QwenVisionAdapter
from bridges.ai.streaming import StreamChunk, StreamEvent

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
    "QwenAsrAdapter",
    "QwenOcrAdapter",
    "QwenStructuredOutputAdapter",
    "QwenTextChatAdapter",
    "QwenTtsAdapter",
    "QwenVisionAdapter",
    "RateLimitError",
    "RegionError",
    "StreamChunk",
    "StreamEvent",
    "StubQwenAdapter",
    "TransientError",
]
