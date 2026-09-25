"""Issue 11 论文模块：解析、消歧、计划与排序的单元合同。

覆盖验收要点：
- 原始术语逐字保留，扩展词只作补充；
- 孤立「Transformer」先消歧；明确机器学习语境（含前文语境）直接检索相应主题；
- 澄清回答能从等待状态恢复，仍不能消歧时只再问一项；
- 排序核对主题覆盖、给阅读顺序与选择理由，主题不匹配时不凑篇数。
"""

from __future__ import annotations

from datetime import UTC, datetime

from bridges.contracts.modules import ModuleWaitState
from bridges.paper.contracts import PaperSortIntent
from bridges.paper.parsing import parse_paper_request, pending_payload
from bridges.paper.planning import plan_queries
from bridges.paper.ranking import cover_original_phrase, rank_candidates
from bridges.paper.sources import EnrichedMetadata, PaperCandidate

NOW = datetime(2026, 9, 25, tzinfo=UTC)


def _candidate(
    arxiv_id: str,
    title: str,
    abstract: str = "attention mechanism for sequence modeling",
    year: int = 2024,
    category: str | None = "cs.LG",
) -> PaperCandidate:
    return PaperCandidate(
        arxiv_id=arxiv_id,
        title=title,
        authors=["A. Author"],
        published_at=datetime(year, 1, 1, tzinfo=UTC),
        abs_url=f"https://arxiv.org/abs/{arxiv_id}",
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
        abstract=abstract,
        primary_category=category,
    )


# ---------------------------------------------------------------------------
# paper.parse
# ---------------------------------------------------------------------------


def test_isolated_transformer_asks_one_clarification() -> None:
    """孤立「Transformer」歧义：只问一项，带上两个候选语境。"""
    analysis = parse_paper_request("帮我找 Transformer 论文", now=NOW)
    assert analysis.original_phrase.lower() == "transformer"
    assert analysis.clarification is not None
    assert analysis.clarification.missing == "domain"
    assert analysis.clarification.question.count("？") == 1
    keys = [candidate.key for candidate in analysis.clarification.candidates]
    assert keys == ["machine_learning", "power_electronics"]
    assert analysis.final_query == ""
    assert analysis.confidence < 0.5


def test_machine_learning_context_retrieves_ml_topic_without_asking() -> None:
    """明确机器学习语境：不提问，检索相应主题，原词仍在查询里。"""
    analysis = parse_paper_request("Transformer 的注意力机制入门", now=NOW)
    assert analysis.clarification is None
    assert analysis.context_key == "machine_learning"
    assert analysis.original_phrase.lower() == "transformer"
    assert analysis.final_query.lower() == "transformer"
    assert "attention mechanism" in analysis.expansions
    assert analysis.constraints.sort_intent is PaperSortIntent.BEGINNER


def test_power_context_retrieves_power_topic() -> None:
    analysis = parse_paper_request("电力系统里的 transformer 有哪些研究", now=NOW)
    assert analysis.clarification is None
    assert analysis.context_key == "power_electronics"
    assert "power transformer" in analysis.expansions


def test_prior_context_resolves_ambiguity() -> None:
    """前文已确认语境（机器学习）时不再追问。"""
    analysis = parse_paper_request(
        "再找几篇 Transformer 的",
        prior_context=["我在学深度学习，想了解注意力机制"],
        now=NOW,
    )
    assert analysis.clarification is None
    assert analysis.context_key == "machine_learning"


def test_chinese_topic_is_translated_but_original_is_preserved() -> None:
    analysis = parse_paper_request("找几篇知识蒸馏的论文", now=NOW)
    assert analysis.clarification is None
    assert analysis.original_phrase == "知识蒸馏"
    assert analysis.final_query == "knowledge distillation"


def test_empty_topic_asks_one_question() -> None:
    analysis = parse_paper_request("帮我找几篇论文", now=NOW)
    assert analysis.clarification is not None
    assert analysis.clarification.missing == "topic"
    assert analysis.final_query == ""


def test_clarification_answer_resumes_and_keeps_original_phrase() -> None:
    """澄清回答恢复：原词与前文限制沿用，不把回答当成新的主题。"""
    first = parse_paper_request("找 Transformer 的综述", now=NOW)
    pending = ModuleWaitState(
        module_id="paper",
        kind="clarification",
        question=first.clarification.question if first.clarification else "",
        origin_message_id="assistant-1",
        context=pending_payload(first, ambiguous_term="transformer"),
        created_at=NOW,
    )
    resumed = parse_paper_request(
        "机器学习方向的，最好入门", pending=pending, now=NOW
    )
    assert resumed.clarification is None
    assert resumed.context_key == "machine_learning"
    assert resumed.original_phrase.lower() == "transformer"
    assert resumed.constraints.prefer_survey is True


def test_clarification_answer_still_ambiguous_asks_again() -> None:
    first = parse_paper_request("找 Transformer 的综述", now=NOW)
    pending = ModuleWaitState(
        module_id="paper",
        kind="clarification",
        question=first.clarification.question if first.clarification else "",
        origin_message_id="assistant-1",
        context=pending_payload(first, ambiguous_term="transformer"),
        created_at=NOW,
    )
    again = parse_paper_request("都行", pending=pending, now=NOW)
    assert again.clarification is not None
    assert again.original_phrase.lower() == "transformer"


# ---------------------------------------------------------------------------
# paper.plan
# ---------------------------------------------------------------------------


