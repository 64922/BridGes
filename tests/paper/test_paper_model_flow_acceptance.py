"""通过实际论文节点证明模型查询、反馈与跨轮引用进入下游行为。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from bridges.contracts.modules import ModuleQueryRecord, ModuleQueryStatus
from bridges.kernel.contracts import NodeInvocation, RecipeInputs
from bridges.paper.contracts import PaperCitationEdge, PaperResearchConcept, PaperTermAnalysis
from bridges.paper.discovery import CitationExpansionOutcome
from bridges.paper.kernel import (
    NODE_PARSE,
    NODE_PLAN,
    NODE_SCREEN,
    NODE_SEARCH,
    PaperFlowContext,
    PaperNodeFlow,
    build_paper_recipe,
)
from bridges.paper.relevance_cache import NodeRelevanceCache
from bridges.paper.source_types import CandidateSearchOutcome, PaperCandidate


def _analysis() -> PaperTermAnalysis:
    return PaperTermAnalysis(
        original_phrase="深度学习、轴承故障诊断", normalized_term="深度学习、轴承故障诊断",
        confidence=1, recommended_sources=["openalex", "arxiv"],
        concepts=[PaperResearchConcept(
            concept=concept, source_phrase=concept, kind=kind, english_search_terms=terms,
        ) for concept, kind, terms in (
            ("深度学习", "method", ["deep learning", "neural network"]),
            ("轴承故障诊断", "application", ["bearing fault diagnosis", "bearing diagnosis"]),
        )],
    )


def _candidate(identifier: str) -> PaperCandidate:
    return PaperCandidate(
        arxiv_id=None, title="Deep learning bearing fault diagnosis", authors=["A"],
        published_at=datetime(2024, 1, 1, tzinfo=UTC), abs_url=f"https://openalex.org/{identifier}",
        pdf_url="", abstract="Deep learning studies bearing fault diagnosis.",
        source="openalex", openalex_id=f"https://openalex.org/{identifier}",
    )


class _Planner:
    def __init__(self) -> None:
        self.feedback: list[dict[str, Any]] = []
        self.analyzed: list[dict[str, Any]] = []

    def analyze(self, _request: str, **kwargs: Any) -> Any:
        self.analyzed.append(kwargs)
        return _analysis()

    def plan(self, analysis: Any, *, available_sources: Any, feedback: Any = None) -> Any:
        from bridges.paper.query_planning import model_plan_queries

        if feedback:
            self.feedback.append(feedback)

        def invoke(*_args: Any, **_kwargs: Any) -> Any:
            return {"queries": [{
                "source": source,
                "query": (
                    "neural network bearing diagnosis" if feedback
                    else 'all:"deep learning" AND all:"bearing fault diagnosis"'
                    if source == "arxiv" else "deep learning bearing fault diagnosis"
                ),
            } for source in available_sources]}

        return model_plan_queries(invoke, analysis, available_sources, feedback=feedback)


class _Source:
    def __init__(self, source: str) -> None:
        self.source = source
        self.queries: list[str] = []
        self.seeds: list[str] = []

    def search(self, _account: str, query: str, **_kwargs: Any) -> Any:
        self.queries.append(query)
        return CandidateSearchOutcome(query, [_candidate(f"W{len(self.queries)}")],
            ModuleQueryRecord(source=self.source, query=query, status=ModuleQueryStatus.SUCCESS))

    def expand_citations(self, _account: str, seeds: Any, **_kwargs: Any) -> Any:
        self.seeds.extend(item.candidate_id for item in seeds)
        citing = _candidate("W99")
        return CitationExpansionOutcome([citing], [PaperCitationEdge(
            citing_candidate_id=citing.candidate_id, cited_candidate_id=seeds[0].candidate_id,
            citing_title=citing.title, cited_title=seeds[0].title,
        )], [ModuleQueryRecord(
            source="openalex", query="cites:W42", status=ModuleQueryStatus.SUCCESS,
        )])


class _Judge:
    def judge_many(self, _analysis: Any, candidates: Any) -> Any:
        return {candidate.candidate_id: {"relevant": True, "evidence": [
                    {"requirement": "深度学习", "source": "title", "quote": "Deep learning"},
                    {"requirement": "轴承故障诊断", "source": "title",
                     "quote": "bearing fault diagnosis"},
                ]}
                for candidate in candidates}


def _run_nodes(flow: PaperNodeFlow) -> dict[str, Any]:
    artifacts: dict[str, Any] = {}
    inputs = RecipeInputs(
        account_id="account", conversation_id="conversation", run_id="run",
        user_message_id="message", user_content="沿第二篇找后续工作",
        task_id="task", task_version=2, wait_identity=None, artifacts={},
    )
    for name in (NODE_PARSE, NODE_PLAN, NODE_SEARCH, NODE_SCREEN):
        spec = next(item for item in build_paper_recipe().nodes if item.name == name)
        result = flow.run_node(NodeInvocation(
            spec=spec, inputs=replace(inputs, artifacts=artifacts),
            dependencies=artifacts, remaining_budget_ms=10000,
        ))
        assert result.failure is None
        artifacts[name] = result.artifact
    return artifacts


def test_model_plans_reach_each_source_and_feedback_contains_actual_evidence() -> None:
    planner = _Planner()
    sources = {name: _Source(name) for name in ("arxiv", "openalex")}
    flow = PaperNodeFlow(
        source=sources["arxiv"], sources=sources, intent_analyzer=planner,
        enricher=None, judge=_Judge(),
    )
    artifacts = _run_nodes(flow)

    assert sources["arxiv"].queries[0].startswith('all:"deep learning"')
    assert sources["openalex"].queries[0] == "deep learning bearing fault diagnosis"
    assert sources["openalex"].queries[1] == "neural network bearing diagnosis"
    recorded = {item["source"]: item["query"] for item in planner.feedback[0]["queries"]}
    assert recorded["arxiv"] == sources["arxiv"].queries[0]
    assert planner.feedback[0]["semantic_evidence"]
    assert artifacts[NODE_SCREEN].payload["records"][0]["query"] == sources["openalex"].queries[1]


def test_cross_round_reference_is_the_only_citation_seed_and_old_paper_is_not_followup() -> None:
    seed = _candidate("W42")
    planner = _Planner()
    source = _Source("openalex")
    flow = PaperNodeFlow(
        source=source, sources={"openalex": source}, intent_analyzer=planner,
        enricher=None, judge=_Judge(), context=PaperFlowContext(citation_seed=seed),
    )
    artifacts = _run_nodes(flow)

    assert planner.analyzed[0]["citation_seed"] is seed
    assert source.seeds == [seed.candidate_id]
    screened_ids = [row["candidate"]["candidate_id"]
                    for row in artifacts[NODE_SCREEN].payload["screened"]]
    assert seed.candidate_id not in screened_ids
    assert "openalex:w99" in screened_ids
    edge = artifacts[NODE_SCREEN].payload["citation_edges"][0]
    assert edge["cited_candidate_id"] == seed.candidate_id


def test_invalid_cross_round_reference_stops_before_model_and_sources() -> None:
    planner = _Planner()
    source = _Source("openalex")
    flow = PaperNodeFlow(
        source=source, intent_analyzer=planner, enricher=None,
        context=PaperFlowContext(continuation_error="未找到指定论文"),
    )
    inputs = RecipeInputs(
        account_id="account", conversation_id="conversation", run_id="run",
        user_message_id="message", user_content="沿第二篇找后续工作",
        task_id=None, task_version=None, wait_identity=None, artifacts={},
    )
    spec = next(item for item in build_paper_recipe().nodes if item.name == NODE_PARSE)
    result = flow.run_node(NodeInvocation(
        spec=spec, inputs=inputs, dependencies={}, remaining_budget_ms=10000,
    ))
    assert result.failure is not None
    assert result.failure["code"] == "paper_reference_unavailable"
    assert planner.analyzed == []
    assert source.queries == []


def test_node_cache_reuses_actual_evidence_and_only_judges_new_or_changed_candidates() -> None:
    calls: list[list[str]] = []

    class Judge(_Judge):
        def judge_many(self, analysis: Any, candidates: Any) -> Any:
            calls.append([item.candidate_id for item in candidates])
            return super().judge_many(analysis, candidates)

    cache = NodeRelevanceCache(Judge())
    first, second = _candidate("W1"), _candidate("W2")
    cache.judge_many(_analysis(), [first])
    cache.judge_many(_analysis(), [first, second])
    cache.judge_many(_analysis(), [first, second])
    changed = replace(first, abstract="Updated real abstract")
    cache.judge_many(_analysis(), [changed])
    assert calls == [[first.candidate_id], [second.candidate_id], [first.candidate_id]]


def test_failed_semantic_evaluation_stops_recommendations_and_keeps_actual_queries() -> None:
    class FailedJudge:
        def judge_many(self, _analysis: Any, _candidates: Any) -> Any:
            raise RuntimeError("budget exhausted")

    source = _Source("openalex")
    flow = PaperNodeFlow(source=source, intent_analyzer=_Planner(), enricher=None,
                         judge=FailedJudge())
    candidate = _candidate("W1")
    from bridges.paper.candidate_codec import candidate_payload

    artifacts: dict[str, Any] = {
        NODE_PARSE: SimpleNamespace(
            artifact_id="parse", content_hash="parse",
            payload={"analysis": _analysis().model_dump(mode="json")},
        ),
        NODE_SEARCH: SimpleNamespace(
            artifact_id="search", content_hash="search", payload={
                "candidates": [candidate_payload(candidate)], "target_count": 8,
                "records": [ModuleQueryRecord(
                    source="openalex", query="deep learning bearing fault diagnosis",
                    status=ModuleQueryStatus.SUCCESS,
                ).model_dump(mode="json")],
            },
        ),
    }
    inputs = RecipeInputs(
        account_id="account", conversation_id="conversation", run_id="run",
        user_message_id="message", user_content="请找论文", task_id=None,
        task_version=None, wait_identity=None, artifacts=artifacts,
    )
    spec = next(item for item in build_paper_recipe().nodes if item.name == NODE_SCREEN)
    result = flow.run_node(NodeInvocation(
        spec=spec, inputs=inputs, dependencies=artifacts, remaining_budget_ms=10000,
    ))
    assert result.failure is not None
    assert result.failure["code"] == "paper_semantic_evaluation_unavailable"
    assert result.failure["retryable"] is True
    assert result.artifact.payload["screened"] == []
    assert result.artifact.payload["records"][0]["query"] == source.queries[0]
