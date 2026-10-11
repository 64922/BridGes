"""论文推荐排序、身份核验与证据质量门节点。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from bridges.kernel.contracts import NodeArtifact, NodeExecution, NodeInvocation
from bridges.paper.artifact_codec import (
    _candidate,
    _candidate_payload_year,
    _conflicts,
    _metadata,
    _reading,
    _scope,
    _unverified,
    _violates_specification,
    _violates_year,
)
from bridges.paper.contracts import (
    PaperCandidateProjection,
    PaperIdentity,
    PaperQueryPlan,
    PaperReadScope,
    PaperRecommendation,
    PaperRelevance,
    PaperRequirementEvidence,
)
from bridges.paper.enrichment import _candidate_key
from bridges.paper.planning import DEFAULT_TARGET_COUNT
from bridges.paper.ranking import rank_candidates
from bridges.paper.reading import supported_claims
from bridges.paper.recipe import (
    NODE_ENRICH,
    NODE_EVALUATE,
    NODE_PARSE,
    NODE_PLAN,
    NODE_READ,
    NODE_SCREEN,
)

if TYPE_CHECKING:
    from bridges.paper.kernel import PaperNodeFlow



def evaluate_node(self: PaperNodeFlow, invocation: NodeInvocation) -> NodeExecution:
    analysis = self._analysis(invocation, NODE_PARSE)
    plan_payload = invocation.dependencies[NODE_PLAN].payload
    plan = PaperQueryPlan.model_validate((plan_payload.get("plans") or [{}])[0])
    screen_payload = invocation.dependencies[NODE_SCREEN].payload
    read_payload = invocation.dependencies[NODE_READ].payload
    enrich_payload = invocation.dependencies[NODE_ENRICH].payload
    screened_payload = screen_payload.get("screened") or []
    metadata = {
        arxiv_id: _metadata(item)
        for arxiv_id, item in (enrich_payload.get("metadata") or {}).items()
    }
    comparison_requested = self._comparison_requested(invocation)
    blocked = screen_payload.get("hard_condition_blocked")
    if not screened_payload:
        outcome = "empty"
        notes = list(screen_payload.get("notes") or ())
        if blocked == "year":
            notes.append("年份条件内没有主题匹配的真实结果；未放宽年份，保持空结果。")
        elif blocked == "specified_paper":
            notes.append("没有检索到指定论文；未改用其他论文充当结果。")
        elif blocked == "source":
            notes.append("来源条件内没有可用候选；未改用未允许的来源。")
        else:
            notes.append("本轮没有取得主题匹配的真实论文，未凑篇数。")
        payload = {
            "recommendations": [],
            "selection": [],
            "candidates": [],
            "citation_edges": screen_payload.get("citation_edges") or [],
            "identity_missing": [],
            "unsupported_claims": [],
            "hard_condition_violations": [],
            "outcome": outcome,
            "notes": notes,
            "comparison_requested": comparison_requested,
            "conflicts": [],
            "unconfirmed": ["本轮没有可交付的论文结果"],
        }
        return self._completed(invocation, payload)
    outcome = "success"
    candidates = [_candidate(item["candidate"]) for item in screened_payload]
    ranked = rank_candidates(
        analysis,
        plan,
        candidates,
        metadata,
        target_count=int(plan_payload.get("target_count") or DEFAULT_TARGET_COUNT),
        evidence_matches={
            _candidate_key(_candidate(item["candidate"])): list(item["matched_requirements"])
            for item in screened_payload
        },
    )
    evidence_by_id: dict[str, Mapping[str, Any]] = {}
    for screen_item in screened_payload:
        screen_candidate = _candidate(screen_item["candidate"])
        evidence_by_id[_candidate_key(screen_candidate)] = screen_item
        evidence_by_id[screen_candidate.candidate_id] = screen_item
    readings = {
        arxiv_id: _reading(item)
        for arxiv_id, item in (read_payload.get("readings") or {}).items()
    }
    recommendations: list[PaperRecommendation] = []
    identity_missing: list[str] = []
    unsupported: list[str] = []
    violations: list[str] = []
    for item in ranked.recommendations:
        paper_key = item.arxiv_id or item.candidate_id
        enriched = metadata.get(paper_key)
        reading = readings.get(paper_key)
        evidence = evidence_by_id.get(paper_key, {})
        candidate = evidence.get("candidate") or {}
        claims = supported_claims(reading)
        item.doi = enriched.doi if enriched and enriched.doi else item.doi
        item.venue = enriched.venue if enriched and enriched.venue else item.venue
        item.cited_by_count = (
            enriched.cited_by_count
            if enriched and enriched.cited_by_count is not None
            else item.cited_by_count
        )
        item.full_text_url = (
            enriched.full_text_url
            if enriched and enriched.full_text_url
            else item.full_text_url
        )
        item.read_scope = _scope(reading)
        item.full_text_available = bool(item.pdf_url or item.full_text_url)
        item.source_abstract = str(candidate.get("abstract") or "")
        item.relevance = PaperRelevance(str(evidence.get("relevance") or "direct"))
        item.research_group = str(evidence.get("research_group") or "")
        item.match_evidence = [
            PaperRequirementEvidence(
                requirement=str(entry.get("requirement") or ""),
                source=str(entry.get("source") or ""),
                quote=str(entry.get("quote") or ""),
            )
            for entry in evidence.get("evidence") or ()
        ]
        item.read_evidence = [
            PaperRequirementEvidence(
                requirement=item.title,
                source=f"full_text:{section.name}",
                quote=section.text[:400],
            )
            for section in (reading.sections if reading is not None else ())
            if section.text.strip()
        ]
        item.supported_claims = claims
        if item.read_scope is not PaperReadScope.ABSTRACT and not claims:
            # 宣称读到正文却没有可定位的小节证据：不得冒充精读。
            unsupported.append(f"{item.title}：宣称正文级阅读但未定位到正文依据")
        item.unverified = _unverified(item, claims, reading)
        recommendations.append(item)
        if not item.candidate_id and item.arxiv_id is None and item.doi is None:
            identity_missing.append(item.title)
        if _violates_year(analysis, item):
            violations.append(item.title)
        if _violates_specification(analysis, item):
            violations.append(item.title)
    if ranked.topic_mismatch:
        outcome = "topic_mismatch"
    selection = [
        PaperIdentity(
            order=item.order,
            candidate_id=item.candidate_id,
            arxiv_id=item.arxiv_id,
            doi=item.doi,
            title=item.title,
            published_year=item.published_year,
            abs_url=item.abs_url,
            content_hash=NodeArtifact.hash_payload(item.model_dump(mode="json")),
        )
        for item in recommendations
    ]
    conflicts = _conflicts(metadata, recommendations)
    candidate_projections = [
        PaperCandidateProjection(
            candidate_id=str(item["candidate"]["candidate_id"]),
            arxiv_id=item["candidate"].get("arxiv_id"),
            doi=item["candidate"].get("doi"),
            title=str(item["candidate"].get("title") or ""),
            authors=[str(value) for value in item["candidate"].get("authors") or ()],
            published_year=_candidate_payload_year(item["candidate"]),
            primary_source=str(item["candidate"].get("source") or "unknown"),
            sources=[str(value) for value in item["candidate"].get("sources") or ()],
            source_ids=[str(value) for value in item["candidate"].get("source_ids") or ()],
            abs_url=str(item["candidate"].get("abs_url") or ""),
            pdf_url=str(item["candidate"].get("pdf_url") or "") or None,
            full_text_url=str(item["candidate"].get("full_text_url") or "") or None,
            abstract=str(item["candidate"].get("abstract") or ""),
            relevance=PaperRelevance(str(item.get("relevance") or "direct")),
            research_group=str(item.get("research_group") or ""),
            match_evidence=[
                PaperRequirementEvidence(
                    requirement=str(entry.get("requirement") or ""),
                    source=str(entry.get("source") or ""),
                    quote=str(entry.get("quote") or ""),
                )
                for entry in item.get("evidence") or ()
            ],
            discovery_paths=[
                str(value) for value in item["candidate"].get("discovery_paths") or ()
            ],
        ).model_dump(mode="json")
        for item in screened_payload
    ]
    payload = {
        "recommendations": [item.model_dump(mode="json") for item in recommendations],
        "selection": [item.model_dump(mode="json") for item in selection],
        "candidates": candidate_projections,
        "citation_edges": invocation.dependencies[NODE_SCREEN].payload.get("citation_edges")
        or [],
        "identity_missing": identity_missing,
        "unsupported_claims": unsupported,
        "hard_condition_violations": violations,
        "outcome": outcome,
        "notes": list(ranked.notes),
        "comparison_requested": comparison_requested,
        "conflicts": conflicts,
        "unconfirmed": [],
    }
    return self._completed(invocation, payload)


def verify_node(self: PaperNodeFlow, invocation: NodeInvocation) -> NodeExecution:
    evaluate_payload = invocation.dependencies[NODE_EVALUATE].payload
    recommendations = [
        PaperRecommendation.model_validate(item)
        for item in evaluate_payload.get("recommendations") or []
    ]
    unsupported = list(evaluate_payload.get("unsupported_claims") or [])
    for item in recommendations:
        if item.read_scope is PaperReadScope.ABSTRACT and item.supported_claims:
            # 跨版本防线：旧产物若在未读正文时留下正文断言，仍要阻塞。
            unsupported.append(f"{item.title}：{','.join(item.supported_claims)}")
    unconfirmed: list[str] = []
    for item in recommendations:
        unconfirmed.extend(item.unverified)
    unconfirmed.extend(evaluate_payload.get("unconfirmed") or [])
    triggered = bool(evaluate_payload.get("comparison_requested")) or bool(
        evaluate_payload.get("conflicts")
    )
    review: dict[str, Any] = {"triggered": triggered, "executed": False}
    if triggered and self._reviewer is not None:
        result = self._reviewer(
            recommendations,
            [str(item) for item in (evaluate_payload.get("conflicts") or [])],
        )
        if result is not None:
            # 裁决结构无效（非映射或缺少 passed）按未通过处理，不冒充通过。
            verdict = (
                dict(result)
                if isinstance(result, Mapping)
                else {"passed": False, "reason": "invalid_review_payload"}
            )
            if verdict.get("passed") is not True:
                verdict.setdefault("reason", "independent_review_not_passed")
            review = {"triggered": True, "executed": True, "result": verdict}
    notes = list(evaluate_payload.get("notes") or ())
    if triggered and not review.get("executed"):
        if self._reviewer is None:
            notes.append(
                "本轮存在复杂比较或来源冲突，独立复核未装配，"
                "相关结论按证据直接呈现，未冒充已复核。"
            )
        else:
            notes.append(
                "本轮存在复杂比较或来源冲突，独立复核未取得有效裁决，相关结论按证据直接呈现。"
            )
        unconfirmed.append("独立复核未执行（未装配或裁决无效）")
    passed_checks: list[str] = []
    if not evaluate_payload.get("identity_missing"):
        passed_checks.append("paper.identity_present")
    if not unsupported:
        passed_checks.append("paper.claims_have_evidence")
    if not evaluate_payload.get("hard_condition_violations"):
        passed_checks.append("paper.hard_conditions_hold")
    payload = {
        "passed_checks": passed_checks,
        "unsupported_claims": unsupported,
        "hard_condition_violations": list(
            evaluate_payload.get("hard_condition_violations") or []
        ),
        "unconfirmed": list(dict.fromkeys(unconfirmed)),
        "independent_review": review,
        "notes": notes,
    }
    return self._completed(
        invocation,
        payload,
        requirement_coverage=tuple(
            {"requirement": item.arxiv_id or item.title, "status": item.read_scope.value}
            for item in recommendations
        ),
        unconfirmed=tuple(payload["unconfirmed"]),
    )
