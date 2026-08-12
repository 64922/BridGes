"""案例版本化与哈希（Test plan：案例定义部分）。"""

from __future__ import annotations

import pytest

from bridges.humanize_eval.cases import (
    CORPUS_VERSION,
    HUMANIZE_CASES,
    HumanizeCaseIntegrityError,
    HumanizeCaseKind,
    MIN_CASES_PER_SURFACE,
    case_hashes,
    corpus_hashes,
    ledger_hashes,
    surface_cases,
    validate_cases,
)


def test_registry_has_both_surfaces():
    kinds = {case.kind for case in HUMANIZE_CASES}
    assert HumanizeCaseKind.ARTICLE in kinds
    assert HumanizeCaseKind.CHAT in kinds
    assert len(HUMANIZE_CASES) == 93


def test_surface_case_counts_meet_minimum():
    """chat-naturalness 与 article-humanization 分别至少 40 个案例。"""
    chat = surface_cases(HumanizeCaseKind.CHAT)
    article = surface_cases(HumanizeCaseKind.ARTICLE)
    assert len(chat) >= MIN_CASES_PER_SURFACE
    assert len(article) >= MIN_CASES_PER_SURFACE


def test_case_hashes_stable_and_versioned():
    hashes = case_hashes()
    assert len(hashes) == 93
    assert all(len(h) == 64 for h in hashes.values())
    assert all(case.case_id.endswith("-v1") for case in HUMANIZE_CASES)


def test_corpus_and_ledger_hashes_present():
    corpus = corpus_hashes()
    assert set(corpus) == {"chat", "article"}
    assert all(len(h) == 64 for h in corpus.values())
    ledger = ledger_hashes()
    assert set(ledger) == {"chat", "article"}
    assert all(len(h) == 64 for h in ledger.values())


def test_corpus_version_set():
    assert CORPUS_VERSION


def test_validate_cases_clean():
    assert validate_cases() == []


def test_hash_detects_content_change():
    case = HUMANIZE_CASES[0]
    tampered = case.model_copy(update={"audience": "被篡改的受众"})
    with pytest.raises(HumanizeCaseIntegrityError):
        tampered.ensure_hash()


def test_partition_markers_exist():
    from bridges.humanize_eval.cases import CasePartition

    holdout = [c for c in HUMANIZE_CASES if c.partition is CasePartition.HOLDOUT]
    development = [c for c in HUMANIZE_CASES if c.partition is CasePartition.DEVELOPMENT]
    # holdout 冻结不影响各面 development 下限。
    assert len(holdout) >= 8
    assert len(development) >= 80
    assert len(holdout) + len(development) == 93
    chat_dev = [c for c in development if c.kind is HumanizeCaseKind.CHAT]
    article_dev = [c for c in development if c.kind is HumanizeCaseKind.ARTICLE]
    assert len(chat_dev) >= MIN_CASES_PER_SURFACE
    assert len(article_dev) >= MIN_CASES_PER_SURFACE


def test_time_management_frozen_cases_present():
    """用户时间管理失败型文章冻结案例 + 无亲历对抗变体必须存在。"""
    ids = {c.case_id for c in HUMANIZE_CASES}
    assert "article-time-management-failure-v1" in ids
    assert "article-time-management-failure-nocontext-v1" in ids
    failure = next(c for c in HUMANIZE_CASES if c.case_id == "article-time-management-failure-v1")
    nocontext = next(c for c in HUMANIZE_CASES if c.case_id == "article-time-management-failure-nocontext-v1")
    assert failure.source_text
    # 原文不含编造亲历模式（冻结案例输入无亲历）。
    for pattern in ("我以前", "我过去", "我试过", "上周", "半小时前", "朋友", "刷短视频"):
        assert pattern not in failure.source_text, f"原文出现亲历模式：{pattern}"
    # 对抗变体显式禁止亲历词。
    assert any("刷短视频" in claim for claim in nocontext.forbidden_claims)
    assert any("我以前" in claim for claim in nocontext.forbidden_claims)


def test_adversarial_coverage_present():
    """12 类对抗案例全部注册。"""
    adversarial = {c.adversarial_type for c in HUMANIZE_CASES if c.adversarial_type}
    expected_kinds = {
        "商业/PPT 抽象词",
        "抽象名词链",
        "强行生活场景",
        "假经验/假情绪",
        "机械三段式",
        "每段金句",
        "过度设问",
        "居高临下",
        "无必要第一人称",
        "引语含禁词",
        "合法术语",
        "必要冒号/破折号/列表",
        "材料不足",
        "假经验/假情绪（输入无亲历）",
        "假经验/假情绪（显式禁止亲历词变体）",
    }
    for kind in expected_kinds:
        assert kind in adversarial, f"缺少对抗类型：{kind}"


def test_article_case_declares_no_personal_experience():
    article = next(
        c for c in HUMANIZE_CASES
        if c.case_id == "article-time-management-failure-v1"
    )
    assert article.source_text
    # 原文不含编造亲历模式（冻结案例输入无亲历）。
    for pattern in ("我以前", "我过去", "我试过", "上周", "半小时前", "朋友"):
        assert pattern not in article.source_text, f"原文出现亲历模式：{pattern}"
    # 禁止 claim 覆盖亲历/对话/数据/时间地点等类别（至少一类有明确关键词）。
    assert any(
        any(
            keyword in claim
            for keyword in ("亲历", "对话", "数据", "功能", "时间地点")
        )
        for claim in article.forbidden_claims
    ), "禁止 claim 清单缺少明确类别"


def test_chat_case_is_simple_question():
    chat = next(c for c in HUMANIZE_CASES if c.kind is HumanizeCaseKind.CHAT)
    assert "？" in chat.user_request or "?" in chat.user_request
    assert any("教学" in claim or "长文" in claim for claim in chat.forbidden_claims)


def test_protected_items_are_verbatim_substrings_of_source():
    """保护项必须是原文中的逐字片段，保真检查才能可靠执行。"""
    for case in HUMANIZE_CASES:
        if not case.source_text:
            continue
        for item in case.protected_items:
            assert item in case.source_text, (
                f"{case.case_id} 保护项不是原文逐字片段：{item!r}"
            )


def test_all_cases_carry_cleanroom_license_note():
    """全部案例必须携带净室许可证/来源说明（ADR-0011 硬门）。"""
    for case in HUMANIZE_CASES:
        note = case.license_source_note
        assert note.strip(), f"{case.case_id} 缺少许可证/来源说明"
        assert any(
            marker in note for marker in ("净室", "原创", "无第三方")
        ), f"{case.case_id} 许可证说明缺少净室声明"
