"""论文读取范围与有限元数据补充节点。"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from bridges.kernel.contracts import NodeExecution, NodeInvocation
from bridges.paper.artifact_codec import _allowed_metadata_sources, _candidate, _record_payload
from bridges.paper.enrichment import _candidate_key
from bridges.paper.reading import GOAL_CANDIDATES
from bridges.paper.recipe import NODE_PARSE, NODE_SCREEN, NODE_SEARCH
from bridges.paper.screening import ScreenedCandidate
from bridges.paper.source_types import EnrichedMetadata, EnrichOutcome

if TYPE_CHECKING:
    from bridges.paper.kernel import PaperNodeFlow



def read_node(self: PaperNodeFlow, invocation: NodeInvocation) -> NodeExecution:
    screened = [
        ScreenedCandidate(
            candidate=_candidate(item["candidate"]),
            matched_requirements=tuple(item.get("matched_requirements") or ()),
            evidence=tuple(item.get("evidence") or ()),
            strength=str(item.get("strength") or "weak"),
            unconfirmed=tuple(item.get("unconfirmed") or ()),
            relevance=str(item.get("relevance") or "direct"),
            research_group=str(item.get("research_group") or ""),
        )
        for item in invocation.dependencies[NODE_SCREEN].payload.get("screened") or ()
    ]
    goal = str(
        invocation.dependencies[NODE_PARSE].payload.get("reading_goal") or GOAL_CANDIDATES
    )
    outcome = self._reader.read(
        screened,
        goal=goal,
        account_id=invocation.account_id,
        budget=self._budget,
        stop_event=self._stop_event,
    )
    payload = {
        "readings": {
            candidate_key: {
                "scope": reading.scope,
                "sections": [
                    {"name": section.name, "text": section.text, "locator": section.locator}
                    for section in reading.sections
                ],
                "notes": list(reading.notes),
            }
            for candidate_key, reading in outcome.readings.items()
        },
        "notes": list(outcome.notes),
        "deep_read_count": outcome.deep_read_count,
    }
    return self._completed(invocation, payload)


def enrich_node(self: PaperNodeFlow, invocation: NodeInvocation) -> NodeExecution:
    candidates = [
        _candidate(item)
        for item in invocation.dependencies[NODE_SEARCH].payload.get("candidates") or []
    ]
    analysis = self._analysis(invocation, NODE_PARSE)
    metadata_sources = _allowed_metadata_sources(analysis)
    metadata: dict[str, EnrichedMetadata] = {}
    for candidate in candidates:
        if any(
            (
                candidate.doi,
                candidate.cited_by_count is not None,
                candidate.venue,
                candidate.full_text_url,
            )
        ):
            metadata[_candidate_key(candidate)] = EnrichedMetadata(
                source=candidate.source,
                matched_title=candidate.title,
                doi=candidate.doi,
                cited_by_count=candidate.cited_by_count,
                venue=candidate.venue,
                year=candidate.published_year,
                is_open_access=candidate.is_open_access,
                full_text_url=candidate.full_text_url,
            )
    lookups = [
        candidate
        for candidate in candidates
        if candidate.arxiv_id and _candidate_key(candidate) not in metadata
    ]
    available = self._enricher is not None and bool(metadata_sources)
    notes: list[str] = []
    if not metadata_sources:
        notes.append("来源条件未允许 Crossref/OpenAlex，本轮未调用这两个补充来源。")
    elif self._enricher is None:
        notes.append("本轮候选已用搜索来源返回的元数据；缺失项未额外查询。")
    elif not lookups:
        notes.append("搜索来源已提供本轮所需元数据，未重复发起逐篇补充查询。")
    elif not metadata:
        notes.append("未装配发表信息补充适配器，本轮未核对 DOI/venue/引用量。")
    if self._enricher is None or not metadata_sources or not lookups:
        payload = {
            "metadata": {key: item.__dict__ for key, item in metadata.items()},
            "records": [],
            "available": available,
            "notes": notes,
        }
        return self._completed(invocation, payload)
    try:
        outcome: EnrichOutcome = self._enricher.enrich(
            lookups,
            account_id=invocation.account_id,
            need_publication_info=True,
            deadline=time.monotonic()
            + min(
                self._enrich_deadline_seconds,
                (
                    self._budget.deadline_seconds()
                    if self._budget
                    else self._enrich_deadline_seconds
                ),
            ),
            budget=self._budget,
            stop_event=self._stop_event,
            sources=metadata_sources,
        )
    except Exception:  # noqa: BLE001 - 可选补充失败只留缺口，不阻断交付
        payload = {
            "metadata": {key: item.__dict__ for key, item in metadata.items()},
            "records": [],
            "available": False,
            "notes": ["发表信息补充失败，本轮未核对 DOI/venue/引用量。"],
        }
        return self._completed(invocation, payload)
    self._enrich_records.extend(outcome.records)
    metadata.update(outcome.metadata)
    if len(metadata) < len(candidates):
        notes.append("部分候选未核对到发表信息，缺失项保持未确认。")
    payload = {
        "metadata": {
            key: {
                "source": item.source,
                "matched_title": item.matched_title,
                "doi": item.doi,
                "cited_by_count": item.cited_by_count,
                "venue": item.venue,
                "year": item.year,
                "is_open_access": item.is_open_access,
                "full_text_url": item.full_text_url,
                "error_code": item.error_code,
                "error_message": item.error_message,
            }
            for key, item in metadata.items()
        },
        "records": [_record_payload(item) for item in outcome.records],
        "available": True,
        "notes": notes,
    }
    return self._completed(invocation, payload)
