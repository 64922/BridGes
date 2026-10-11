"""论文搜索改造：研究意图、硬条件和首次理解失败的行为合同。"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from bridges.ai.model_quota import QuotaVerificationBasis, RunModelQuota
from bridges.contracts.modules import ModuleQueryRecord, ModuleQueryStatus
from bridges.kernel.contracts import NodeInvocation, RecipeInputs
from bridges.paper.contracts import (
    PaperConstraints,
    PaperQueryPlan,
    PaperTermAnalysis,
)
from bridges.paper.intent import PaperResearchIntentAnalyzer
from bridges.paper.kernel import (
    NODE_PARSE,
    NODE_PLAN,
    NODE_SEARCH,
    PaperNodeFlow,
    build_paper_recipe,
)
from bridges.paper.planning import plan_queries
from bridges.paper.sources import CandidateSearchOutcome


class _Gateway:
    def __init__(self, output: dict[str, Any] | None) -> None:
        self.output = output
        self.payloads: list[dict[str, Any]] = []

    def invoke(self, *_args: Any, **kwargs: Any) -> Any:
        self.payloads.append(kwargs["payload"])
        return SimpleNamespace(
            status=SimpleNamespace(value="success" if self.output is not None else "error"),
            output=self.output,
            lock=None,
        )


def _intent_output(*concepts: dict[str, Any]) -> dict[str, Any]:
    return {
        "research_object": "图像分类模型",
        "research_question": "卷积神经网络如何用于小样本图像分类",
        "methods": ["卷积神经网络"],
        "applications": ["小样本图像分类"],
        "reading_purpose": "主题探索",
        "reading_purpose_quote": "",
        "concepts": list(concepts),
        "recommended_sources": ["openalex", "arxiv", "crossref"],
        "clarification_question": "",
        "clarification_missing": "",
    }


def _quota() -> RunModelQuota:
    return RunModelQuota(
        model_id="test-model",
        context_window=32768,
        verification_basis=QuotaVerificationBasis.FACTORY_MATRIX,
    )


def test_model_intent_generates_english_query_for_plain_cnn_exploration() -> None:
    request = "请给我几篇与卷积神经网络有关的论文"
    gateway = _Gateway(
        _intent_output(
            {
                "concept": "卷积神经网络",
                "source_phrase": "卷积神经网络",
                "kind": "method",
                "english_search_terms": ["convolutional neural network", "CNN"],
            }
        )
    )
    analyzer = PaperResearchIntentAnalyzer(
        gateway,
        run_context=object(),
        model_id=None,
        quota=_quota(),
        budget=None,
        manifest_sink=None,
    )

    analysis = analyzer.analyze(request)

    assert analysis is not None
    plans = plan_queries(analysis, available_sources=("openalex", "arxiv", "crossref"))
    assert plans
    assert [item.source for item in plans] == ["openalex", "arxiv", "crossref"]
    assert "convolutional neural network" in plans[0].query
    assert "卷积神经网络" in analysis.original_phrase
    assert analysis.constraints.year_from is None


def test_model_intent_keeps_transformer_and_traffic_prediction_in_query() -> None:
    request = "请给我近三年用 Transformer 进行交通流预测的论文"
    gateway = _Gateway(
        _intent_output(
            {
                "concept": "Transformer",
                "source_phrase": "Transformer",
                "kind": "method",
                "english_search_terms": ["Transformer", "attention-based model"],
            },
            {
                "concept": "交通流预测",
                "source_phrase": "交通流预测",
                "kind": "application",
                "english_search_terms": ["traffic flow prediction", "traffic forecasting"],
            },
        )
    )
    analyzer = PaperResearchIntentAnalyzer(
        gateway,
        run_context=object(),
        model_id=None,
        quota=_quota(),
        budget=None,
        manifest_sink=None,
    )

    analysis = analyzer.analyze(request, now=datetime(2026, 10, 11, tzinfo=UTC))

    assert analysis is not None
    plan = plan_queries(analysis, available_sources=("openalex",))[0]
    assert "Transformer" in plan.query
    assert "traffic flow prediction" in plan.query
    assert analysis.constraints.year_from == 2023


def test_model_intent_preserves_method_and_application_and_generates_english_terms() -> None:
    request = "请给我几篇利用卷积神经网络解决小样本图像分类的论文"
    gateway = _Gateway(
        _intent_output(
            {
                "concept": "卷积神经网络",
                "source_phrase": "卷积神经网络",
                "kind": "method",
                "english_search_terms": ["convolutional neural network", "CNN"],
            },
            {
                "concept": "小样本图像分类",
                "source_phrase": "小样本图像分类",
                "kind": "application",
                "english_search_terms": [
                    "few-shot image classification",
                    "low-shot image classification",
                ],
            },
        )
    )
    analyzer = PaperResearchIntentAnalyzer(
        gateway,
        run_context=object(),
        model_id=None,
        quota=_quota(),
        budget=None,
        manifest_sink=None,
    )

    analysis = analyzer.analyze(request, now=datetime(2026, 10, 11, tzinfo=UTC))

    assert analysis is not None
    assert "卷积神经网络" in analysis.original_phrase
    assert "小样本图像分类" in analysis.original_phrase
    assert {item.kind for item in analysis.concepts} == {"method", "application"}
    assert "convolutional neural network" in analysis.final_query
    assert "few-shot image classification" in analysis.final_query
    assert analysis.constraints.year_from is None
    assert "小样本图像分类" in analysis.research_question
    sent = json.loads(gateway.payloads[0]["messages"][1]["content"])
    assert sent["request"] == request


def test_model_intent_preserves_method_and_application_for_bearing_diagnosis() -> None:
    request = "请给我几篇用深度学习做轴承故障诊断的论文"
    gateway = _Gateway(
        _intent_output(
            {
                "concept": "深度学习",
                "source_phrase": "深度学习",
                "kind": "method",
                "english_search_terms": ["deep learning"],
            },
            {
                "concept": "轴承故障诊断",
                "source_phrase": "轴承故障诊断",
                "kind": "application",
                "english_search_terms": ["bearing fault diagnosis"],
            },
        )
    )
    analyzer = PaperResearchIntentAnalyzer(
        gateway,
        run_context=object(),
        model_id=None,
        quota=_quota(),
        budget=None,
        manifest_sink=None,
    )

    analysis = analyzer.analyze(request)

    assert analysis is not None
    assert "deep learning" in analysis.final_query
    assert "bearing fault diagnosis" in analysis.final_query


def test_model_intent_keeps_explicit_year_and_source_as_hard_conditions() -> None:
    request = "请给我近三年用 Transformer 进行交通流预测的论文，只查 OpenAlex"
    gateway = _Gateway(
        _intent_output(
            {
                "concept": "Transformer",
                "source_phrase": "Transformer",
                "kind": "method",
                "english_search_terms": ["Transformer", "attention-based model"],
            },
            {
                "concept": "交通流预测",
                "source_phrase": "交通流预测",
                "kind": "application",
                "english_search_terms": ["traffic flow prediction"],
            },
        )
    )
    analyzer = PaperResearchIntentAnalyzer(
        gateway,
        run_context=object(),
        model_id=None,
        quota=_quota(),
        budget=None,
        manifest_sink=None,
    )

    analysis = analyzer.analyze(request, now=datetime(2026, 10, 11, tzinfo=UTC))

    assert analysis is not None
    assert analysis.constraints.year_from == 2023
    assert analysis.constraints.year_to is None
    assert analysis.constraints.allowed_sources == ["openalex"]
    assert any(
        item.kind == "year" and item.source_phrase == "近三年"
        for item in analysis.condition_sources
    )
    assert any(
        item.kind == "source" and item.source_phrase == "OpenAlex"
        for item in analysis.condition_sources
    )


def test_current_year_condition_does_not_inherit_old_phrase_as_its_evidence() -> None:
    request = "请给我2024年以后的 Transformer 交通预测论文"
    gateway = _Gateway(
        _intent_output(
            {
                "concept": "Transformer",
                "source_phrase": "Transformer",
                "kind": "method",
                "english_search_terms": ["Transformer", "attention-based model"],
            }
        )
    )
    analyzer = PaperResearchIntentAnalyzer(
        gateway,
        run_context=object(),
        model_id=None,
        quota=_quota(),
        budget=None,
        manifest_sink=None,
    )

    analysis = analyzer.analyze(
        request,
        task_conditions=[SimpleNamespace(kind="year", text="近三年")],
        now=datetime(2026, 10, 11, tzinfo=UTC),
    )

    assert analysis is not None
    year_sources = [
        item.source_phrase for item in analysis.condition_sources if item.kind == "year"
    ]
    assert year_sources == ["2024年以后"]


def test_planning_uses_domain_recommended_sources_without_filling_all_adapters() -> None:
    analysis = PaperTermAnalysis(
        original_phrase="图像分类",
        normalized_term="图像分类",
        confidence=1.0,
        concepts=[],
        recommended_sources=["crossref", "openalex"],
    )

    plans = plan_queries(analysis, available_sources=("arxiv", "openalex", "crossref"))

    assert [item.source for item in plans] == ["crossref", "openalex"]


def test_planning_replaces_a_recommended_source_excluded_by_user() -> None:
    analysis = PaperTermAnalysis(
        original_phrase="图像分类",
        normalized_term="图像分类",
        confidence=1.0,
        recommended_sources=["openalex", "arxiv"],
        constraints=PaperConstraints(excluded_sources=["openalex"]),
    )

    plans = plan_queries(analysis, available_sources=("openalex", "arxiv", "crossref"))

    assert [item.source for item in plans] == ["arxiv", "crossref"]


def test_first_intent_failure_stops_before_any_paper_search() -> None:
    class Source:
        queries: list[str] = []

        def search(self, _account_id: str, query: str, **_kwargs: Any) -> CandidateSearchOutcome:
            self.queries.append(query)
            return CandidateSearchOutcome(
                query=query,
                candidates=[],
                record=ModuleQueryRecord(
                    source="arxiv",
                    query=query,
                    status=ModuleQueryStatus.EMPTY,
                ),
            )

    source = Source()

    class FailedIntent:
        def analyze(self, *_args: Any, **_kwargs: Any) -> None:
            return None

    flow = PaperNodeFlow(
        source=source,
        enricher=None,
        intent_analyzer=FailedIntent(),
    )
    inputs = RecipeInputs(
        account_id="account",
        conversation_id="conversation",
        run_id="run",
        user_message_id="message",
        user_content="请给我几篇与卷积神经网络有关的论文",
        task_id=None,
        task_version=None,
        wait_identity=None,
        artifacts={},
    )
    spec = next(item for item in build_paper_recipe().nodes if item.name == NODE_PARSE)
    result = flow.run_node(
        NodeInvocation(spec=spec, inputs=inputs, dependencies={}, remaining_budget_ms=1000)
    )

    assert result.status.value == "failed"
    assert result.failure is not None
    assert result.failure["code"] == "paper_intent_analysis_failed"
    assert source.queries == []


def test_first_round_searches_available_sources_in_parallel() -> None:
    rendezvous = threading.Barrier(2)

    class Source:
        def __init__(self, name: str) -> None:
            self.source = name

        def search(
            self,
            _account_id: str,
            query: str,
            *,
            max_results: int,
            stop_event: Any = None,
            deadline: float | None = None,
            year_from: int | None = None,
            year_to: int | None = None,
        ) -> CandidateSearchOutcome:
            del max_results, stop_event, deadline, year_from, year_to
            rendezvous.wait(timeout=1.5)
            return CandidateSearchOutcome(
                query=query,
                candidates=[],
                record=ModuleQueryRecord(
                    source=self.source,
                    query=query,
                    status=ModuleQueryStatus.EMPTY,
                ),
            )

    sources = {name: Source(name) for name in ("arxiv", "openalex")}
    flow = PaperNodeFlow(
        source=sources["arxiv"], sources=sources, enricher=None, intent_analyzer=None
    )
    analysis = PaperTermAnalysis(
        original_phrase="CNN",
        normalized_term="CNN",
        final_query="CNN",
        confidence=1.0,
    )
    plans = [
        PaperQueryPlan(
            query="CNN",
            source=name,
            sort_by="relevance",
            sort_order="descending",
            max_results=8,
            target_count=5,
            rationale="并行来源回归",
        )
        for name in sources
    ]
    inputs = RecipeInputs(
        account_id="account",
        conversation_id="conversation",
        run_id="run",
        user_message_id="message",
        user_content="请找 CNN 论文",
        task_id=None,
        task_version=None,
        wait_identity=None,
        artifacts={
            NODE_PARSE: SimpleNamespace(
                content_hash="parse-hash", payload={"analysis": analysis.model_dump(mode="json")}
            ),
            NODE_PLAN: SimpleNamespace(
                content_hash="plan-hash",
                payload={"plans": [item.model_dump(mode="json") for item in plans]},
            ),
        },
    )
    dependencies = {
        NODE_PARSE: SimpleNamespace(
            artifact_id="parse-artifact",
            content_hash="parse-hash",
            payload={"analysis": analysis.model_dump(mode="json")},
        ),
        NODE_PLAN: SimpleNamespace(
            artifact_id="plan-artifact",
            content_hash="plan-hash",
            payload={"plans": [item.model_dump(mode="json") for item in plans], "target_count": 5},
        ),
    }
    spec = next(item for item in build_paper_recipe().nodes if item.name == NODE_SEARCH)

    result = flow.run_node(
        NodeInvocation(
            spec=spec, inputs=inputs, dependencies=dependencies, remaining_budget_ms=5000
        )
    )

    assert [item["status"] for item in result.artifact.payload["records"]] == ["empty", "empty"]
