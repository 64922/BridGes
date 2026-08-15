"""确定性句子级剔除模块单测（人味化改造第八次改进 Issue 02）。

覆盖：四类机械可剔除码的句子定位与整句剔除、移除清单、归一化偏移映射
（全半角/空白折叠）、句数占比阈值护栏、空稿护栏、不可剔除（非机械类/
无位置）护栏。
"""

from __future__ import annotations

from bridges.contracts.humanizer import (
    FidelityFailure,
    FidelityFailureCode,
    FidelitySeverity,
    SpanLocation,
)
from bridges.skills.humanizer.excision import (
    EXCISABLE_CODES,
    EXCISION_SENTENCE_RATIO_LIMIT,
    can_excise,
    excise_blocking_content,
    split_sentences,
)
from bridges.skills.humanizer.factlock import _normalize_text


def _failure(
    code: FidelityFailureCode,
    note: str,
    *,
    location: SpanLocation | None = None,
) -> FidelityFailure:
    return FidelityFailure(
        failure_id="ff-test",
        code=code,
        severity=FidelitySeverity.BLOCKING,
        category="测试类别",
        item_type="test",
        location=location,
        note=note,
    )


def _loc_of(candidate: str, surface: str) -> SpanLocation:
    """按归一化文本中 surface 首次出现的位置构造 finding 位置。"""
    normalized = _normalize_text(candidate)
    start = normalized.find(surface)
    assert start >= 0, f"{surface!r} not in {normalized!r}"
    return SpanLocation(start=start, end=start + len(surface))


def test_unattributed_claim_sentence_is_excised() -> None:
    candidate = (
        "番茄工作法把时间切成 25 分钟的工作块。四个工作块后休息 15 分钟。"
        "这个方法适合办公室人群。全球有 90% 的用户使用番茄钟。"
    )
    findings = [
        _failure(
            FidelityFailureCode.UNATTRIBUTED_CLAIM,
            "候选新增「90%」没有账本来源。",
            location=_loc_of(candidate, "90%"),
        )
    ]
    record = excise_blocking_content(candidate, findings)
    assert record is not None
    assert record.text == (
        "番茄工作法把时间切成 25 分钟的工作块。四个工作块后休息 15 分钟。"
        "这个方法适合办公室人群。"
    )
    assert record.removed_count == 1
    assert record.removed_sentence_count == 1
    assert record.sentence_ratio == 0.25
    assert record.items[0].code == "unattributed_claim"


def test_assumption_not_allowed_sentence_is_excised() -> None:
    candidate = (
        "光合作用是植物把光能转化为化学能的过程。叶绿素吸收光能。"
        "二氧化碳和水合成有机物。比如，假设在强光下光合速率会更高。"
    )
    findings = [
        _failure(
            FidelityFailureCode.ASSUMPTION_NOT_ALLOWED,
            "候选使用了「比如/假设/设想」标注的假设内容。",
            location=_loc_of(candidate, "比如"),
        )
    ]
    record = excise_blocking_content(candidate, findings)
    assert record is not None
    assert record.text == (
        "光合作用是植物把光能转化为化学能的过程。叶绿素吸收光能。"
        "二氧化碳和水合成有机物。"
    )
    assert record.removed_count == 1
    assert record.items[0].code == "assumption_not_allowed"


def test_assumption_carries_fact_sentence_is_excised() -> None:
    candidate = (
        "光合作用是植物把光能转化为化学能的过程。叶绿素吸收光能。"
        "比如，假设转化效率达到 80%，产能会翻倍。"
    )
    findings = [
        _failure(
            FidelityFailureCode.ASSUMPTION_CARRIES_FACT,
            "显式假设「80%」承载了高风险事实。",
            location=_loc_of(candidate, "80%"),
        )
    ]
    record = excise_blocking_content(candidate, findings)
    assert record is not None
    assert record.text == (
        "光合作用是植物把光能转化为化学能的过程。叶绿素吸收光能。"
    )
    assert record.items[0].code == "assumption_carries_fact"


