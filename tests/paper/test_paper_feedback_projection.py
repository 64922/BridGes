"""缺口补搜查询记录必须进入最终论文结果投影。"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from bridges.contracts.modules import ModuleQueryRecord, ModuleQueryStatus
from bridges.kernel.contracts import NodeInvocation, RecipeInputs
from bridges.paper.contracts import (
    PaperConstraints,
    PaperResearchConcept,
    PaperTermAnalysis,
)
from bridges.paper.kernel import (
    NODE_PARSE,
    NODE_SCREEN,
    NODE_SEARCH,
    PaperNodeFlow,
    build_paper_recipe,
)
from bridges.paper.screening import candidate_payload
from bridges.paper.sources import CandidateSearchOutcome, PaperCandidate


def test_feedback_query_is_recorded_in_screen_artifact() -> None:
    class Source:
        source = "openalex"

        def __init__(self) -> None:
            self.queries: list[str] = []

        def search(self, _account_id: str, query: str, **_kwargs: Any) -> CandidateSearchOutcome:
            self.queries.append(query)
            candidate = PaperCandidate(
                arxiv_id=None,
                title="Deep learning for bearing fault diagnosis",
                authors=["A. Researcher"],
                published_at=datetime(2024, 1, 1, tzinfo=UTC),
                abs_url="https://openalex.org/W2",
                pdf_url="",
                abstract="Deep learning methods address bearing fault diagnosis.",
                source="openalex",
                source_id="W2",
                openalex_id="https://openalex.org/W2",
            )
            return CandidateSearchOutcome(
                query=query,
                candidates=[candidate],
                record=ModuleQueryRecord(
                    source=self.source,
                    query=query,
                    status=ModuleQueryStatus.SUCCESS,
                    evidence_count=1,
                ),
            )

    source = Source()
    analysis = PaperTermAnalysis(
        original_phrase="深度学习、轴承故障诊断",
        original_request="请找用深度学习做轴承故障诊断的论文",
        normalized_term="深度学习、轴承故障诊断",
        research_object="轴承故障诊断",
        research_question="深度学习如何用于轴承故障诊断",
        confidence=1.0,
        concepts=[
            PaperResearchConcept(
                concept="深度学习",
                source_phrase="深度学习",
                kind="method",
                english_search_terms=["deep learning", "neural network"],
            ),
            PaperResearchConcept(
                concept="轴承故障诊断",
                source_phrase="轴承故障诊断",
                kind="application",
                english_search_terms=["bearing fault diagnosis", "bearing fault classification"],
            ),
        ],
        recommended_sources=["openalex", "crossref"],
        constraints=PaperConstraints(),
        final_query="deep learning bearing fault diagnosis",
    )
    initial_candidate = PaperCandidate(
        arxiv_id=None,
        title="Deep learning for rotating machinery",
        authors=["B. Researcher"],
        published_at=datetime(2023, 1, 1, tzinfo=UTC),
        abs_url="https://openalex.org/W1",
        pdf_url="",
        abstract="Deep learning models are used for rotating machinery.",
        source="openalex",
        source_id="W1",
        openalex_id="https://openalex.org/W1",
    )
    initial_query = "deep learning bearing fault diagnosis"
    search_record = ModuleQueryRecord(
        source="openalex",
        query=initial_query,
        status=ModuleQueryStatus.SUCCESS,
        evidence_count=1,
    )
    parse_payload = {"analysis": analysis.model_dump(mode="json")}
    search_payload = {
        "candidates": [candidate_payload(initial_candidate)],
        "records": [search_record.model_dump(mode="json")],
        "target_count": 2,
    }
    parse_artifact = SimpleNamespace(
        artifact_id="parse-artifact", content_hash="parse-hash", payload=parse_payload
    )
    search_artifact = SimpleNamespace(
        artifact_id="search-artifact", content_hash="search-hash", payload=search_payload
    )
    inputs = RecipeInputs(
        account_id="account",
        conversation_id="conversation",
        run_id="run",
        user_message_id="message",
        user_content=analysis.original_request,
        task_id=None,
        task_version=None,
        wait_identity=None,
        artifacts={NODE_PARSE: parse_artifact, NODE_SEARCH: search_artifact},
    )
    dependencies = {NODE_PARSE: parse_artifact, NODE_SEARCH: search_artifact}
    spec = next(item for item in build_paper_recipe().nodes if item.name == NODE_SCREEN)
    flow = PaperNodeFlow(source=source, sources={"openalex": source}, enricher=None)

    result = flow.run_node(
        NodeInvocation(
            spec=spec,
            inputs=inputs,
            dependencies=dependencies,
            remaining_budget_ms=5000,
        )
    )

    assert len(source.queries) == 1
    assert source.queries[0] != initial_query
    screen_queries = [item["query"] for item in result.artifact.payload["records"]]
    assert screen_queries == [source.queries[0]]
