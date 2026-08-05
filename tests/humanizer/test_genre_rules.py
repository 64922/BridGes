"""四体裁表达规则可测试性测试（Issue 28）。

断言四类体裁各自使用独立的规则集（不共用单一泛化模板），规则在
SKILL 资产中都有对应文档，且同一文本在不同体裁下判定不同。
"""

from __future__ import annotations

from bridges.contracts.expression import Genre
from bridges.skills.humanizer.genre_rules import (
    all_genres,
    check_genre,
    genre_rule_set,
    skill_doc,
)

_POPULAR_SCIENCE_TEXT = (
    "光合作用指的是植物把光能转化为化学能的过程。你可以把它比作植物的"
    "充电过程，但比喻到此为止，实际机制是叶绿素吸收光子。对你说来，这"
    "意味着多吃绿叶菜有助于理解这一过程。数据显示约 25 μmol·m⁻²·s⁻¹ 的"
    "净光合速率在光照充足时常见。"
)

_LECTURE_SCRIPT_TEXT = (
    "学完本节，你将能复述光合作用的基本过程。前提是你知道什么是化学能。"
    "首先引入概念：叶绿体是光合作用的场所，比如植物的叶片里就有很多。"
    "检查一下：你能说出光合作用的原料有哪些吗？请尝试画一张流程图，"
    "花 1 分钟完成。数据显示约 25 μmol·m⁻²·s⁻¹ 的速率在光照充足时常见。"
)

_RESEARCH_REPORT_TEXT = (
    "本报告围绕水稻净光合速率展开。采用便携式光合仪采集数据，结果显示"
    "在 35°C 下净光合速率显著下降。该样本仅包含三个品种，需要说明的是，"
    "结果不能直接外推到田间。下一步计划在高温胁迫条件下补充重复实验。"
)

_PAPER_ASSIST_TEXT = (
    "针对引言部分的结构建议：先综述光合速率研究现状再提出研究缺口。"
    "语言建议：将'做实验'改为'开展实验'。引用核查：Smith (2020) 已核实"
    "存在于 PubMed。论证建议：结论的因果推断需补充中介分析。请注意在"
    "投稿时按期刊要求完成 AI 使用披露声明。"
)


def test_four_genres_have_distinct_rule_sets() -> None:
    genres = all_genres()
    assert set(genres) == {
        Genre.POPULAR_SCIENCE,
        Genre.LECTURE_SCRIPT,
        Genre.RESEARCH_REPORT,
        Genre.PAPER_ASSIST,
    }
    rule_ids = [
        tuple(rule.rule_id for rule in genre_rule_set(genre).required)
        + tuple(rule.rule_id for rule in genre_rule_set(genre).prohibited)
        for genre in genres
    ]
    # 四类体裁规则标识互不重叠（不共用单一泛化模板）
    assert len(set(rule_ids)) == 4
    for ids in rule_ids:
        assert len(ids) >= 5, f"体裁规则过少：{ids}"


def test_each_genre_passes_its_own_contract() -> None:
    cases = [
        (Genre.POPULAR_SCIENCE, _POPULAR_SCIENCE_TEXT),
        (Genre.LECTURE_SCRIPT, _LECTURE_SCRIPT_TEXT),
        (Genre.RESEARCH_REPORT, _RESEARCH_REPORT_TEXT),
        (Genre.PAPER_ASSIST, _PAPER_ASSIST_TEXT),
    ]
    for genre, text in cases:
        result = check_genre(text, genre)
        assert result.passed, f"{genre.value} 未通过自身规则：{result.summary()}"


def test_genre_check_is_discriminating() -> None:
    # 科普文案文本按课程讲稿规则判定应缺失学习目标等要素
    result = check_genre(_POPULAR_SCIENCE_TEXT, Genre.LECTURE_SCRIPT)
    assert not result.passed
    assert any(f.rule_id == "ls_learning_objective" and not f.passed for f in result.findings)


def test_prohibited_pattern_detected() -> None:
    result = check_genre("本文将讨论光合作用。", Genre.POPULAR_SCIENCE)
    assert not result.passed
    assert any(f.rule_id == "ps_paper_tone" and not f.passed for f in result.findings)


def test_skill_assets_exist_for_all_genres() -> None:
    for genre in all_genres():
        doc = skill_doc(genre)
        assert doc.strip(), f"{genre.value} 的体裁合同资产为空"
        assert "## " in doc  # 资产含结构化章节