def test_first_person_unbound_sentence_is_excised() -> None:
    candidate = (
        "把时间切成 25 分钟的工作块。四个工作块后休息 15 分钟。"
        "我去年靠番茄钟坚持了三十天。"
    )
    findings = [
        _failure(
            FidelityFailureCode.FIRST_PERSON_UNBOUND,
            "候选新增亲历「我去年」没有可绑定的来源经历。",
            location=_loc_of(candidate, "我去年"),
        )
    ]
    record = excise_blocking_content(candidate, findings)
    assert record is not None
    assert record.text == (
        "把时间切成 25 分钟的工作块。四个工作块后休息 15 分钟。"
    )
    assert record.items[0].code == "first_person_unbound"


def test_multiple_findings_in_same_sentence_removed_once() -> None:
    candidate = (
        "把时间切成 25 分钟的工作块。四个工作块后休息 15 分钟。"
        "我去年坚持了 30 天，还去过 5 个国家。"
    )
    findings = [
        _failure(
            FidelityFailureCode.FIRST_PERSON_UNBOUND,
            "无来源亲历。",
            location=_loc_of(candidate, "我去年"),
        ),
        _failure(
            FidelityFailureCode.UNATTRIBUTED_CLAIM,
            "候选新增「30」没有账本来源。",
            location=_loc_of(candidate, "30"),
        ),
    ]
    # 「5 个国家」的 5（rfind 避开「25 分钟/15 分钟」里的 5，落在第三句）
    normalized = _normalize_text(candidate)
    last_five = normalized.rfind("5")
    assert last_five >= 0
    findings.append(
        _failure(
            FidelityFailureCode.UNATTRIBUTED_CLAIM,
            "候选新增「5」没有账本来源。",
            location=SpanLocation(start=last_five, end=last_five + 1),
        )
    )
    record = excise_blocking_content(candidate, findings)
    assert record is not None
    # 同句多条 finding：只剔除一次，移除清单仍如实记录每条
    assert record.removed_sentence_count == 1
    assert record.removed_count == 3
    assert record.text == "把时间切成 25 分钟的工作块。四个工作块后休息 15 分钟。"
    assert [item.code for item in record.items] == [
        "first_person_unbound",
        "unattributed_claim",
        "unattributed_claim",
    ]


def test_normalized_offsets_map_back_to_original_sentences() -> None:
    # 全半角与空白折叠会让归一化偏移与原文偏移不一致；剔除必须作用在
    # 原文句子而不是归一化文本的错位位置。
    candidate = "第一句。第二句包含 ９０％ 的数字。第三句。"
    findings = [
        _failure(
            FidelityFailureCode.UNATTRIBUTED_CLAIM,
            "候选新增「90%」没有账本来源。",
            location=_loc_of(candidate, "90%"),
        )
    ]
    record = excise_blocking_content(candidate, findings)
    assert record is not None
    assert record.text == "第一句。第三句。"
    assert record.removed_sentence_count == 1


def test_fact_breaking_code_is_never_excised() -> None:
    candidate = "把时间切成 35 分钟的工作块。四个工作块后休息 15 分钟。"
    findings = [
        _failure(
            FidelityFailureCode.NUMBER_CHANGED,
            "原文数值「25|分钟」在结果中未保持。",
        )
    ]
    assert excise_blocking_content(candidate, findings) is None


def test_excisable_code_without_location_is_never_excised() -> None:
    """保留检查形态（如亲历丢失的 FIRST_PERSON_UNBOUND）无位置 → 不剔除。"""
    candidate = "把时间切成 25 分钟的工作块。四个工作块后休息 15 分钟。"
    findings = [
        _failure(
            FidelityFailureCode.FIRST_PERSON_UNBOUND,
            "原文亲历在结果中未保持。",
        )
    ]
    assert excise_blocking_content(candidate, findings) is None


def test_mixed_findings_are_never_excised() -> None:
    candidate = (
        "把时间切成 35 分钟的工作块。四个工作块后休息 15 分钟。"
        "全球有 90% 的用户使用番茄钟。"
    )
    findings = [
        _failure(
            FidelityFailureCode.NUMBER_CHANGED,
            "原文数值「25|分钟」在结果中未保持。",
        ),
        _failure(
            FidelityFailureCode.UNATTRIBUTED_CLAIM,
            "候选新增「90%」没有账本来源。",
            location=_loc_of(candidate, "90%"),
        ),
    ]
    assert excise_blocking_content(candidate, findings) is None


