"""论文节点产物与候选编解码。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from hashlib import sha256
from typing import Any

from bridges.contracts.modules import ModuleQueryRecord
from bridges.paper.contracts import PaperReadScope, PaperRecommendation, PaperTermAnalysis
from bridges.paper.reading import PaperReading
from bridges.paper.source_types import (
    CROSSREF_SOURCE,
    OPENALEX_SOURCE,
    EnrichedMetadata,
    PaperCandidate,
)


def _digest(value: Any) -> str:
    material = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(material.encode("utf-8")).hexdigest()


def _candidate(payload: Mapping[str, Any]) -> PaperCandidate:
    return PaperCandidate(
        arxiv_id=str(payload["arxiv_id"]) if payload.get("arxiv_id") else None,
        title=str(payload["title"]),
        authors=[str(item) for item in payload.get("authors") or ()],
        published_at=datetime.fromisoformat(str(payload["published_at"])),
        abs_url=str(payload["abs_url"]),
        pdf_url=str(payload.get("pdf_url") or ""),
        abstract=str(payload.get("abstract") or ""),
        primary_category=payload.get("primary_category"),
        source=str(payload.get("source") or "arxiv"),
        source_id=str(payload["source_id"]) if payload.get("source_id") else None,
        doi=str(payload["doi"]) if payload.get("doi") else None,
        openalex_id=str(payload["openalex_id"]) if payload.get("openalex_id") else None,
        cited_by_count=(
            int(payload["cited_by_count"]) if payload.get("cited_by_count") is not None else None
        ),
        venue=str(payload["venue"]) if payload.get("venue") else None,
        full_text_url=str(payload["full_text_url"]) if payload.get("full_text_url") else None,
        is_open_access=(
            bool(payload["is_open_access"]) if payload.get("is_open_access") is not None else None
        ),
        sources=tuple(str(item) for item in payload.get("sources") or ()),
        source_ids=tuple(str(item) for item in payload.get("source_ids") or ()),
        discovery_paths=tuple(str(item) for item in payload.get("discovery_paths") or ()),
        referenced_works=tuple(str(item) for item in payload.get("referenced_works") or ()),
    )


def _candidate_payload_year(payload: Mapping[str, Any]) -> int | None:
    year = payload.get("published_year")
    if isinstance(year, int):
        return year if year > 1 else None
    date = payload.get("published_at")
    if not date:
        return None
    try:
        year = datetime.fromisoformat(str(date)).year
    except ValueError:
        return None
    return year if year > 1 else None


def _candidate_key(candidate: PaperCandidate) -> str:
    return candidate.arxiv_id or candidate.candidate_id


def _metadata(payload: Mapping[str, Any]) -> EnrichedMetadata:
    return EnrichedMetadata(
        source=str(payload.get("source") or ""),
        matched_title=payload.get("matched_title"),
        doi=payload.get("doi"),
        cited_by_count=payload.get("cited_by_count"),
        venue=payload.get("venue"),
        year=payload.get("year"),
        is_open_access=payload.get("is_open_access"),
        full_text_url=payload.get("full_text_url"),
        error_code=payload.get("error_code"),
        error_message=payload.get("error_message"),
    )


def _record_payload(record: ModuleQueryRecord) -> dict[str, Any]:
    return record.model_dump(mode="json")


def _record(payload: Mapping[str, Any]) -> ModuleQueryRecord:
    return ModuleQueryRecord.model_validate(payload)


def _reading(payload: Mapping[str, Any] | None) -> PaperReading | None:
    if not payload:
        return None
    from bridges.paper.reading import PaperSection

    return PaperReading(
        arxiv_id="",
        scope=str(payload.get("scope") or "abstract"),
        sections=tuple(
            PaperSection(
                name=str(item.get("name") or ""),
                text=str(item.get("text") or ""),
                locator=item.get("locator"),
            )
            for item in payload.get("sections") or ()
        ),
        notes=tuple(str(item) for item in payload.get("notes") or ()),
    )


def _scope(reading: PaperReading | None) -> PaperReadScope:
    if reading is None:
        return PaperReadScope.ABSTRACT
    if reading.scope == "full_text":
        return PaperReadScope.FULL_TEXT
    if reading.scope == "partial":
        return PaperReadScope.PARTIAL
    return PaperReadScope.ABSTRACT


def _unverified(
    item: PaperRecommendation,
    claims: list[str],
    reading: PaperReading | None,
) -> list[str]:
    notes = list(item.unverified)
    if _scope(reading) is PaperReadScope.ABSTRACT:
        notes.append("未读全文，方法/实验/局限断言不成立")
    if item.doi is None and item.venue is None:
        notes.append("未核对发表信息")
    if item.cited_by_count is not None:
        notes.append("引用量只作辅助信息，不代表质量或入门性")
    return list(dict.fromkeys(notes))


def _violates_year(analysis: PaperTermAnalysis, item: PaperRecommendation) -> bool:
    if item.published_year is None:
        return False
    year_from = analysis.constraints.year_from
    year_to = analysis.constraints.year_to
    if year_from is not None and item.published_year < year_from:
        return True
    return year_to is not None and item.published_year > year_to


def _violates_specification(analysis: PaperTermAnalysis, item: PaperRecommendation) -> bool:
    """指定论文条件不得被其他论文替代（身份或标题不一致即违约）。"""
    constraints = analysis.constraints
    if constraints.arxiv_id:
        if item.arxiv_id is None:
            return True
        left = item.arxiv_id.split("v")[0].lower()
        right = constraints.arxiv_id.split("v")[0].lower()
        if left != right:
            return True
    title = constraints.paper_title
    if title:
        return " ".join(item.title.lower().split()) != " ".join(title.lower().split())
    return False


def _allowed_metadata_sources(analysis: PaperTermAnalysis) -> list[str]:
    """按来源硬条件计算允许调用的元数据补充来源（空表示不得调用）。"""
    constraints = analysis.constraints
    allowed = {item.lower() for item in constraints.allowed_sources}
    excluded = {item.lower() for item in constraints.excluded_sources}
    return [
        source
        for source in (CROSSREF_SOURCE, OPENALEX_SOURCE)
        if source not in excluded and (not allowed or source in allowed)
    ]


def _conflicts(
    metadata: dict[str, EnrichedMetadata], recommendations: list[PaperRecommendation]
) -> list[str]:
    conflicts: list[str] = []
    for item in recommendations:
        enriched = metadata.get(item.arxiv_id or item.candidate_id)
        if enriched is None or enriched.year is None or item.published_year is None:
            continue
        if abs(enriched.year - item.published_year) > 1:
            conflicts.append(
                f"{item.title}：来源年份 {item.published_year} 与核对年份 {enriched.year} 不一致"
            )
    return conflicts


def _source_refs(payload: Mapping[str, Any]) -> tuple[str, ...]:
    refs: list[str] = []
    for candidate in payload.get("candidates") or ():
        arxiv_id = candidate.get("arxiv_id")
        if arxiv_id:
            refs.append(str(arxiv_id))
        elif candidate.get("candidate_id"):
            refs.append(str(candidate["candidate_id"]))
    for item in payload.get("selection") or ():
        if isinstance(item, Mapping) and (item.get("arxiv_id") or item.get("candidate_id")):
            refs.append(str(item.get("arxiv_id") or item.get("candidate_id")))
    return tuple(dict.fromkeys(refs))


def _payload_read_scope(payload: Mapping[str, Any]) -> str:
    if "readings" in payload:
        scopes = {item.get("scope") for item in payload["readings"].values()}
        if scopes == {"abstract"}:
            return "abstract"
        if "full_text" in scopes:
            return "full_text"
        return "partial"
    return "none"
