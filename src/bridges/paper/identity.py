"""跨来源论文身份匹配与候选融合。"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import replace

from bridges.paper.sources import PaperCandidate


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().lower()
    normalized = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", normalized)
    normalized = normalized.removeprefix("doi:").strip()
    return normalized or None


def paper_candidate_id(candidate: PaperCandidate) -> str:
    doi = normalize_doi(candidate.doi)
    if doi:
        return f"doi:{doi}"
    if candidate.arxiv_id:
        return f"arxiv:{_arxiv_key(candidate.arxiv_id)}"
    if candidate.openalex_id:
        return f"openalex:{_openalex_key(candidate.openalex_id)}"
    if candidate.source_id:
        return f"{candidate.source}:{candidate.source_id.strip()}"
    year = candidate.published_year or "unknown"
    return f"{candidate.source}:{candidate.title.strip()}:{year}"


def merge_candidates(candidates: Iterable[PaperCandidate]) -> list[PaperCandidate]:
    """只按 DOI、arXiv 或 OpenAlex 原生 ID 合并，绝不按相似题名猜身份。"""
    groups: list[tuple[set[str], PaperCandidate]] = []
    for candidate in candidates:
        keys = _identity_keys(candidate)
        matches = [index for index, (known, _) in enumerate(groups) if keys & known]
        if not matches:
            groups.append((keys, _with_provenance(candidate)))
            continue
        first_index = matches[0]
        known, current = groups[first_index]
        merged = _merge_pair(current, candidate)
        known.update(keys)
        for extra_index in reversed(matches[1:]):
            extra_keys, extra_candidate = groups.pop(extra_index)
            known.update(extra_keys)
            merged = _merge_pair(merged, extra_candidate)
        groups[first_index] = (known, merged)
    return [candidate for _, candidate in groups]


def _with_provenance(candidate: PaperCandidate) -> PaperCandidate:
    sources = tuple(dict.fromkeys((*candidate.sources, candidate.source)))
    ids = tuple(dict.fromkeys((*candidate.source_ids, *_source_ids(candidate))))
    path = f"{candidate.source}:{candidate.source_id or candidate.candidate_id}"
    return replace(
        candidate,
        sources=sources,
        source_ids=ids,
        discovery_paths=tuple(dict.fromkeys((*candidate.discovery_paths, path))),
    )


def _merge_pair(first: PaperCandidate, second: PaperCandidate) -> PaperCandidate:
    first = _with_provenance(first)
    second = _with_provenance(second)
    return replace(
        first,
        arxiv_id=first.arxiv_id or second.arxiv_id,
        doi=first.doi or second.doi,
        openalex_id=first.openalex_id or second.openalex_id,
        title=first.title or second.title,
        authors=list(dict.fromkeys((*first.authors, *second.authors))),
        abstract=first.abstract or second.abstract,
        published_at=(
            first.published_at if first.published_year is not None else second.published_at
        ),
        pdf_url=first.pdf_url or second.pdf_url,
        abs_url=first.abs_url or second.abs_url,
        primary_category=first.primary_category or second.primary_category,
        cited_by_count=(
            first.cited_by_count if first.cited_by_count is not None else second.cited_by_count
        ),
        venue=first.venue or second.venue,
        full_text_url=first.full_text_url or second.full_text_url,
        is_open_access=(
            first.is_open_access if first.is_open_access is not None else second.is_open_access
        ),
        sources=tuple(dict.fromkeys((*first.sources, *second.sources))),
        source_ids=tuple(dict.fromkeys((*first.source_ids, *second.source_ids))),
        discovery_paths=tuple(dict.fromkeys((*first.discovery_paths, *second.discovery_paths))),
        referenced_works=tuple(dict.fromkeys((*first.referenced_works, *second.referenced_works))),
    )


def _identity_keys(candidate: PaperCandidate) -> set[str]:
    keys: set[str] = set()
    doi = normalize_doi(candidate.doi)
    if doi:
        keys.add(f"doi:{doi}")
    if candidate.arxiv_id:
        keys.add(f"arxiv:{_arxiv_key(candidate.arxiv_id)}")
    if candidate.openalex_id:
        keys.add(f"openalex:{_openalex_key(candidate.openalex_id)}")
    if candidate.source_id:
        keys.add(f"source:{candidate.source}:{candidate.source_id.strip().casefold()}")
    return keys


def _source_ids(candidate: PaperCandidate) -> tuple[str, ...]:
    values: list[str] = []
    if candidate.arxiv_id:
        values.append(f"arxiv:{candidate.arxiv_id}")
    doi = normalize_doi(candidate.doi)
    if doi:
        values.append(f"doi:{doi}")
    if candidate.openalex_id:
        values.append(f"openalex:{_openalex_key(candidate.openalex_id)}")
    if candidate.source_id:
        values.append(f"{candidate.source}:{candidate.source_id}")
    return tuple(values)


def _openalex_key(value: str) -> str:
    return value.rstrip("/").rsplit("/", 1)[-1].lower()


def _arxiv_key(value: str) -> str:
    return re.sub(r"v\d+$", "", value.strip().lower())