def test_plan_is_original_first_with_bounded_attempts() -> None:
    analysis = parse_paper_request("Transformer 的注意力机制入门", now=NOW)
    plans = plan_queries(analysis)
    assert plans[0].query.split()[0].lower() == "transformer"
    assert "attention mechanism" in plans[0].expansions_used
    assert len(plans) == 2, "扩展词生效时保留一次只用主词的放宽尝试"
    assert plans[1].query.lower() == "transformer"
    assert plans[0].max_results >= 9


def test_latest_intent_uses_submitted_date_sorting() -> None:
    analysis = parse_paper_request("找深度学习里 Transformer 的最新进展", now=NOW)
    assert analysis.constraints.sort_intent is PaperSortIntent.LATEST
    plans = plan_queries(analysis)
    assert plans[0].sort_by == "submittedDate"
    assert plans[0].sort_order == "descending"


def test_year_constraints_are_parsed() -> None:
    analysis = parse_paper_request("2020年以后的知识蒸馏论文", now=NOW)
    assert analysis.constraints.year_from == 2020
    assert analysis.constraints.year_to is None


def test_year_range_really_filters_and_reports_excluded_count() -> None:
    """用户提到的年份范围是真实过滤条件：范围外有命中就报出被排除的篇数。"""
    analysis = parse_paper_request(
        "2020年以后的 Transformer 注意力机制论文", now=NOW
    )
    plan = plan_queries(analysis)[0]
    candidates = [
        _candidate("2401.1", "Transformer Attention Survey", "survey", year=2024),
        _candidate("2401.2", "Old Transformer Notes", "attention mechanism", year=2018),
    ]
    outcome = rank_candidates(analysis, plan, candidates, {})
    titles = [item.title for item in outcome.recommendations]
    assert titles == ["Transformer Attention Survey"]
    assert any("2020 年起" in note and "1 篇" in note for note in outcome.notes)


def test_year_range_widens_instead_of_silently_returning_nothing() -> None:
    """范围外才有结果时保留候选并如实说明（不悄悄忽略范围，也不假装没有结果）。"""
    analysis = parse_paper_request(
        "2020年以后的 Transformer 注意力机制论文", now=NOW
    )
    plan = plan_queries(analysis)[0]
    outcome = rank_candidates(
        analysis,
        plan,
        [_candidate("2401.3", "Transformer Attention in 2018", "attention", year=2018)],
        {},
    )
    assert [item.title for item in outcome.recommendations] == ["Transformer Attention in 2018"]
    assert not outcome.topic_mismatch
    assert any("范围外" in note for note in outcome.notes)


# ---------------------------------------------------------------------------
# paper.rank
# ---------------------------------------------------------------------------


def test_rank_drops_topic_mismatch_and_reports_order() -> None:
    analysis = parse_paper_request("Transformer 的注意力机制入门", now=NOW)
    plan = plan_queries(analysis)[0]
    candidates = [
        _candidate(
            "2401.00001",
            "A Survey of Transformer Attention Mechanisms",
            "survey review of attention",
            year=2023,
        ),
        _candidate(
            "2401.00002",
            "Transformer for High Voltage Substations",
            "power grid transformer monitoring",
            year=2022,
        ),
        _candidate(
            "2401.00003",
            "Attention Is All You Need Revisited",
            "attention mechanism transformer architecture",
            year=2017,
        ),
    ]
    outcome = rank_candidates(analysis, plan, candidates, {})
    titles = [item.title for item in outcome.recommendations]
    assert not outcome.topic_mismatch
    assert "Transformer for High Voltage Substations" not in titles
    assert outcome.recommendations[0].order == 1
    assert outcome.recommendations[0].role == "survey"
    assert outcome.recommendations[0].summary_zh is None
    assert outcome.recommendations[0].unverified, "未通读全文必须标注"
    assert "标题或摘要覆盖检索词" in outcome.recommendations[0].match_basis


def test_rank_reports_mismatch_when_nothing_matches() -> None:
    analysis = parse_paper_request("Transformer 的注意力机制入门", now=NOW)
    plan = plan_queries(analysis)[0]
    outcome = rank_candidates(
        analysis,
        plan,
        [_candidate("2401.9", "Powder Metallurgy of Copper", "sintering process")],
        {},
    )
    assert outcome.topic_mismatch
    assert outcome.recommendations == []
    assert outcome.notes, "必须说明没有匹配结果"


def test_rank_counts_arxiv_pdf_as_available_full_text() -> None:
    """arXiv 候选自带来源给出的 PDF 链接时，就是已取得全文（不误报"未确认"）。"""
    analysis = parse_paper_request("Transformer 的注意力机制入门", now=NOW)
    plan = plan_queries(analysis)[0]
    outcome = rank_candidates(
        analysis,
        plan,
        [_candidate("2401.6", "Transformer Attention Survey", "survey attention")],
        {},
    )
    paper = outcome.recommendations[0]
    assert paper.pdf_url
    assert paper.full_text_available is True
    assert not any("全文链接" in item for item in paper.unverified)


def test_rank_marks_full_text_and_publication_info_when_enriched() -> None:
    analysis = parse_paper_request("Transformer 的注意力机制入门", now=NOW)
    plan = plan_queries(analysis)[0]
    candidate = _candidate("2401.5", "Transformer Attention Survey", "survey")
    outcome = rank_candidates(
        analysis,
        plan,
        [candidate],
        {
            "2401.5": EnrichedMetadata(
                source="openalex",
                cited_by_count=120,
                venue="Journal of Testing",
                year=2024,
                is_open_access=True,
                full_text_url="https://example.org/paper.pdf",
            )
        },
    )
    paper = outcome.recommendations[0]
    assert paper.full_text_available is True
    assert "Journal of Testing" in paper.reason_zh
    assert "120" in paper.reason_zh
    assert cover_original_phrase(analysis, outcome.recommendations)
