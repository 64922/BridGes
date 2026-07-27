"""Multimodal media ingestion and correction module for T030."""

from science_companion.media.service import (
    MediaError,
    MediaIngestionService,
    build_media_impact_resolver,
)

__all__ = [
    "MediaError",
    "MediaIngestionService",
    "build_media_impact_resolver",
]
