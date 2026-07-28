"""Multimodal media ingestion, correction and generation module for T030/T032."""

from science_companion.media.generation import MediaGenerationService, MediaGenerationError
from science_companion.media.service import (
    MediaError,
    MediaIngestionService,
    build_media_impact_resolver,
)

__all__ = [
    "MediaError",
    "MediaGenerationError",
    "MediaGenerationService",
    "MediaIngestionService",
    "build_media_impact_resolver",
]
