"""Issue 23：学习模式教学证据门与统一教学轮次测试。"""

from datetime import UTC, datetime

from bridges.arxiv_mcp.contracts import (
    ArxivPaperProjection,
    ArxivSearchProjection,
    ArxivSearchStatus,
)
from bridges.contracts.retrieval import (
    CitationProjection,
    RetrievalRoundProjection,
    RetrievalSourceLayer,
    RetrievalSufficiency,
)
from bridges.contracts.teaching import TeachingCardStatus, TeachingEvidenceStatus
from bridges.learning.teaching_gate import TeachingTurnService
from bridges.web_search.contracts import (
    WebSearchProjection,
    WebSearchResult,
    WebSearchStatus,
)

NOW = datetime(2026, 8, 4, tzinfo=UTC)


def _local_round(
    sufficiency: RetrievalSufficiency, *, note: str | None = None
) -> RetrievalRoundProjection:
    citations = []
    if sufficiency == RetrievalSufficiency.SUFFICIENT:
        citations = [
            CitationProjection(
                citation_id="cit-1",
                source_layer=RetrievalSourceLayer.ATTACHMENT,
                object_id="obj-1",
                filename="讲义.txt",
                media_type="text/plain",
                snippet="核心机制",
                rank=1,
            )
        ]
    return RetrievalRoundProjection(
        round_id="round-1",
        message_id="assistant-1",
        conversation_id="conversation-1",
        use_knowledge_base=True,
        sufficiency=sufficiency,
        layers=[],
        citations=citations,
        note=note,
        created_at=NOW,
    )


def _web(status: WebSearchStatus) -> WebSearchProjection:
    results = []
    if status == WebSearchStatus.SUCCESS:
        results = [
            WebSearchResult(
                result_id="web-1",
                title="公开来源",
                url="https://example.com/source",
                site="example.com",
                snippet="来源片段",
                accessed_at=NOW,
            )
        ]
    return WebSearchProjection(
        status=status,
        trigger_reason="本地材料不足",
        query_summary="量子机制",
        results=results,
        error_message="搜索失败" if status != WebSearchStatus.SUCCESS else None,
    )


def test_no_local_material_automatically_requires_duckduckgo_and_exposes_gap() -> None:
    turn = TeachingTurnService().prepare(
        "解释量子纠缠",
        retrieval=None,
        web_search=_web(WebSearchStatus.EMPTY),
    )

    assert turn.evidence_gate.status == TeachingEvidenceStatus.INSUFFICIENT
    assert turn.evidence_gate.required_search.value == "duckduckgo"
    assert turn.status == TeachingCardStatus.EMPTY
    assert turn.can_answer_reliably is False
    assert turn.evidence_gate.allow_model_knowledge is True
    assert "未联网核实" in (turn.gap_response or "")


def test_missing_external_provider_still_exposes_an_explicit_gap() -> None:
    turn = TeachingTurnService().prepare(
        "解释量子纠缠",
        retrieval=None,
    )

    assert turn.evidence_gate.required_search.value == "duckduckgo"
    assert turn.evidence_gate.gap
    assert turn.can_answer_reliably is False
    assert turn.status == TeachingCardStatus.EMPTY
    assert turn.evidence_gate.allow_model_knowledge is True


def test_sufficient_local_material_skips_external_search_and_creates_one_quiz() -> None:
    turn = TeachingTurnService().prepare(
        "解释光合作用",
        retrieval=_local_round(RetrievalSufficiency.SUFFICIENT),
    )

    assert turn.evidence_gate.status == TeachingEvidenceStatus.SUFFICIENT
    assert turn.evidence_gate.required_search.value == "none"
    assert turn.can_answer_reliably is True
    assert turn.quiz is not None
    assert turn.quiz.evidence_refs == ["cit-1"]


