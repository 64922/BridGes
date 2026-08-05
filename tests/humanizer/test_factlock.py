"""文本级事实锁引擎测试（Issue 28）。

固定语料（SKILL 夹具）覆盖数值、单位、对象关系、限定条件、公式、引用
与结论强度七类；断言改写前后自动比较的阻断/需人工/新增判定。
"""

from __future__ import annotations

import re
from pathlib import Path

from bridges.contracts.humanizer import (
    FactLockKind,
    FactLockSeverity,
    FactLockStatus,
)
from bridges.skills.humanizer.factlock import (
    check_requirements,
    compare_locks,
    extract_locks,
    surface_summary,
)

_CORPUS = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "bridges"
    / "skills"
    / "humanizer"
    / "skill"
    / "fixtures"
    / "rewrite_corpus.md"
).read_text(encoding="utf-8")
_SOURCE = re.sub(
    r"\s+", " ",
    _CORPUS.split("## 原文")[1].split("## 事实锁清单")[0].strip(),
)

_GENERATE_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "bridges"
    / "skills"
    / "humanizer"
    / "skill"
    / "fixtures"
    / "generate_topic.md"
).read_text(encoding="utf-8")
_CONSTRAINTS = [
    line.strip()[2:]
    for line in _GENERATE_FIXTURE.split("## 硬约束")[1].strip().splitlines()
    if line.strip().startswith("- ")
]


def _kinds(text: str) -> set[FactLockKind]:
    return {draft.kind for draft in extract_locks(text)}


def test_corpus_extraction_covers_all_seven_kinds() -> None:
    kinds = _kinds(_SOURCE)
    assert kinds == {
        FactLockKind.NUMBER,
        FactLockKind.UNIT,
        FactLockKind.OBJECT_RELATION,
        FactLockKind.QUALIFIER,
        FactLockKind.FORMULA,
        FactLockKind.CITATION,
        FactLockKind.CONCLUSION_STRENGTH,
    }


def test_corpus_extracts_expected_surfaces() -> None:
    summary = surface_summary(_SOURCE)
    joined = "\n".join(summary)
    assert "25 μmol·m-2·s-1" in joined  # 上标已规范化为 ASCII 位
    assert "35°C" in joined
    assert "6CO2 + 6H2O" in joined.replace("₂", "2").replace("₆", "6")
    assert "(Zhang et al., 2021)" in joined
    assert "参见" in joined


def test_self_comparison_preserves_everything() -> None:
    result = compare_locks(_SOURCE, _SOURCE)
    assert result.passed
    assert not result.blocking_conflicts
    assert not result.needs_human
    assert all(entry.status == FactLockStatus.PRESERVED for entry in result.entries)


def test_number_change_is_blocking() -> None:
    modified = _SOURCE.replace("约为 25 μmol·m⁻²·s⁻¹", "约为 30 μmol·m⁻²·s⁻¹")
    result = compare_locks(_SOURCE, modified)
    assert not result.passed
    assert any("25" in c and "30" in c for c in result.blocking_conflicts)


def test_unit_change_is_blocking() -> None:
    modified = _SOURCE.replace("25 μmol·m⁻²·s⁻¹", "25 mmol·m⁻²·s⁻¹")
    result = compare_locks(_SOURCE, modified)
    assert not result.passed
    assert any("μmol" in c for c in result.blocking_conflicts)


def test_formula_change_is_blocking() -> None:
    modified = _SOURCE.replace("6CO₂ + 6H₂O → C₆H₁₂O₆ + 6O₂", "6CO₂ + 12H₂O → C₆H₁₂O₆ + 6O₂")
    result = compare_locks(_SOURCE, modified)
    assert not result.passed
    assert any("公式" in c or "6CO2" in c.replace("₂", "2") for c in result.blocking_conflicts)


def test_strength_upgrade_is_blocking() -> None:
    modified = _SOURCE.replace("尚不能证明其普遍适用", "证明其普遍适用")
    result = compare_locks(_SOURCE, modified)
    assert not result.passed
    assert any("结论强度" in c for c in result.blocking_conflicts)


