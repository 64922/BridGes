"""AI capability registry, adapters and model gateway."""

from bridges.ai.adapters import (
    AdapterError,
    AdapterResult,
    AuthError,
    CapabilityAdapter,
    RateLimitError,
    RegionError,
    StreamChunk,
    StreamEvent,
    StubQwenAdapter,
    TransientError,
)
from bridges.ai.capability_registry import CapabilityRegistry, CapabilityRegistryError
from bridges.ai.embedding_adapter import QwenEmbeddingAdapter
from bridges.ai.model_gateway import ModelGateway, ModelGatewayError
from bridges.ai.qwen_adapters import QwenStructuredOutputAdapter, QwenTextChatAdapter
from bridges.ai.qwen_asr_adapter import QwenAsrAdapter
from bridges.ai.qwen_client import CassetteStore, QwenApiClient
from bridges.ai.qwen_image_adapter import QwenImageAdapter
from bridges.ai.qwen_tts_adapter import QwenTtsAdapter
from bridges.ai.qwen_vision_adapters import QwenOcrAdapter, QwenVisionAdapter
from bridges.ai.qwen_wan_adapter import QwenWanAdapter

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
    "QwenEmbeddingAdapter",
    "QwenImageAdapter",
    "QwenOcrAdapter",
    "QwenStructuredOutputAdapter",
    "QwenTextChatAdapter",
    "QwenTtsAdapter",
    "QwenVisionAdapter",
    "QwenWanAdapter",
    "RateLimitError",
    "RegionError",
    "StreamChunk",
    "StreamEvent",
    "StubQwenAdapter",
    "TransientError",
]
