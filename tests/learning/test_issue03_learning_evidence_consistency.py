"""Issue 03：学习证据覆盖裁决的确定性测试矩阵。"""

from datetime import UTC, datetime

import pytest

from bridges.contracts.teaching import (
    TeachingCardStatus,
    TeachingEvidenceStatus,
)
from bridges.learning.teaching_gate import TeachingTurnService
from bridges.web_search.contracts import (
    WebSearchProjection,
    WebSearchResult,
    WebSearchStatus,
    WebSearchVerification,
)


NOW = datetime(2026, 8, 12, tzinfo=UTC)


def _result(
    result_id: str,
    title: str,
    *,
    snippet: str,
    content: str = "",
    verification: WebSearchVerification = WebSearchVerification.VERIFIED,
    fetched: bool = True,
) -> WebSearchResult:
    return WebSearchResult(
        result_id=result_id,
        title=title,
        site="example.com",
        url=f"https://example.com/{result_id}",
        snippet=snippet,
        accessed_at=NOW,
        fetched_at=NOW if fetched else None,
        content_summary=content,
        verification=verification,
    )


def _web(*results: WebSearchResult) -> WebSearchProjection:
    return _web_with_query("Transformer 架构", *results)


def _web_with_query(
    query_summary: str, *results: WebSearchResult
) -> WebSearchProjection:
    return WebSearchProjection(
        status=WebSearchStatus.SUCCESS,
        trigger_reason="本地材料不足",
        query_summary=query_summary,
        results=list(results),
        searched_at=NOW,
    )


def test_cnn_goal_accepts_chinese_source_despite_a_truncated_query_summary() -> None:
    turn = TeachingTurnService().prepare(
        "学习“卷积神经网络的相关基础知识”并理解其核心机制",
        retrieval=None,
        web_search=_web_with_query(
            "topic:核心机制",
            _result(
                "web-cnn",
                "卷积神经网络（CNN）基础知识入门",
                snippet="卷积神经网络是常见的神经网络架构。",
                content="卷积神经网络通过局部感受野提取特征，构成其核心机制。",
            ),
        ),
        goal="学习“卷积神经网络的相关基础知识”并理解其核心机制",
    )

    assert turn.can_answer_reliably is True
    assert turn.status == TeachingCardStatus.READY
    assert turn.evidence_gate.status == TeachingEvidenceStatus.SUFFICIENT
    assert [source.source_id for source in turn.evidence_gate.external_sources] == [
        "web-cnn"
    ]
    assert turn.evidence_gate.coverage is not None
    assert turn.evidence_gate.coverage.rules_version == "learning-evidence-coverage-v2"
    assert turn.evidence_gate.coverage.topic_aliases_version == (
        "learning-evidence-topic-aliases-v1"
    )


def test_transformer_learning_accepts_only_fetched_sources_covering_core_dimensions() -> None:
    turn = TeachingTurnService().prepare(
        "学习Transformer架构的相关知识",
        retrieval=None,
        web_search=_web(
            _result(
                "web-ai",
                "AI Transformer 架构与自注意力",
                snippet="Transformer 架构使用自注意力机制处理序列。",
                content=(
                    "Transformer 是用于人工智能模型的神经网络架构，"
                    "由编码器、解码器和自注意力机制组成。"
                ),
            ),
            _result(
                "web-power",
                "电力变压器工作原理",
                snippet="电力系统中的变压器用于改变电压。",
                content="电力变压器通过电磁感应改变交流电压。",
            ),
        ),
    )

    assert turn.can_answer_reliably is True
    assert turn.status == TeachingCardStatus.READY
    assert [source.source_id for source in turn.evidence_gate.external_sources] == [
        "web-ai"
    ]
    assert "web-power" not in {
        source.source_id for source in turn.evidence_gate.external_sources
    }
    assert turn.evidence_gate.coverage is not None
    assert turn.evidence_gate.coverage.candidate_count == 2
    assert turn.evidence_gate.coverage.fetched_count == 2
    assert turn.evidence_gate.coverage.accepted_count == 1
    assert turn.evidence_gate.coverage.rejection_counts == {"topic_mismatch": 1}
    assert turn.gap_response is None


def test_search_success_without_target_coverage_stays_visible_but_blocks_teaching() -> None:
    turn = TeachingTurnService().prepare(
        "学习Transformer架构的相关知识",
        retrieval=None,
        web_search=_web(
            _result(
                "web-partial",
                "Transformer 架构简介",
                snippet="介绍 Transformer 的基本结构。",
                content="Transformer architecture uses stacked layers.",
            )
        ),
    )

    assert turn.can_answer_reliably is False
    assert turn.status == TeachingCardStatus.EMPTY
    assert turn.evidence_gate.status == TeachingEvidenceStatus.INSUFFICIENT
    assert turn.evidence_gate.external_sources == []
    assert turn.evidence_gate.search_status == TeachingCardStatus.EMPTY
    assert "已搜索但未覆盖本轮目标" in turn.evidence_gate.reason
    assert turn.can_retry is True
    assert turn.evidence_gate.coverage is not None
    assert turn.evidence_gate.coverage.rejection_counts == {"coverage_incomplete": 1}


def test_conflicting_public_source_never_combines_with_an_accepted_source() -> None:
    turn = TeachingTurnService().prepare(
        "学习Transformer架构的相关知识",
        retrieval=None,
        web_search=_web(
            _result(
                "web-ai",
                "AI Transformer 架构与自注意力",
                snippet="Transformer 架构使用自注意力机制处理序列。",
                content="Transformer 是人工智能模型的神经网络架构，由编码器、解码器和自注意力机制组成。",
            ),
            _result(
                "web-conflict",
                "Transformer 争议信息",
                snippet="Transformer 的架构结论存在冲突。",
                content="Transformer 架构与自注意力机制的公开结论存在冲突。",
                verification=WebSearchVerification.CONFLICTING,
            ),
        ),
    )

    assert turn.can_answer_reliably is False
    assert turn.evidence_gate.status == TeachingEvidenceStatus.CONFLICT
    assert turn.evidence_gate.external_sources == []
    assert turn.evidence_gate.search_status == TeachingCardStatus.RECOVERY


@pytest.mark.parametrize(
    ("verification", "fetched"),
    [
        (WebSearchVerification.SUMMARY_ONLY, True),
        (WebSearchVerification.FETCH_FAILED, False),
    ],
)
def test_summary_or_fetch_failure_never_enters_teaching_evidence_gate(
    verification: WebSearchVerification,
    fetched: bool,
) -> None:
    turn = TeachingTurnService().prepare(
        "学习Transformer架构的相关知识",
        retrieval=None,
        web_search=_web(
            _result(
                "web-unusable",
                "AI Transformer 架构与自注意力",
                snippet="Transformer 架构和自注意力机制。",
                content="",
                verification=verification,
                fetched=fetched,
            )
        ),
    )

    assert turn.can_answer_reliably is False
    assert turn.evidence_gate.external_sources == []
    assert turn.evidence_gate.status == TeachingEvidenceStatus.INSUFFICIENT
