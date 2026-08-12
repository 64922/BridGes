"""案例版本化与哈希（Test plan：案例定义部分）。"""

from __future__ import annotations

import pytest

from bridges.humanize_eval.cases import (
    HUMANIZE_CASES,
    HumanizeCaseIntegrityError,
    HumanizeCaseKind,
    case_hashes,
    validate_cases,
)


def test_registry_has_article_and_chat_cases():
    kinds = {case.kind for case in HUMANIZE_CASES}
    assert HumanizeCaseKind.ARTICLE in kinds
    assert HumanizeCaseKind.CHAT in kinds
    assert len(HUMANIZE_CASES) == 2


def test_case_hashes_stable_and_versioned():
    hashes = case_hashes()
    assert len(hashes) == 2
    assert all(len(h) == 64 for h in hashes.values())
    assert all(case.case_id.endswith("-v1") for case in HUMANIZE_CASES)


def test_validate_cases_clean():
    assert validate_cases() == []


def test_hash_detects_content_change():
    case = HUMANIZE_CASES[0]
    tampered = case.model_copy(update={"audience": "被篡改的受众"})
    with pytest.raises(HumanizeCaseIntegrityError):
        tampered.ensure_hash()


def test_article_case_declares_no_personal_experience():
    article = next(c for c in HUMANIZE_CASES if c.kind is HumanizeCaseKind.ARTICLE)
    assert article.source_text
    # 原文不含编造亲历模式（“自我监控”中的“我”不构成亲历表述）。
    for pattern in ("我以前", "我过去", "我试过", "上周", "半小时前", "朋友"):
        assert pattern not in article.source_text, f"原文出现亲历模式：{pattern}"
    for claim in article.forbidden_claims:
        assert any(
            keyword in claim
            for keyword in ("亲历", "对话", "数据", "功能", "时间地点")
        ), f"禁止 claim 类别不明确：{claim}"


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
