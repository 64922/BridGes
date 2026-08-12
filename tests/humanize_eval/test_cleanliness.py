"""来源清洁扫描与数据卡（Test plan 5：精确/近似片段比对、许可清单）。"""

from __future__ import annotations

from bridges.humanize_eval.cases import (
    HumanizeCase,
    HumanizeCaseKind,
    HUMANIZE_CASES,
)
from bridges.humanize_eval.cleanliness import (
    run_cleanliness_scan,
    scan_cross_partition_duplicates,
    scan_license,
    generate_datacard,
)


def _copy_case_with(case: HumanizeCase, *, case_id: str) -> HumanizeCase:
    return case.model_copy(
        update={"case_id": case_id},
    )


def test_real_corpus_is_clean():
    scan = run_cleanliness_scan(HUMANIZE_CASES)
    assert scan.checked_cases == 93
    assert scan.license_complete == 93
    assert scan.license_missing == 0
    assert scan.duplicate_pairs == []
    assert scan.protected_violations == []
    assert scan.clean


def test_cross_partition_duplicate_detected():
    """同源文本出现在 development 与 holdout 两侧必须被报告。"""
    from bridges.humanize_eval.cases import CasePartition

    article = next(
        c for c in HUMANIZE_CASES if c.kind is HumanizeCaseKind.ARTICLE
    )
    dev = _copy_case_with(article, case_id="dev-copy-v1")
    dev = dev.model_copy(
        update={"partition": CasePartition.DEVELOPMENT, "content_sha256": ""}
    )
    hold = _copy_case_with(article, case_id="hold-copy-v1")
    hold = hold.model_copy(
        update={"partition": CasePartition.HOLDOUT, "content_sha256": ""}
    )
    problems = scan_cross_partition_duplicates([dev, hold])
    assert problems, "跨分区近似重复必须被报告"


def test_license_missing_detected():
    case = HUMANIZE_CASES[0]
    bad = case.model_copy(
        update={
            "case_id": "bad-license-v1",
            "license_source_note": "来源：某外部网站。",
            "content_sha256": "",
        }
    )
    complete, missing = scan_license([bad, case])
    assert complete == 1
    assert missing == ["bad-license-v1"]


def test_datacard_structure():
    card = generate_datacard(HUMANIZE_CASES)
    assert card["total_cases"] == 93
    assert card["surfaces"]["chat_naturalness"]["count"] == 45
    assert card["surfaces"]["article_humanization"]["count"] == 48
    # holdout 分区计入。
    assert card["surfaces"]["chat_naturalness"]["holdout"] >= 3
    assert card["surfaces"]["article_humanization"]["holdout"] >= 5
    # 体裁与强度切片存在。
    genres = {g["label"] for g in card["surfaces"]["article_humanization"]["genres"]}
    for expected in ("email", "report", "tutorial", "speech", "popular_science",
                     "research_technical", "instruction"):
        assert expected in genres, f"数据卡缺少体裁：{expected}"
    intensities = {
        i["label"]
        for i in card["surfaces"]["article_humanization"]["intensities"]
    }
    assert {"light", "standard", "deep"} <= intensities
    assert card["cleanliness"]["clean"] is True
    # 对抗类型覆盖。
    adversarial = {a["label"] for a in card["surfaces"]["adversarial_types"]}
    assert len(adversarial) >= 12