def test_sentence_ratio_guard_aborts() -> None:
    # 两句话中违规句占 1/2（50%），超过默认阈值 40% → 放弃剔除
    candidate = (
        "把时间切成 25 分钟的工作块和 5 分钟的休息块，四个工作块后"
        "休息 15 分钟。全球有 90% 的用户使用番茄钟。"
    )
    findings = [
        _failure(
            FidelityFailureCode.UNATTRIBUTED_CLAIM,
            "候选新增「90%」没有账本来源。",
            location=_loc_of(candidate, "90%"),
        )
    ]
    assert EXCISION_SENTENCE_RATIO_LIMIT == 0.4
    assert excise_blocking_content(candidate, findings) is None


def test_empty_result_guard_aborts() -> None:
    # 全文只有一句违规内容：剔除后为空 → 放弃
    candidate = "全球有 90% 的用户使用番茄钟。"
    findings = [
        _failure(
            FidelityFailureCode.UNATTRIBUTED_CLAIM,
            "候选新增「90%」没有账本来源。",
            location=_loc_of(candidate, "90%"),
        )
    ]
    assert excise_blocking_content(candidate, findings) is None


def test_empty_candidate_or_findings_aborts() -> None:
    assert excise_blocking_content("", []) is None
    assert excise_blocking_content("   ", []) is None
    assert excise_blocking_content("正文。", []) is None


def test_out_of_range_location_aborts() -> None:
    candidate = "把时间切成 25 分钟的工作块。四个工作块后休息 15 分钟。"
    findings = [
        _failure(
            FidelityFailureCode.UNATTRIBUTED_CLAIM,
            "越界位置。",
            location=SpanLocation(start=999, end=1000),
        )
    ]
    assert excise_blocking_content(candidate, findings) is None


def test_excisable_codes_set_matches_issue_definition() -> None:
    expected = frozenset(
        {
            FidelityFailureCode.UNATTRIBUTED_CLAIM,
            FidelityFailureCode.ASSUMPTION_NOT_ALLOWED,
            FidelityFailureCode.ASSUMPTION_CARRIES_FACT,
            FidelityFailureCode.FIRST_PERSON_UNBOUND,
        }
    )
    assert expected == EXCISABLE_CODES


def test_can_excise_gates_attemptable_scenarios() -> None:
    candidate = (
        "把时间切成 25 分钟的工作块。四个工作块后休息 15 分钟。"
        "全球有 90% 的用户使用番茄钟。"
    )
    excisable = _failure(
        FidelityFailureCode.UNATTRIBUTED_CLAIM,
        "候选新增「90%」没有账本来源。",
        location=_loc_of(candidate, "90%"),
    )
    # 全为可剔除类且带位置 → 可尝试
    assert can_excise([excisable])
    # 空清单 / 破坏事实类 / 无位置 → 不可尝试
    assert not can_excise([])
    assert not can_excise(
        [
            _failure(
                FidelityFailureCode.NUMBER_CHANGED,
                "原文数值「25|分钟」在结果中未保持。",
            )
        ]
    )
    assert not can_excise(
        [
            _failure(
                FidelityFailureCode.FIRST_PERSON_UNBOUND,
                "原文亲历在结果中未保持。",
            )
        ]
    )
    # 混合（可剔除 + 破坏事实）→ 不可尝试
    assert not can_excise(
        [
            excisable,
            _failure(
                FidelityFailureCode.NUMBER_CHANGED,
                "原文数值「25|分钟」在结果中未保持。",
            ),
        ]
    )


def test_split_sentences_keeps_punctuation_and_newlines() -> None:
    # 第一句。= 4 字符；第二句！= 4 字符；第三句？= 4 字符
    assert split_sentences("第一句。第二句！第三句？") == [
        (0, 4),
        (4, 8),
        (8, 12),
    ]
    # 「。\n」连排视为同一边界段（含换行），标点并入前一句
    assert split_sentences("第一句。\n第二句；第三句") == [
        (0, 5),
        (5, 9),
        (9, 12),
    ]
    # 无边界：整段一句；纯空白：无句子
    assert split_sentences("整段只有一句") == [(0, 6)]
    assert split_sentences("   ") == []
