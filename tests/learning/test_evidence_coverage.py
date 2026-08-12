"""学习模式公开来源覆盖裁决的 Issue 02 行为契约。"""

from datetime import UTC, datetime
from typing import Any

import bridges.learning.evidence_coverage as evidence_coverage
from bridges.chat.turn import teaching_web_result_ids
from bridges.contracts.teaching import TeachingCardStatus, TeachingEvidenceStatus
from bridges.learning.evidence_coverage import (
    EVIDENCE_COVERAGE_RULES_V1,
    EVIDENCE_COVERAGE_RULES_VERSION,
    adjudicate_web_sources,
)
from bridges.learning.teaching_gate import TeachingTurnService
from bridges.web_search.contracts import (
    WebSearchProjection,
    WebSearchResult,
    WebSearchStatus,
)


NOW = datetime(2026, 8, 12, tzinfo=UTC)


def _result(
    result_id: str,
    title: str,
    *,
    snippet: str = "",
    content: str = "",
    verification: str = "verified",
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


def test_coverage_uses_learning_goal_instead_of_query_summary() -> None:
    coverage = adjudicate_web_sources(
        "topic:无关查询摘要",
        [
            _result(
                "cnn-cn",
                "卷积神经网络（CNN）基础知识入门",
                content="介绍卷积神经网络的基本结构与核心机制。",
            )
        ],
        goal="学习“卷积神经网络”并理解其核心机制",
    )

    assert coverage.accepted_count == 1
    assert coverage.required_dimensions == ("topic:卷积神经网络",)


def test_teaching_gate_uses_goal_when_search_summary_is_a_different_fragment() -> None:
    turn = TeachingTurnService().prepare(
        "学习“卷积神经网络”并理解其核心机制",
        retrieval=None,
        web_search=WebSearchProjection(
            status=WebSearchStatus.SUCCESS,
            trigger_reason="本地材料不足",
            query_summary="topic:核心机制",
            results=[
                _result(
                    "cnn-cn",
                    "卷积神经网络（CNN）基础知识入门",
                    content="介绍卷积神经网络的基本结构与核心机制。",
                )
            ],
            searched_at=NOW,
        ),
        goal="学习“卷积神经网络”并理解其核心机制",
    )

    assert turn.evidence_gate.status == TeachingEvidenceStatus.SUFFICIENT
    assert turn.status == TeachingCardStatus.READY


def test_cjk_normalization_removes_goal_template_noise_and_full_width_variants() -> None:
    coverage = adjudicate_web_sources(
        "卷积神经网络的相关",
        [
            _result(
                "cnn-cjk",
                "卷积神经网络（ＣＮＮ）基础知识入门",
                content="卷积神经网络的基本结构与核心机制。",
            )
        ],
        goal="学习“卷积神经网络的相关基础知识入门”并理解其核心机制",
    )

    assert coverage.required_dimensions == ("topic:卷积神经网络",)
    assert coverage.accepted_count == 1


def test_cnn_aliases_accept_english_sources_for_a_chinese_goal() -> None:
    coverage = adjudicate_web_sources(
        "查询摘要",
        [
            _result(
                "cnn-en",
                "Convolutional Neural Network introduction",
                content="A convolutional neural network learns local visual features.",
            ),
            _result(
                "cnn-abbreviation",
                "CNN fundamentals",
                content="CNN is a neural network architecture for image processing.",
            ),
        ],
        goal="学习“卷积神经网络”并理解其核心机制",
    )

    assert coverage.accepted_count == 2


def test_unlisted_english_term_does_not_match_by_partial_word() -> None:
    coverage = adjudicate_web_sources(
        "查询摘要",
        [
            _result(
                "svm-en",
                "Support Vector Machine introduction",
                content="Support Vector Machine basics and examples.",
            )
        ],
        goal="学习“支持向量机”并理解其核心机制",
    )

    assert coverage.accepted_count == 0
    assert coverage.rejection_counts == {"topic_mismatch": 1}


def test_structured_sources_share_the_citable_verification_contract() -> None:
    coverage = adjudicate_web_sources(
        "查询摘要",
        [
            _result(
                "cnn-structured",
                "卷积神经网络（CNN）结构化来源",
                content="卷积神经网络用于从图像中提取局部特征。",
                verification="structured",
                fetched=False,
            )
        ],
        goal="学习“卷积神经网络”并理解其核心机制",
    )

    assert coverage.accepted_count == 1


def test_structured_sources_are_projected_into_the_teaching_evidence_gate() -> None:
    turn = TeachingTurnService().prepare(
        "学习“卷积神经网络”并理解其核心机制",
        retrieval=None,
        web_search=WebSearchProjection(
            status=WebSearchStatus.SUCCESS,
            trigger_reason="备用公开来源",
            query_summary="topic:卷积神经网络",
            results=[
                _result(
                    "cnn-structured",
                    "CNN 结构化来源",
                    content="CNN 用于从图像中提取局部特征。",
                    verification="structured",
                    fetched=False,
                )
            ],
            searched_at=NOW,
        ),
        goal="学习“卷积神经网络”并理解其核心机制",
    )

    assert turn.can_answer_reliably is True
    assert [source.source_id for source in turn.evidence_gate.external_sources] == [
        "cnn-structured"
    ]


def test_structured_sources_are_allowed_as_brave_context_sources() -> None:
    turn = TeachingTurnService().prepare(
        "学习“卷积神经网络”并理解其核心机制",
        retrieval=None,
        web_search=WebSearchProjection(
            status=WebSearchStatus.SUCCESS,
            trigger_reason="备用公开来源",
            query_summary="topic:卷积神经网络",
            results=[
                _result(
                    "cnn-structured",
                    "CNN 结构化来源",
                    content="CNN 用于从图像中提取局部特征。",
                    verification="structured",
                    fetched=False,
                ).model_copy(update={"provider": "brave_search"}),
            ],
            searched_at=NOW,
        ),
        goal="学习“卷积神经网络”并理解其核心机制",
    )

    assert [source.source_id for source in turn.evidence_gate.external_sources] == [
        "cnn-structured"
    ]
    assert teaching_web_result_ids(turn) == {"cnn-structured"}


def test_v1_constant_restores_query_summary_and_strict_fetch_rules(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        evidence_coverage,
        "EVIDENCE_COVERAGE_RULES_VERSION",
        evidence_coverage.EVIDENCE_COVERAGE_RULES_V1,
    )

    coverage = evidence_coverage.adjudicate_web_sources(
        "卷积神经网络的相关",
        [
            _result(
                "cnn-cn",
                "卷积神经网络（CNN）基础知识入门",
                content="该模型通过局部感受野提取特征。",
            )
        ],
        goal="学习“卷积神经网络”并理解其核心机制",
    )

    assert coverage.required_dimensions == ("topic:卷积神经网络的",)
    assert coverage.accepted_count == 0
    assert coverage.rules_version == evidence_coverage.EVIDENCE_COVERAGE_RULES_V1


def test_coverage_rules_expose_a_versioned_default_and_explicit_rollback_point() -> None:
    assert EVIDENCE_COVERAGE_RULES_VERSION == "learning-evidence-coverage-v2"
    assert EVIDENCE_COVERAGE_RULES_V1 == "learning-evidence-coverage-v1"
