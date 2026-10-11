"""论文结果展示保留阅读范围与未知元数据边界。"""

from __future__ import annotations

from datetime import UTC, datetime

from bridges.paper.contracts import (
    PaperConstraints,
    PaperQueryPlan,
    PaperReadScope,
    PaperRecommendation,
    PaperSearchProjection,
    PaperSearchStatus,
    PaperTermAnalysis,
)
from bridges.paper.presenting import render_result_content
from bridges.paper.ranking import rank_candidates
from bridges.paper.screening import _within_year_range
from bridges.paper.sources import PaperCandidate


def _analysis(*, constraints: PaperConstraints | None = None) -> PaperTermAnalysis:
    return PaperTermAnalysis(
        original_phrase="卷积神经网络",
        normalized_term="卷积神经网络",
        final_query="convolutional neural network",
        research_question="卷积神经网络的研究进展",
        confidence=1.0,
        constraints=constraints or PaperConstraints(),
    )


def _plan() -> PaperQueryPlan:
    return PaperQueryPlan(
        query="convolutional neural network",
        source="crossref",
        sort_by="relevance",
        sort_order="descending",
        max_results=8,
        target_count=5,
        rationale="动态意图查询",
    )


def _undated_candidate() -> PaperCandidate:
    return PaperCandidate(
        arxiv_id=None,
        title="Convolutional neural network paper",
        authors=[],
        published_at=datetime(1, 1, 1, tzinfo=UTC),
        abs_url="https://doi.org/10.5555/undated",
        pdf_url="",
        abstract="A paper about convolutional neural networks.",
        source="crossref",
        source_id="10.5555/undated",
        doi="10.5555/undated",
    )


def test_unknown_year_does_not_satisfy_an_explicit_year_filter() -> None:
    analysis = _analysis(constraints=PaperConstraints(year_to=2025))

    assert not _within_year_range(analysis, _undated_candidate())


def test_ranking_keeps_unknown_year_as_related_and_reports_it() -> None:
    result = rank_candidates(
        _analysis(),
        _plan(),
        [_undated_candidate()],
        {},
    )

    assert len(result.recommendations) == 1
    recommendation = result.recommendations[0]
    assert recommendation.published_year is None
    assert recommendation.role == "related"
    assert "年份未知" in recommendation.reason_zh


def test_result_content_separates_full_text_link_from_actual_read_scope() -> None:
    paper = PaperRecommendation(
        order=1,
        candidate_id="doi:10.5555/example",
        title="A CNN paper",
        source="openalex",
        abs_url="https://doi.org/10.5555/example",
        full_text_url="https://example.org/paper.pdf",
        full_text_available=True,
        read_scope=PaperReadScope.ABSTRACT,
        role="recent",
        reason_zh="与研究问题直接相关。",
        match_basis="摘要匹配卷积神经网络。",
    )
    projection = PaperSearchProjection(
        status=PaperSearchStatus.SUCCESS,
        selected_sources=["openalex", "crossref"],
        papers=[paper],
    )

    content = render_result_content(_analysis(), _plan(), projection)

    assert "检索 OpenAlex、Crossref" in content
    assert "https://example.org/paper.pdf" in content
    assert "本轮只读取标题与摘要" in content
    assert "本轮读取了全文" not in content


def test_result_content_labels_unknown_year_without_inventing_a_date() -> None:
    paper = PaperRecommendation(
        order=1,
        candidate_id="doi:10.5555/undated",
        title="An undated CNN paper",
        source="crossref",
        abs_url="https://doi.org/10.5555/undated",
        published_year=None,
        full_text_available=False,
        read_scope=PaperReadScope.ABSTRACT,
        role="related",
        reason_zh="年份未知，相关研究",
        match_basis="摘要匹配卷积神经网络。",
    )
    projection = PaperSearchProjection(
        status=PaperSearchStatus.SUCCESS,
        selected_sources=["crossref"],
        papers=[paper],
    )

    content = render_result_content(_analysis(), _plan(), projection)

    assert "年份未知，相关研究" in content
    assert "（None，" not in content
