"""体裁表达 profile 测试（Issue 28，人味化改造 Issue 04）。

断言四类体裁各自使用独立 profile（任务目标/风险/可选表达/禁止模式），
不再存在「必现元素」正则完成条件；体裁复核只判断任务完成与禁止模式，
不搜索指定套话；通用文章 profile 不强制任何元素。
"""

from __future__ import annotations

from bridges.contracts.expression import Genre
from bridges.skills.humanizer.genre_rules import (
    all_genres,
    check_genre,
    genre_rule_set,
    skill_doc,
)


def test_four_genres_have_distinct_profiles() -> None:
    genres = all_genres()
    assert set(genres) == {
        Genre.POPULAR_SCIENCE,
        Genre.LECTURE_SCRIPT,
        Genre.RESEARCH_REPORT,
        Genre.PAPER_ASSIST,
    }
    goals = [genre_rule_set(genre).task_goal for genre in genres]
    # 四类体裁各自独立，目标互不相同
    assert len(set(goals)) == 4
    for genre in genres:
        profile = genre_rule_set(genre)
        assert profile.risks, f"{genre.value} 缺少风险说明"
        assert profile.optional_devices, f"{genre.value} 缺少可选表达方式"
        assert profile.human_responsibility


def test_profiles_have_no_mandatory_elements() -> None:
    """Issue 04：体裁 profile 不再有必现元素正则完成条件。"""
    for genre in all_genres():
        profile = genre_rule_set(genre)
        for device in profile.optional_devices:
            assert "按需使用" in device, (
                f"{genre.value} 可选表达被写成必现条件：{device}"
            )


def test_genre_check_passes_without_mandatory_phrases() -> None:
    """正文即使没有定义/类比/练习/局限/下一步，也通过体裁复核。"""
    cases = [
        (Genre.POPULAR_SCIENCE, "光合作用把光能转化为化学能。数据显示速率约为 25 单位。"),
        (Genre.LECTURE_SCRIPT, "我们来看光合作用的两个阶段。先是光反应，再是暗反应。"),
        (Genre.RESEARCH_REPORT, "我们采集了三个品种的叶片，结果显示 35°C 下速率下降。"),
        (Genre.PAPER_ASSIST, "引言部分建议先综述研究现状，再提出研究缺口。"),
    ]
    for genre, text in cases:
        result = check_genre(text, genre)
        assert result.passed, f"{genre.value} 不应因缺少必现元素而失败：{result.summary()}"


def test_prohibited_pattern_detected() -> None:
    result = check_genre("本文将讨论光合作用。", Genre.POPULAR_SCIENCE)
    assert not result.passed
    assert any(f.rule_id == "ps_paper_tone" and not f.passed for f in result.findings)


def test_empty_text_fails_completion() -> None:
    result = check_genre("   ", Genre.RESEARCH_REPORT)
    assert not result.passed


def test_generic_profile_passes_any_text() -> None:
    result = check_genre("一些没有套话的自然段落。", None)
    assert result.passed
    assert result.genre is None


def test_skill_assets_exist_for_all_genres() -> None:
    for genre in all_genres():
        doc = skill_doc(genre)
        assert doc.strip(), f"{genre.value} 的体裁合同资产为空"
        assert "## " in doc  # 资产含结构化章节
        # Issue 04：资产文档不得再有「必含」章节（声明说明文字除外）
        assert "## 必含" not in doc, f"{genre.value} 资产仍声明必含元素"
        assert "缺失即复核不通过" not in doc