def test_conflict_keeps_conflict_status_and_arxiv_source_type_distinct() -> None:
    paper = ArxivPaperProjection(
        citation_id="arxiv-1",
        arxiv_id="2401.00001",
        title="A paper",
        authors=["Author"],
        published_at=NOW,
        abs_url="https://arxiv.org/abs/2401.00001",
        pdf_url="https://arxiv.org/pdf/2401.00001",
        abstract="Abstract",
        summary_zh="摘要",
        relevance_basis="标题匹配",
        learning_advice_zh="核对方法",
    )
    arxiv = ArxivSearchProjection(
        status=ArxivSearchStatus.SUCCESS,
        trigger_reason="本地材料冲突",
        query_summary="量子论文",
        papers=[paper],
    )
    turn = TeachingTurnService().prepare(
        "请解释量子纠缠论文中的方法",
        retrieval=_local_round(RetrievalSufficiency.CONFLICT),
        arxiv_search=arxiv,
    )

    assert turn.evidence_gate.status == TeachingEvidenceStatus.CONFLICT
    assert turn.evidence_gate.external_sources[0].source_type == "arxiv"
    assert turn.evidence_gate.external_sources[0].source_id == "arxiv-1"
    assert turn.evidence_gate.external_sources[0].accessed_at
    assert turn.evidence_gate.external_sources[0].url == paper.abs_url
    assert turn.evidence_gate.external_sources[0].alternate_url == paper.pdf_url
    assert turn.status == TeachingCardStatus.RECOVERY
    assert turn.can_answer_reliably is False
    assert turn.evidence_gate.allow_model_knowledge is False


def test_stale_local_material_requires_public_search() -> None:
    turn = TeachingTurnService().prepare(
        "解释光合作用的最新研究论文",
        retrieval=_local_round(RetrievalSufficiency.SUFFICIENT, note="材料已过时"),
    )

    assert turn.evidence_gate.required_search.value == "both"
    assert turn.evidence_gate.status == TeachingEvidenceStatus.UNAVAILABLE
    assert turn.can_answer_reliably is False


def test_public_paper_query_can_combine_duckduckgo_and_arxiv() -> None:
    service = TeachingTurnService()
    assert service.required_search("最新公开论文研究综述", None).value == "both"


def test_answer_evidence_is_traceable_but_never_claims_mastery() -> None:
    service = TeachingTurnService()
    previous = service.prepare(
        "解释光合作用",
        retrieval=_local_round(RetrievalSufficiency.SUFFICIENT),
    )
    answer = service.prepare(
        "解释光合作用",
        retrieval=_local_round(RetrievalSufficiency.SUFFICIENT),
        previous_turn=previous,
        answer_text="光合作用",
        answer_message_id="user-answer-1",
    )

    record = answer.evidence[0]
    assert record.question_id == previous.quiz.question_id  # type: ignore[union-attr]
    assert record.source_message_id == "user-answer-1"
    assert record.evaluation_basis
    assert record.requires_confirmation is True
    assert record.mastery_claim_allowed is False
    # 说明性文案明确「不能直接标记已掌握」（否定句），不构成掌握宣称。
    assert "不能直接标记已掌握" in record.knowledge_state_reason


def test_uncertain_answer_that_repeats_topic_is_not_marked_correct() -> None:
    service = TeachingTurnService()
    previous = service.prepare(
        "解释光合作用",
        retrieval=_local_round(RetrievalSufficiency.SUFFICIENT),
    )
    answer = service.prepare(
        "解释光合作用",
        retrieval=_local_round(RetrievalSufficiency.SUFFICIENT),
        previous_turn=previous,
        answer_text="我不懂光合作用",
        answer_message_id="user-answer-uncertain",
    )

    assert answer.evidence[0].evaluated_state.value == "incorrect"
    assert answer.evidence[0].knowledge_state.value == "unknown"


def test_follow_up_question_is_not_recorded_as_quiz_answer() -> None:
    service = TeachingTurnService()
    previous = service.prepare(
        "解释光合作用",
        retrieval=_local_round(RetrievalSufficiency.SUFFICIENT),
    )
    follow_up = service.prepare(
        "解释光合作用",
        retrieval=_local_round(RetrievalSufficiency.SUFFICIENT),
        previous_turn=previous,
        answer_text="为什么还需要举例？",
        answer_message_id="user-follow-up",
    )

    assert follow_up.evidence == []
    assert follow_up.quiz is not None
