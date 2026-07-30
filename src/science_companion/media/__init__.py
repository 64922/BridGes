"""Multimodal media ingestion, correction, generation, storyboard and publish module."""

from science_companion.media.accessibility_service import (
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
from science_companion.media.generation import MediaGenerationError, MediaGenerationService
from science_companion.media.publish_service import (
    MediaPublishError,
    MediaPublishService,
    build_media_publish_impact_resolver,
)
from science_companion.media.service import (
    MediaError,
    MediaIngestionService,
    build_media_impact_resolver,
)
from science_companion.media.storyboard_service import (
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
