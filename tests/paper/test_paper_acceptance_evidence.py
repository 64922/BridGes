"""独立验收：语义评估失效及引用量不得导致未经核验的结论。"""

from datetime import UTC, datetime
from typing import Any

import pytest

from bridges.paper.contracts import PaperResearchConcept, PaperTermAnalysis
from bridges.paper.planning import plan_queries
from bridges.paper.query_privacy import is_public_search_term
from bridges.paper.ranking import rank_candidates
from bridges.paper.screening import screen_candidates
from bridges.paper.sources import EnrichedMetadata, PaperCandidate


def _analysis() -> PaperTermAnalysis:
    return PaperTermAnalysis(
        original_phrase="CNN",
        normalized_term="cnn",
        final_query="CNN",
        confidence=1,
        concepts=[
            PaperResearchConcept(
                concept="CNN", source_phrase="CNN", kind="method", english_search_terms=["CNN"]
            )
        ],
    )


def _candidate(identifier: str, year: int) -> PaperCandidate:
    return PaperCandidate(
        arxiv_id=identifier,
        title="CNN methods",
        authors=[],
        published_at=datetime(year, 1, 1, tzinfo=UTC),
        abs_url="",
        pdf_url="",
        abstract="CNN methods for classification.",
    )


@pytest.mark.parametrize(
    "result",
    [
        {},
        {"relevant": True, "evidence": []},
        {
            "relevant": True,
            "evidence": [
                {"requirement": "CNN", "source": "abstract", "quote": "Invented evidence"}
            ],
        },
    ],
)
def test_semantic_failure_cannot_pass_as_verified_recommendation(result: dict[str, Any]) -> None:
    candidate = _candidate("2401.00001", 2024)

    class Judge:
        def judge_many(self, *_args: Any) -> dict[str, dict[str, Any]]:
            return {candidate.candidate_id: result} if result else {}

    outcome = screen_candidates(_analysis(), [candidate], judge=Judge())
    assert outcome.screened == []
    assert outcome.excluded[0].reason.startswith("semantic_")
    assert "未完成语义" in "".join(outcome.notes)


def test_old_or_highly_cited_paper_is_not_asserted_as_foundation() -> None:
    analysis = _analysis()
    old, recent = _candidate("1801.00001", 2018), _candidate("2401.00001", 2024)
    outcome = rank_candidates(
        analysis,
        plan_queries(analysis)[0],
        [old, recent],
        {old.arxiv_id: EnrichedMetadata(source="openalex", cited_by_count=10000)},
    )
    assert len(outcome.recommendations) == 2
    assert all(item.role != "foundation" for item in outcome.recommendations)


@pytest.mark.parametrize(
    "value",
    ["CNN alice@example.com", "CNN 13812345678", "CNN api_key=private-value", "CNN name:Alice"],
)
def test_sensitive_model_search_terms_fail_local_privacy_gate(value: str) -> None:
    assert not is_public_search_term(value)


def test_academic_terms_are_not_mistaken_for_credentials() -> None:
    assert is_public_search_term("private information retrieval token classification")
