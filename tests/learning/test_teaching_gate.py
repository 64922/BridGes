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
    sufficiency: RetrievalSufficiency,
    *,
    note: str | None = None,
    media_type: str = "text/plain",
) -> RetrievalRoundProjection:
    citations = []
    if sufficiency == RetrievalSufficiency.SUFFICIENT:
        citations = [
            CitationProjection(
                citation_id="cit-1",
                source_layer=RetrievalSourceLayer.ATTACHMENT,
                object_id="obj-1",
                filename="图表.jpg" if media_type.startswith("image/") else "讲义.txt",
                media_type=media_type,
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


def test_no_local_material_automatically_requires_tavily_and_exposes_gap() -> None:
    turn = TeachingTurnService().prepare(
        "解释量子纠缠",
        retrieval=None,
        web_search=_web(WebSearchStatus.EMPTY),
    )

    assert turn.evidence_gate.status == TeachingEvidenceStatus.INSUFFICIENT
    assert turn.evidence_gate.required_search.value == "tavily"
    assert turn.status == TeachingCardStatus.EMPTY
    assert turn.can_answer_reliably is False
    assert turn.evidence_gate.allow_model_knowledge is True
    assert "未联网核实" in (turn.gap_response or "")


def test_missing_external_provider_still_exposes_an_explicit_gap() -> None:
    turn = TeachingTurnService().prepare(
        "解释量子纠缠",
        retrieval=None,
    )

    assert turn.evidence_gate.required_search.value == "tavily"
    assert turn.evidence_gate.gap
    assert turn.can_answer_reliably is False
    assert turn.status == TeachingCardStatus.EMPTY
    assert turn.evidence_gate.allow_model_knowledge is True


def test_provider_challenge_is_blocked_with_explicit_cooldown_recovery() -> None:
    challenge = _web(WebSearchStatus.ERROR).model_copy(
        update={
            "error_code": "web_search_provider_challenge",
            "error_message": "提供方当前受阻，请稍后重试。",
        }
    )

    turn = TeachingTurnService().prepare(
        "解释量子纠缠",
        retrieval=None,
        web_search=challenge,
    )

    assert turn.evidence_gate.search_error_code == "web_search_provider_challenge"
    assert "提供方受阻" in turn.evidence_gate.reason
    assert any("冷却" in step for step in turn.evidence_gate.recovery_steps)
    assert turn.evidence_gate.allow_model_knowledge is True
    assert turn.can_answer_reliably is False


def test_sufficient_local_material_skips_external_search_and_creates_one_overview() -> None:
    turn = TeachingTurnService().prepare(
        "解释光合作用",
        retrieval=_local_round(RetrievalSufficiency.SUFFICIENT),
    )

    assert turn.evidence_gate.status == TeachingEvidenceStatus.SUFFICIENT
    assert turn.evidence_gate.required_search.value == "none"
    assert turn.can_answer_reliably is True
    assert turn.mission is None
    assert turn.plan is None
    assert turn.lesson is None
    assert turn.quiz is None
    assert "全面介绍" in turn.steps[1]
    assert "不强制测验" in turn.check_method


def test_image_local_hit_is_preserved_and_annotated() -> None:
    turn = TeachingTurnService().prepare(
        "这张图片是什么",
        retrieval=_local_round(
            RetrievalSufficiency.SUFFICIENT,
            media_type="image/jpeg",
        ),
    )

    assert turn.evidence_gate.status == TeachingEvidenceStatus.SUFFICIENT
    assert turn.evidence_gate.required_search.value == "none"
    assert [source.title for source in turn.evidence_gate.local_sources] == ["图表.jpg"]
    assert turn.evidence_gate.local_sources[0].locator == "图片未做内容理解"
    assert "没有可用命中" not in turn.evidence_gate.reason


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


def test_public_paper_query_can_combine_tavily_and_arxiv() -> None:
    service = TeachingTurnService()
    assert service.required_search("最新公开论文研究综述", None).value == "both"


def test_follow_up_does_not_create_legacy_answer_evidence() -> None:
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

    assert previous.quiz is None
    assert answer.evidence == []
    assert answer.quiz is None


def test_uncertain_follow_up_is_not_interpreted_as_a_quiz_answer() -> None:
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

    assert answer.evidence == []
    assert answer.quiz is None


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
    assert follow_up.quiz is None
