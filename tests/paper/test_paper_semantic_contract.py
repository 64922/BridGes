"""Issue 01：稳定概念标识与可区分语义证据拒绝原因。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from bridges.ai.model_quota import QuotaVerificationBasis, RunModelQuota
from bridges.contracts.ai import ModelCallResult, ModelCallStatus
from bridges.paper.assessment import PaperEvidenceRole
from bridges.paper.contracts import PaperResearchConcept, PaperTermAnalysis
from bridges.paper.intent import PaperResearchIntentAnalyzer
from bridges.paper.relevance_cache import NodeRelevanceCache
from bridges.paper.screening import screen_candidates
from bridges.paper.sources import PaperCandidate


def _quota() -> RunModelQuota:
    return RunModelQuota(
        model_id="test-model",
        context_window=32768,
        verification_basis=QuotaVerificationBasis.FACTORY_MATRIX,
    )


def _analysis(
    *, first_name: str = "Machine Learning", second_name: str = "Fault Diagnosis"
) -> PaperTermAnalysis:
    return PaperTermAnalysis(
        original_phrase="机器学习、轴承故障诊断",
        normalized_term="机器学习、轴承故障诊断",
        confidence=1,
        concepts=[
            PaperResearchConcept(
                concept_id="c1",
                concept=first_name,
                source_phrase="机器学习",
                kind="method",
                english_search_terms=["machine learning"],
            ),
            PaperResearchConcept(
                concept_id="c2",
                concept=second_name,
                source_phrase="轴承故障诊断",
                kind="application",
                english_search_terms=["bearing fault diagnosis"],
            ),
        ],
    )


def _candidate(
    *, identifier: str = "2401.00001", title: str = "Machine learning for bearing fault diagnosis"
) -> PaperCandidate:
    return PaperCandidate(
        arxiv_id=identifier,
        title=title,
        authors=[],
        published_at=datetime(2024, 1, 1, tzinfo=UTC),
        abs_url=f"https://arxiv.org/abs/{identifier}",
        pdf_url="",
        abstract="We apply machine learning to bearing fault diagnosis.",
    )


def _valid_evidence() -> list[dict[str, str]]:
    return [
        {"requirement": "c1", "source": "title", "quote": "Machine learning"},
        {"requirement": "c2", "source": "abstract", "quote": "bearing fault diagnosis"},
    ]


class _Judge:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[list[str]] = []

    def judge_many(self, _analysis: Any, candidates: Any) -> Any:
        self.calls.append([item.candidate_id for item in candidates])
        return {item.candidate_id: self.result for item in candidates}


@pytest.mark.parametrize(
    "first_name,second_name",
    [
        ("Machine Learning", "Fault Diagnosis"),
        ("machine learning", "fault diagnosis"),
        ("机器学习", "轴承故障诊断"),
        ("机器学习方法", "轴承故障识别"),
    ],
)
def test_valid_evidence_passes_regardless_of_display_name(
    first_name: str, second_name: str
) -> None:
    outcome = screen_candidates(
        _analysis(first_name=first_name, second_name=second_name),
        [_candidate()],
        judge=_Judge({"relevant": True, "evidence": _valid_evidence()}),
    )

    assert len(outcome.screened) == 1
    entry = outcome.screened[0]
    assert entry.matched_requirements == (first_name, second_name)
    assert entry.evidence[0]["quote"] in _candidate().title
    assert entry.evidence[1]["quote"] in _candidate().abstract
    assert outcome.semantic_rejection_counts == {}


@pytest.mark.parametrize(
    "result,reason",
    [
        ({}, "semantic_evaluation_unavailable"),
        ({"evidence": _valid_evidence()}, "semantic_evaluation_unavailable"),
        ({"relevant": True}, "semantic_evidence_missing"),
        ({"relevant": True, "evidence": []}, "semantic_evidence_missing"),
        (
            {"relevant": True, "evidence": "not-a-list"},
            "semantic_evidence_structure_invalid",
        ),
        (
            {
                "relevant": True,
                "evidence": [
                    {
                        "requirement": "machine learning",
                        "source": "title",
                        "quote": "Machine learning",
                    }
                ],
            },
            "semantic_requirement_unknown",
        ),
        (
            {
                "relevant": True,
                "evidence": [
                    {"requirement": "c1", "source": "full_text", "quote": "Machine learning"}
                ],
            },
            "semantic_source_invalid",
        ),
        (
            {
                "relevant": True,
                "evidence": [
                    {"requirement": "c1", "source": "title", "quote": "fabricated quote"}
                ],
            },
            "semantic_quote_not_found",
        ),
    ],
)
def test_invalid_semantic_evidence_is_rejected_with_distinct_reason(
    result: dict[str, Any], reason: str
) -> None:
    outcome = screen_candidates(_analysis(), [_candidate()], judge=_Judge(result))

    assert outcome.screened == []
    assert outcome.excluded[0].reason == reason
    assert outcome.semantic_rejection_counts[reason] >= 1


def test_explicit_irrelevant_verdict_is_not_semantic_failure() -> None:
    outcome = screen_candidates(
        _analysis(),
        [_candidate()],
        judge=_Judge({"relevant": False, "evidence": _valid_evidence()}),
    )

    assert outcome.screened == []
    assert outcome.excluded[0].reason == "judge_not_relevant"
    assert outcome.semantic_rejection_counts == {}


def test_valid_evidence_is_kept_when_other_evidence_entries_are_invalid() -> None:
    evidence = [
        *_valid_evidence()[:1],
        {"requirement": "machine learning", "source": "title", "quote": "Machine learning"},
        {"requirement": "c2", "source": "abstract", "quote": "fabricated quote"},
    ]
    outcome = screen_candidates(
        _analysis(), [_candidate()], judge=_Judge({"relevant": True, "evidence": evidence})
    )

    assert len(outcome.screened) == 1
    entry = outcome.screened[0]
    assert entry.matched_requirements == ("Machine Learning",)
    assert entry.evidence == (
        {"requirement": "Machine Learning", "source": "title", "quote": "Machine learning"},
    )
    assert outcome.semantic_rejection_counts == {
        "semantic_requirement_unknown": 1,
        "semantic_quote_not_found": 1,
    }


def test_valid_candidate_is_delivered_even_when_another_candidate_fails() -> None:
    first, second = _candidate(), _candidate(identifier="2401.00002")

    class PartialJudge:
        def judge_many(self, _analysis: Any, candidates: Any) -> Any:
            return {
                item.candidate_id: (
                    {"relevant": True, "evidence": _valid_evidence()}
                    if item is first
                    else {"relevant": True, "evidence": []}
                )
                for item in candidates
            }

    outcome = screen_candidates(_analysis(), [first, second], judge=PartialJudge())

    assert [item.candidate.candidate_id for item in outcome.screened] == [first.candidate_id]
    assert outcome.excluded[0].candidate.candidate_id == second.candidate_id
    assert outcome.excluded[0].reason == "semantic_evidence_missing"
    assert "1 篇候选未完成语义" in "".join(outcome.notes)


def test_code_assigns_unique_concept_ids_and_ignores_model_supplied_ids() -> None:
    class Gateway:
        def invoke(self, *_args: Any, **_kwargs: Any) -> Any:
            return SimpleNamespace(
                status=SimpleNamespace(value="success"),
                output={
                    "research_object": "图像分类模型",
                    "research_question": "卷积神经网络如何用于小样本图像分类",
                    "methods": ["卷积神经网络"],
                    "applications": ["小样本图像分类"],
                    "reading_purpose": "主题探索",
                    "reading_purpose_quote": "",
                    "concepts": [
                        {
                            "concept_id": "model-invented-c1",
                            "concept": "卷积神经网络",
                            "source_phrase": "卷积神经网络",
                            "kind": "method",
                            "english_search_terms": ["convolutional neural network"],
                        },
                        {
                            "concept_id": "model-invented-c2",
                            "concept": "小样本图像分类",
                            "source_phrase": "小样本图像分类",
                            "kind": "application",
                            "english_search_terms": ["few-shot image classification"],
                        },
                    ],
                    "recommended_sources": ["openalex", "arxiv"],
                    "clarification_question": "",
                    "clarification_missing": "",
                },
                lock=None,
            )

    analyzer = PaperResearchIntentAnalyzer(
        Gateway(),
        run_context=object(),
        model_id=None,
        quota=_quota(),
        budget=None,
        manifest_sink=None,
    )
    analysis = analyzer.analyze("请给我几篇利用卷积神经网络解决小样本图像分类的论文")

    assert analysis is not None
    assert [item.concept_id for item in analysis.concepts] == ["c1", "c2"]


def test_judge_request_passes_identifier_mapping_and_legal_enum() -> None:
    class Gateway:
        def __init__(self) -> None:
            self.payloads: list[dict[str, Any]] = []

        def invoke(
            self, _capability: str, _version: str, _context: Any, payload: dict[str, Any],
            **_kwargs: Any,
        ) -> Any:
            self.payloads.append(payload)
            return ModelCallResult(
                status=ModelCallStatus.SUCCESS,
                output={
                    "matches": [
                        {
                            "candidate_id": "2401.00001",
                            "relevant": True,
                            "evidence": _valid_evidence(),
                        }
                    ]
                },
            )

    gateway = Gateway()
    role = PaperEvidenceRole(
        gateway,
        run_context=object(),
        model_id=None,
        quota=_quota(),
        budget=None,  # type: ignore[arg-type]
        manifest_sink=None,
    )

    result = role.judge_many(_analysis(), [_candidate()])

    payload = gateway.payloads[0]
    sent = json.loads(payload["messages"][1]["content"])
    assert sent["request"]["requirements"] == [
        {
            "id": "c1",
            "name": "Machine Learning",
            "source_phrase": "机器学习",
            "kind": "method",
        },
        {
            "id": "c2",
            "name": "Fault Diagnosis",
            "source_phrase": "轴承故障诊断",
            "kind": "application",
        },
    ]
    evidence_schema = payload["json_schema"]["properties"]["matches"]["items"][
        "properties"
    ]["evidence"]["items"]["properties"]
    assert evidence_schema["requirement"]["enum"] == ["c1", "c2"]
    assert evidence_schema["source"]["enum"] == ["title", "abstract"]
    assert result == {
        "2401.00001": {
            "candidate_id": "2401.00001",
            "relevant": True,
            "evidence": _valid_evidence(),
        }
    }


def test_cache_rejudges_only_when_requirements_change_not_display_name_alone() -> None:
    judge = _Judge({"relevant": True, "evidence": _valid_evidence()})
    cache = NodeRelevanceCache(judge)
    candidate = _candidate()
    cache.judge_many(_analysis(), [candidate])
    cache.judge_many(_analysis(first_name="机器学习"), [candidate])
    cache.judge_many(_analysis(first_name="机器学习方法"), [candidate])
    changed = _analysis()
    changed.concepts[1].source_phrase = "轴承故障识别"
    cache.judge_many(changed, [candidate])

    assert len(judge.calls) == 2
