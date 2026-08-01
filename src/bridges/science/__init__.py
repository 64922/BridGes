"""Scientific evidence source ingestion, versioning and claim module."""

from bridges.science.claims import ClaimEvidenceService
from bridges.science.search import ScienceSearchService
from bridges.science.service import ScienceError, ScienceSourceService

__all__ = [
    "ScienceError",
    "ScienceSourceService",
    "ScienceSearchService",
    "ClaimEvidenceService",
]
