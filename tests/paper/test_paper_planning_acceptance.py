"""来源查询、预算内反馈与跨轮产物引用的验收回归。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from bridges.paper.continuation import resolve_paper_continuation
from bridges.paper.contracts import PaperConstraints, PaperResearchConcept, PaperTermAnalysis
from bridges.paper.planning import plan_queries
from bridges.paper.query_planning import model_plan_queries


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
        constraints=PaperConstraints(year_from=2023, exclude_survey=True),
    )


def test_source_model_query_and_feedback_preserve_concepts_and_hard_conditions() -> None:
    calls: list[dict[str, Any]] = []

    def invoke(data: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        calls.append(data)
        assert kwargs["json_schema"]["properties"]["queries"]
        return {"queries": [
            {"source": "arxiv", "query": 'all:"deep learning" AND all:"bearing diagnosis"'},
            {"source": "openalex", "query": "neural network bearing fault diagnosis"},
        ]}

    feedback = {
        "queries": [{"source": "openalex", "query": "deep learning bearing fault diagnosis"}],
        "missing_requirements": ["轴承故障诊断"],
        "semantic_evidence": [{"title": "Deep learning", "covered": ["深度学习"]}],
    }
    plans = model_plan_queries(invoke, _analysis(), ("arxiv", "openalex"), feedback=feedback)

    assert [plan.source for plan in plans] == ["openalex", "arxiv"]
    assert plans[0].query != plans[1].query
    assert calls[0]["feedback"] == feedback
    assert calls[0]["hard_conditions"]["year_from"] == 2023
    assert calls[0]["hard_conditions"]["exclude_survey"] is True
    assert [plan.max_results for plan in plans] == [10, 10]


@pytest.mark.parametrize("query", [
    "deep learning",  # 丢失应用场景。
    "deep learning bearing fault diagnosis 2020",  # 增加未经校验限制。
    "deep learning bearing fault diagnosis token=private-value",  # 增加私密字段。
])
def test_invalid_model_query_never_reaches_source_plan(query: str) -> None:
    def invoke(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"queries": [{"source": "openalex", "query": query}]}

    assert model_plan_queries(invoke, _analysis(), ("openalex",)) == ()


def test_default_candidate_target_twenty_is_independent_of_focus_count() -> None:
    plans = plan_queries(_analysis(), available_sources=("openalex", "arxiv"))
    assert sum(plan.max_results for plan in plans) == 20
    assert all(plan.target_count == 8 for plan in plans)
    analysis = _analysis().model_copy(update={"constraints": PaperConstraints(requested_count=2)})
    assert all(plan.target_count == 2 for plan in plan_queries(analysis))


def _message(identity: str, *, message_id: str = "old") -> Any:
    return SimpleNamespace(message_id=message_id, paper_search={
        "status": "success",
        "selected": [{"order": 2, "candidate_id": identity}],
        "candidates": [{
            "candidate_id": identity, "title": "Bearing fault diagnosis",
            "primary_source": "openalex", "published_year": 2024,
            "abs_url": "https://openalex.org/W2", "source_ids": ["openalex:W2"],
            "abstract": "Neural networks diagnose bearing faults.",
        }],
    })


def test_second_paper_reference_resolves_actual_prior_selected_identity() -> None:
    messages = [_message("doi:10.1/second"), SimpleNamespace(message_id="current"),
                _message("doi:10.1/future", message_id="future")]
    reference = resolve_paper_continuation(
        messages, user_message_id="current", request="沿第二篇找后续工作",
    )
    assert reference.requested
    assert reference.seed is not None
    assert reference.seed.openalex_id == "W2"
    assert reference.seed.abstract == "Neural networks diagnose bearing faults."


def test_unresolved_reference_does_not_silently_search_topic() -> None:
    reference = resolve_paper_continuation(
        [], user_message_id="current", request="沿第二篇找后续工作",
    )
    assert reference.requested
    assert reference.seed is None
    assert reference.error
