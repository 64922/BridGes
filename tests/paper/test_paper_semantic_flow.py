"""Issue 01：补搜、缓存与节点级语义失败的行为合同。"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

from bridges.ai.model_quota import QuotaVerificationBasis, RunModelQuota
from bridges.contracts.ai import ModelCallResult, ModelCallStatus
from bridges.contracts.modules import ModuleQueryRecord, ModuleQueryStatus
from bridges.kernel.contracts import NodeInvocation, RecipeInputs
from bridges.paper.assessment import PaperEvidenceRole
from bridges.paper.candidate_codec import candidate_payload
from bridges.paper.contracts import PaperQueryPlan, PaperTermAnalysis
from bridges.paper.intent import PaperResearchIntentAnalyzer
from bridges.paper.kernel import (
    NODE_ENRICH,
    NODE_EVALUATE,
    NODE_PARSE,
    NODE_PLAN,
    NODE_READ,
    NODE_SCREEN,
    NODE_SEARCH,
    NODE_VERIFY,
    PaperNodeFlow,
    build_paper_recipe,
)
from bridges.paper.source_types import CandidateSearchOutcome
from bridges.paper.sources import PaperCandidate
from tests.paper.test_paper_module_flow import _FakePaperSource
from tests.paper.test_paper_semantic_contract import (
    _analysis,
    _candidate,
    _Judge,
    _valid_evidence,
)


def test_followup_candidates_and_existing_cache_share_the_same_mapping() -> None:
    class Planner:
        def __init__(self) -> None:
            self.feedback: list[dict[str, Any]] = []

        def analyze(self, _request: str, **_kwargs: Any) -> Any:
            return _analysis()

        def plan(
            self, _analysis: Any, *, available_sources: Any, feedback: Any = None
        ) -> Any:
            if feedback:
                self.feedback.append(feedback)
            query = (
                "followup machine learning bearing fault diagnosis"
                if feedback
                else "machine learning bearing fault diagnosis"
            )
            return tuple(
                PaperQueryPlan(
                    query=query,
                    source=source,
                    sort_by="relevance",
                    sort_order="descending",
                    max_results=10,
                    target_count=8,
                    rationale="测试补搜与缓存共用映射",
                )
                for source in available_sources
            )

    class Source:
        source = "openalex"

        def __init__(self) -> None:
            self.queries: list[str] = []

        def search(self, _account: str, query: str, **_kwargs: Any) -> Any:
            self.queries.append(query)
            candidate = (
                _candidate(title="Machine learning for bearing fault diagnosis")
                if len(self.queries) == 1
                else _candidate(
                    identifier="2401.00002",
                    title="Machine learning for bearing fault diagnosis II",
                )
            )
            return CandidateSearchOutcome(
                query,
                [candidate],
                ModuleQueryRecord(
                    source=self.source, query=query, status=ModuleQueryStatus.SUCCESS
                ),
            )

    judge = _Judge({"relevant": True, "evidence": _valid_evidence()})
    planner = Planner()
    source = Source()
    flow = PaperNodeFlow(
        source=source,
        sources={"openalex": source},
        intent_analyzer=planner,
        enricher=None,
        judge=judge,
    )
    artifacts: dict[str, Any] = {}
    inputs = RecipeInputs(
        account_id="account",
        conversation_id="conversation",
        run_id="run",
        user_message_id="message",
        user_content="请给几篇机器学习做轴承故障诊断的论文",
        task_id="task",
        task_version=2,
        wait_identity=None,
        artifacts={},
    )
    for name in (NODE_PARSE, NODE_PLAN, NODE_SEARCH, NODE_SCREEN):
        spec = next(item for item in build_paper_recipe().nodes if item.name == name)
        result = flow.run_node(
            NodeInvocation(
                spec=spec,
                inputs=replace(inputs, artifacts=artifacts),
                dependencies=artifacts,
                remaining_budget_ms=10000,
            )
        )
        assert result.failure is None
        artifacts[name] = result.artifact

    assert len(source.queries) == 2
    assert planner.feedback
    assert len(judge.calls) == 2, "补搜后只评估新增候选，已有裁决必须复用"
    assert judge.calls[0] == [
        artifacts[NODE_SEARCH].payload["candidates"][0]["candidate_id"]
    ]
    assert len(judge.calls[1]) == 1
    screened = artifacts[NODE_SCREEN].payload["screened"]
    assert len(screened) == 2
    assert all(
        row["matched_requirements"] == ["Machine Learning", "Fault Diagnosis"]
        for row in screened
    )
    assert artifacts[NODE_PARSE].payload["analysis"]["concepts"][0]["concept_id"] == "c1"


def _screen_node(analysis: PaperTermAnalysis, candidate: PaperCandidate, judge: Any) -> Any:
    flow = PaperNodeFlow(source=SimpleNamespace(), enricher=None, judge=judge)
    artifacts: dict[str, Any] = {
        NODE_PARSE: SimpleNamespace(
            artifact_id="parse",
            content_hash="parse",
            payload={"analysis": analysis.model_dump(mode="json")},
        ),
        NODE_SEARCH: SimpleNamespace(
            artifact_id="search",
            content_hash="search",
            payload={
                "candidates": [candidate_payload(candidate)],
                "target_count": 8,
                "records": [
                    ModuleQueryRecord(
                        source="openalex",
                        query="machine learning bearing fault diagnosis",
                        status=ModuleQueryStatus.SUCCESS,
                    ).model_dump(mode="json")
                ],
            },
        ),
    }
    inputs = RecipeInputs(
        account_id="account",
        conversation_id="conversation",
        run_id="run",
        user_message_id="message",
        user_content="请找论文",
        task_id=None,
        task_version=None,
        wait_identity=None,
        artifacts=artifacts,
    )
    spec = next(item for item in build_paper_recipe().nodes if item.name == NODE_SCREEN)
    return flow.run_node(
        NodeInvocation(
            spec=spec,
            inputs=inputs,
            dependencies=artifacts,
            remaining_budget_ms=10000,
        )
    )


def test_all_invalid_evidence_fails_honestly_with_reason_counts() -> None:
    judge = _Judge(
        {
            "relevant": True,
            "evidence": [
                {"requirement": "c1", "source": "title", "quote": "fabricated quote"}
            ],
        }
    )
    result = _screen_node(_analysis(), _candidate(), judge)

    assert result.failure is not None
    assert result.failure["code"] == "paper_semantic_evaluation_unavailable"
    assert result.failure["retryable"] is True
    assert result.artifact.payload["screened"] == []
    assert result.artifact.payload["semantic_rejection_counts"] == {
        "semantic_quote_not_found": 1
    }
    assert result.artifact.payload["excluded"][0]["reason"] == "semantic_quote_not_found"


def test_user_request_reaches_verified_recommendation_with_stable_identifiers() -> None:
    class Gateway:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def invoke(
            self, _capability: str, _version: str, _context: Any, payload: dict[str, Any],
            **_kwargs: Any,
        ) -> Any:
            properties = set((payload.get("json_schema") or {}).get("properties") or {})
            if "concepts" in properties:
                self.calls.append("intent")
                output: dict[str, Any] = {
                    "research_object": "轴承故障诊断",
                    "research_question": "机器学习如何用于轴承故障诊断",
                    "methods": ["机器学习"],
                    "applications": ["轴承故障诊断"],
                    "reading_purpose": "主题探索",
                    "reading_purpose_quote": "",
                    "concepts": [
                        {
                            "concept": "机器学习",
                            "source_phrase": "机器学习",
                            "kind": "method",
                            "english_search_terms": ["machine learning"],
                        },
                        {
                            "concept": "轴承故障诊断",
                            "source_phrase": "轴承故障诊断",
                            "kind": "application",
                            "english_search_terms": ["bearing fault diagnosis"],
                        },
                    ],
                    "recommended_sources": ["arxiv", "openalex"],
                    "clarification_question": "",
                    "clarification_missing": "",
                }
            elif "queries" in properties:
                self.calls.append("plan")
                output = {
                    "queries": [
                        {
                            "source": "arxiv",
                            "query": 'all:"machine learning" AND '
                            'all:"bearing fault diagnosis"',
                        }
                    ]
                }
            elif "matches" in properties:
                self.calls.append("judge")
                output = {
                    "matches": [
                        {
                            "candidate_id": "2401.00001",
                            "relevant": True,
                            "evidence": _valid_evidence(),
                        }
                    ]
                }
            else:
                raise AssertionError(f"unexpected schema properties: {properties}")
            return ModelCallResult(status=ModelCallStatus.SUCCESS, output=output)

    gateway = Gateway()
    quota = RunModelQuota(
        model_id="test-model",
        context_window=32768,
        verification_basis=QuotaVerificationBasis.FACTORY_MATRIX,
    )
    analyzer = PaperResearchIntentAnalyzer(
        gateway,
        run_context=object(),
        model_id=None,
        quota=quota,
        budget=None,
        manifest_sink=None,
    )
    judge = PaperEvidenceRole(
        gateway,
        run_context=object(),
        model_id=None,
        quota=quota,
        budget=None,  # type: ignore[arg-type]
        manifest_sink=None,
    )
    source = _FakePaperSource(candidates=[_candidate()])
    flow = PaperNodeFlow(
        source=source, enricher=None, intent_analyzer=analyzer, judge=judge
    )
    artifacts: dict[str, Any] = {}
    inputs = RecipeInputs(
        account_id="account",
        conversation_id="conversation",
        run_id="run",
        user_message_id="message",
        user_content="请给我几篇机器学习做轴承故障诊断的论文",
        task_id=None,
        task_version=None,
        wait_identity=None,
        artifacts={},
    )
    for name in (
        NODE_PARSE,
        NODE_PLAN,
        NODE_SEARCH,
        NODE_SCREEN,
        NODE_READ,
        NODE_ENRICH,
        NODE_EVALUATE,
        NODE_VERIFY,
    ):
        spec = next(item for item in build_paper_recipe().nodes if item.name == name)
        result = flow.run_node(
            NodeInvocation(
                spec=spec,
                inputs=replace(inputs, artifacts=artifacts),
                dependencies=artifacts,
                remaining_budget_ms=10000,
            )
        )
        assert result.failure is None
        artifacts[name] = result.artifact

    assert gateway.calls[:3] == ["intent", "plan", "judge"]
    assert gateway.calls.count("judge") == 1, "语义核验只调用一次，不因补搜重复计费"
    concepts = artifacts[NODE_PARSE].payload["analysis"]["concepts"]
    assert [item["concept_id"] for item in concepts] == ["c1", "c2"]
    evaluate = artifacts[NODE_EVALUATE].payload
    assert evaluate["outcome"] == "success"
    assert evaluate["recommendations"]
    recommendation = evaluate["recommendations"][0]
    assert {item["requirement"] for item in recommendation["match_evidence"]} == {
        "机器学习",
        "轴承故障诊断",
    }
    assert all(
        item["quote"] in f"{_candidate().title} {_candidate().abstract}"
        for item in recommendation["match_evidence"]
    )