def test_strength_downgrade_is_needs_human() -> None:
    modified = _SOURCE.replace("尚不能证明其普遍适用", "其普遍适用可能成立")
    result = compare_locks(_SOURCE, modified)
    assert result.passed  # 弱化不阻断
    assert any("结论强度" in n for n in result.needs_human)


def test_qualifier_removal_is_needs_human() -> None:
    # 「目前」为无歧义限定词（不被他处同词遮蔽），删除应标注需人工确认
    modified = _SOURCE.replace("目前只能说初步结果支持", "只能说初步结果支持")
    result = compare_locks(_SOURCE, modified)
    assert result.passed
    assert any("限定条件" in n for n in result.needs_human)


def test_citation_removal_is_needs_human() -> None:
    modified = _SOURCE.replace("（Zhang et al., 2021）", "")
    result = compare_locks(_SOURCE, modified)
    assert result.passed
    assert any("引用" in n for n in result.needs_human)


def test_relation_flip_is_blocking() -> None:
    # 语料跨行在「抑制」与「光合效率」间留有空白，替换时保持原空白
    modified = _SOURCE.replace("高温会抑制 光合效率", "高温会促进 光合效率")
    result = compare_locks(_SOURCE, modified)
    assert not result.passed
    assert any("对象关系" in c for c in result.blocking_conflicts)


def test_added_facts_are_info_not_blocking() -> None:
    added = _SOURCE + "此外，另有研究声称年降雨量为 500 mm。"
    result = compare_locks(_SOURCE, added)
    assert result.passed
    added_entries = [e for e in result.entries if e.status == FactLockStatus.ADDED]
    assert added_entries
    assert all(e.severity == FactLockSeverity.INFO for e in added_entries)


def test_equivalent_unit_presentation_is_preserved() -> None:
    # μmol·m⁻²·s⁻¹ 与 μmol/(m²·s) 是同一单位的不同呈现，应判定为保持
    modified = _SOURCE.replace("25 μmol·m⁻²·s⁻¹", "25 μmol/(m²·s)")
    result = compare_locks(_SOURCE, modified)
    assert result.passed
    assert not any("25" in c for c in result.blocking_conflicts)


def test_range_and_decimal_normalization() -> None:
    # 8-10 与 8 到 10、25.0 与 25 语义等价
    modified = _SOURCE.replace("8-10 个光量子", "8 到 10 个光量子")
    modified = modified.replace("约为 25 μmol·m⁻²·s⁻¹", "约为 25.0 μmol·m⁻²·s⁻¹")
    result = compare_locks(_SOURCE, modified)
    assert result.passed


# ---------------------------------------------------------------------------
# 生成路径硬约束检查
# ---------------------------------------------------------------------------


def test_negative_constraint_violation_is_blocking() -> None:
    bad = "婴儿期每天 20 小时，成年期 12 小时，老年期 10 小时。睡眠时长决定了健康水平。"
    result = check_requirements(_CONSTRAINTS, bad)
    assert not result.passed
    assert any("不得声称" in c for c in result.blocking_conflicts)


def test_negative_constraint_obeyed_passes() -> None:
    good = (
        "婴儿期每天约 14 小时睡眠，成年期约 7-8 小时，老年期约 7 小时。"
        "个体差异很大，时长只是参考。睡眠时长并不会决定健康水平。"
    )
    result = check_requirements(_CONSTRAINTS, good)
    assert result.passed
    assert not result.blocking_conflicts


def test_length_constraint_numeric_evaluation() -> None:
    long_text = "字" * 3000
    result = check_requirements(["全文不超过 1200 字。"], long_text)
    assert not result.passed
    assert any("长度约束" in c for c in result.blocking_conflicts)

    short_text = "字" * 100
    result = check_requirements(["全文不超过 1200 字。"], short_text)
    assert result.passed


def test_positive_free_text_constraint_is_honest_needs_human() -> None:
    result = check_requirements(
        ["必须给出婴儿期、成年期与老年期的典型睡眠时长。"],
        "婴儿期每天约 14 小时，成年期约 7 小时，老年期约 7 小时。",
    )
    # 自由文本约束无法确定性验证时标注人工确认而非阻断
    assert result.passed
    assert result.needs_human
