"""学术候选到持久节点 JSON 载荷的边界转换。"""

from __future__ import annotations

from typing import Any

from bridges.paper.source_types import PaperCandidate


def candidate_payload(candidate: PaperCandidate) -> dict[str, Any]:
    """候选的标准 JSON 载荷（产物持久化与恢复共用同一形状）。"""
    return {
        "candidate_id": candidate.candidate_id,
        "arxiv_id": candidate.arxiv_id,
        "source": candidate.source,
        "source_id": candidate.source_id,
        "doi": candidate.doi,
        "openalex_id": candidate.openalex_id,
        "cited_by_count": candidate.cited_by_count,
        "venue": candidate.venue,
        "is_open_access": candidate.is_open_access,
        "sources": list(candidate.sources),
        "source_ids": list(candidate.source_ids),
        "discovery_paths": list(candidate.discovery_paths),
        "referenced_works": list(candidate.referenced_works),
        "title": candidate.title,
        "authors": list(candidate.authors),
        "published_at": candidate.published_at.isoformat(),
        "published_year": candidate.published_year,
        "abs_url": candidate.abs_url,
        "pdf_url": candidate.pdf_url,
        "full_text_url": candidate.full_text_url,
        "abstract": candidate.abstract,
        "primary_category": candidate.primary_category,
    }
