"""切片覆盖测试（Test plan 2：路径/模式/强度/体裁/风险/对抗数量）。"""

from __future__ import annotations

from bridges.humanize_eval.cases import (
    ArticleGenre,
    CaseOperation,
    ConversationMode,
    HumanizeCase,
    HumanizeCaseKind,
    RewriteIntensity,
    RiskLevel,
    surface_cases,
)

#: 聊天路径切片（11 类 + 学习课时）。
CHAT_PATH_TAGS = {
    "short_answer",
    "explanation",
    "advice",
    "correction",
    "multi_turn",
    "emotion",
    "clarification",
    "tool_result",
    "error_refusal",
    "code_formula",
    "learning_lesson",
}


def _tagged(cases, tag: str) -> list[HumanizeCase]:
    return [c for c in cases if tag in c.slice_tags]


def test_chat_paths_all_covered():
    chat = surface_cases(HumanizeCaseKind.CHAT)
    for tag in CHAT_PATH_TAGS:
        assert _tagged(chat, tag), f"聊天路径切片缺失：{tag}"


def test_chat_modes_balanced():
    """日常陪伴与学习模式分层均衡（各不低于 40%）。"""
    chat = surface_cases(HumanizeCaseKind.CHAT)
    casual = [c for c in chat if c.conversation_mode is ConversationMode.CASUAL]
    learning = [c for c in chat if c.conversation_mode is ConversationMode.LEARNING]
    assert casual and learning
    assert len(casual) / len(chat) >= 0.4
    assert len(learning) / len(chat) >= 0.4


def test_learning_lessons_present():
    chat = surface_cases(HumanizeCaseKind.CHAT)
    lessons = [c for c in chat if "learning_lesson" in c.slice_tags]
    assert len(lessons) >= 4


def test_chat_high_risk_do_no_harm_cases():
    chat = surface_cases(HumanizeCaseKind.CHAT)
    high_risk = [c for c in chat if c.risk is RiskLevel.HIGH]
    assert len(high_risk) >= 3
    assert all(c.do_no_harm for c in high_risk), "高风险聊天案例必须带 do_no_harm"


def test_article_genres_all_covered():
    article = surface_cases(HumanizeCaseKind.ARTICLE)
    genres = {c.genre_profile for c in article}
    for genre in ArticleGenre:
        if genre is ArticleGenre.GENERAL:
            continue
        assert genre in genres, f"文章体裁缺失：{genre}"


def test_article_operations_covered():
    article = surface_cases(HumanizeCaseKind.ARTICLE)
    ops = {c.operation for c in article}
    assert CaseOperation.REWRITE in ops
    assert CaseOperation.GENERATE in ops
    generated = [c for c in article if c.operation is CaseOperation.GENERATE]
    assert len(generated) >= 4


def test_article_intensities_all_three_levels():
    article = surface_cases(HumanizeCaseKind.ARTICLE)
    intensities = {c.rewrite_intensity for c in article}
    for level in RewriteIntensity:
        assert level in intensities, f"改写强度缺失：{level}"


def test_article_risk_and_material_scenarios():
    article = surface_cases(HumanizeCaseKind.ARTICLE)
    high_risk = [c for c in article if c.risk is RiskLevel.HIGH]
    assert high_risk and all(c.do_no_harm for c in high_risk)
    tags = {tag for c in article for tag in c.slice_tags}
    assert "material_insufficient" in tags
    assert "already_natural" in tags
    assert "high_fact_density" in tags
    assert "low_fact_density" in tags


def test_no_duplicate_case_ids():
    ids = [c.case_id for c in surface_cases(HumanizeCaseKind.CHAT)]
    assert len(ids) == len(set(ids))
    ids = [c.case_id for c in surface_cases(HumanizeCaseKind.ARTICLE)]
    assert len(ids) == len(set(ids))
