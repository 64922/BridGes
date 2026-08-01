"""Multimodal media ingestion, correction, generation, storyboard and publish module."""

from bridges.media.accessibility_service import (
    CORE_MEDIA_TASKS,
    AccessibilityError,
    AccessibilityService,
    AudioStoragePort,
    DeterministicNarrationSynthesizer,
    InMemoryAudioStorage,
    NarrationSynthesisContext,
    NarrationSynthesizer,
    QwenTtsNarrationSynthesizer,
    StoredAudio,
)
from bridges.media.generation import MediaGenerationError, MediaGenerationService
from bridges.media.publish_service import (
    MediaPublishError,
    MediaPublishService,
    build_media_publish_impact_resolver,
)
from bridges.media.service import (
    MediaError,
    MediaIngestionService,
    build_media_impact_resolver,
)
from bridges.media.storyboard_service import (
    SandboxError,
    SandboxService,
    StoryboardError,
    StoryboardService,
)

__all__ = [
    "CORE_MEDIA_TASKS",
    "AccessibilityError",
    "AccessibilityService",
    "AudioStoragePort",
    "DeterministicNarrationSynthesizer",
    "InMemoryAudioStorage",
    "MediaError",
    "MediaGenerationError",
    "MediaGenerationService",
    "MediaIngestionService",
    "MediaPublishError",
    "MediaPublishService",
    "NarrationSynthesisContext",
    "NarrationSynthesizer",
    "QwenTtsNarrationSynthesizer",
    "SandboxError",
    "SandboxService",
    "StoredAudio",
    "StoryboardError",
    "StoryboardService",
    "build_media_impact_resolver",
    "build_media_publish_impact_resolver",
]
