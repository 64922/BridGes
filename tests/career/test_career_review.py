"""生涯规划确定性复核测试（Issue 29）。

复核是确定性控制平面：承诺词边界（阻断）、输出合同完整性门、事实证据门
（无证据不得当作稳定结论）、引用核验、过时来源与证据冲突标注——保证
系统保持校准、不越权、不作就业/薪酬/录取保证。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from bridges.career.review import apply_review_states, review_output
from bridges.contracts.career import (
    CareerAssumption,
    CareerEvidenceKind,
    CareerEvidenceSource,
    CareerFact,
    CareerItemState,
    CareerOption,
    CareerPlanningOutputContract,
    CareerRisk,
    CareerStage,
    CareerSuggestion,
)

NOW = datetime(2026, 8, 5, tzinfo=UTC)


def _evidence(
    evidence_id: str,
    *,
    kind: CareerEvidenceKind = CareerEvidenceKind.WEB_SEARCH,
    accessed_at: datetime = NOW,
) -> CareerEvidenceSource:
    return CareerEvidenceSource(
        evidence_id=evidence_id,
        kind=kind,
        title=f"来源 {evidence_id}",
        locator=f"https://example.com/{evidence_id}",
        accessed_at=accessed_at,
    )


def _output(**overrides: object) -> CareerPlanningOutputContract:
    base: dict[str, Any] = {
        "final_text": "综合来看，数据分析是适合你的方向之一。",
        "facts": [
            CareerFact(
                item_id="fact:1",
                content="数据分析岗位需求持续增长。",
                evidence_refs=["web:0"],
            )
        ],
        "assumptions": [
            CareerAssumption(
                item_id="assumption:1",
                content="你可能适合产品方向。",
                evidence_refs=[],
                verification_next_step="与行业从业者交流后再验证。",
            )
        ],
        "options": [
            CareerOption(
                item_id="option:1",
                content="数据分析方向。",
                evidence_refs=["web:0"],
                rationale="与你的兴趣匹配。",
            )
        ],
        "risks": [
            CareerRisk(
                item_id="risk:1",
                content="行业变化较快。",
                evidence_refs=["web:0"],
                trigger="技术栈更替",
            )
        ],
        "path": [
            CareerStage(
                item_id="stage:1",
                content="第一阶段打好统计基础。",
                evidence_refs=["web:0"],
                timeline="第 1-3 个月",
            )
        ],
        "suggestions": [
            CareerSuggestion(
                item_id="suggestion:1",
                content="学习 SQL 与 Python。",
                evidence_refs=["web:0"],
                verification="完成一个小项目验证。",
            )
        ],
        "boundary_statement": "本规划不构成就业、薪酬或录取保证，也不替代持证职业顾问。",
        "open_questions": [],
    }
    base.update(overrides)
    return CareerPlanningOutputContract(**base)


def test_review_passes_for_well_grounded_output() -> None:
    output = _output()
    evidence_map = {"web:0": _evidence("web:0")}
    result = review_output(output, evidence_map, now=NOW)
    assert result.passed
    assert not result.boundary_violations
    states = {review.item_id: review.state for review in result.reviews}
    assert states["fact:1"] == CareerItemState.VERIFIED
    # 无证据的假设不会因无证据而被误判为可交付事实
    assert states["assumption:1"] == CareerItemState.UNVERIFIED


def test_promise_words_block_delivery() -> None:
    # 正文承诺
    output = _output(final_text="选择这个方向，保证你能找到好工作。")
    result = review_output(output, {"web:0": _evidence("web:0")}, now=NOW)
    assert not result.passed
    assert any(
        "承诺" in violation or "保证" in violation for violation in result.boundary_violations
    )

    # 条目承诺（如「包上岸」）
    output = _output(
        suggestions=[
            CareerSuggestion(
                item_id="suggestion:1",
                content="报这个培训班包上岸。",
                evidence_refs=["web:0"],
            )
        ]
    )
    result = review_output(output, {"web:0": _evidence("web:0")}, now=NOW)
    assert not result.passed
    assert any("承诺表述" in violation for violation in result.boundary_violations)


def test_negated_promise_not_blocked() -> None:
    """「不保证/无法保证/不构成…保证/无法承诺」等否定形式不误伤。"""
    output = _output(
        final_text="本规划无法保证任何就业结果，请结合自身情况判断。",
        boundary_statement="不保证就业。",
    )
    result = review_output(output, {"web:0": _evidence("web:0")}, now=NOW)
    assert result.passed
    assert not result.boundary_violations

    # 标准边界声明（系统提示词要求的合规输出）不得误伤
    output = _output(
        final_text="综合来看，数据分析是适合你的方向之一。",
        boundary_statement="本规划不构成就业、薪酬或录取保证，也不替代持证职业顾问。",
    )
    result = review_output(output, {"web:0": _evidence("web:0")}, now=NOW)
    assert result.passed
    assert not result.boundary_violations

    # 「无法承诺/不会确保」同样放行
    output = _output(
        final_text="本规划无法承诺任何就业结果。",
        boundary_statement="本产品不会确保录取。",
    )
    result = review_output(output, {"web:0": _evidence("web:0")}, now=NOW)
    assert result.passed


def test_promise_in_boundary_statement_blocked() -> None:
    """保证边界声明本身含正向承诺（如「保证就业」）同样阻断。"""
    output = _output(boundary_statement="选择本方向保证就业。")
    result = review_output(output, {"web:0": _evidence("web:0")}, now=NOW)
    assert not result.passed
    assert any("保证边界声明" in violation for violation in result.boundary_violations)


def test_incomplete_contract_blocks() -> None:
    # 缺正文
    result = review_output(_output(final_text=""), {"web:0": _evidence("web:0")}, now=NOW)
    assert not result.passed
    # 缺边界声明
    result = review_output(_output(boundary_statement=""), {"web:0": _evidence("web:0")}, now=NOW)
    assert not result.passed
    # 六类全空
    result = review_output(
        _output(facts=[], assumptions=[], options=[], risks=[], path=[], suggestions=[]),
        {"web:0": _evidence("web:0")},
        now=NOW,
    )
    assert not result.passed


def test_fact_without_evidence_downgraded_to_unverified() -> None:
    """事实证据门：facts 无任何证据引用不得作为稳定结论呈现。"""
    output = _output(
        facts=[CareerFact(item_id="fact:1", content="转行后收入会翻倍。", evidence_refs=[])]
    )
    result = review_output(output, {}, now=NOW)
    assert result.passed  # 不阻断，但标记未核实
    review = next(item for item in result.reviews if item.item_id == "fact:1")
    assert review.state == CareerItemState.UNVERIFIED
    assert "待验证假设" in review.reason


def test_unknown_evidence_ref_marked_unverified() -> None:
    """引用核验：引用本轮不存在的证据 → 未核实 + 提示。"""
    output = _output(
        facts=[CareerFact(item_id="fact:1", content="某行业增长。", evidence_refs=["web:99"])]
    )
    result = review_output(output, {"web:0": _evidence("web:0")}, now=NOW)
    review = next(item for item in result.reviews if item.item_id == "fact:1")
    assert review.state == CareerItemState.UNVERIFIED
    assert any("web:99" in warning for warning in result.warnings)


def test_stale_source_marks_outdated() -> None:
    """过时来源：引用 stale 证据的条目标记来源过时。"""
    stale = _evidence("web:0", accessed_at=NOW - timedelta(days=200))
    stale.stale = True
    output = _output()
    result = review_output(output, {"web:0": stale}, now=NOW)
    review = next(item for item in result.reviews if item.item_id == "fact:1")
    assert review.state == CareerItemState.OUTDATED


def test_conflicting_refs_without_relationship_note_marked_conflicted() -> None:
    """证据冲突：引用多个证据但未说明证据间关系 → 冲突标注（不作稳定判断）。"""
    output = _output(
        facts=[
            CareerFact(
                item_id="fact:1",
                content="该行业正在扩张。",
                evidence_refs=["web:0", "web:1"],
            )
        ]
    )
    evidence_map = {"web:0": _evidence("web:0"), "web:1": _evidence("web:1")}
    result = review_output(output, evidence_map, now=NOW)
    review = next(item for item in result.reviews if item.item_id == "fact:1")
    assert review.state == CareerItemState.CONFLICTED


def test_relationship_note_avoids_conflict_marking() -> None:
    output = _output(
        facts=[
            CareerFact(
                item_id="fact:1",
                content="该行业正在扩张。",
                evidence_refs=["web:0", "web:1"],
                note="两个来源口径不同，结论存在分歧，需进一步核查。",
            )
        ]
    )
    evidence_map = {"web:0": _evidence("web:0"), "web:1": _evidence("web:1")}
    result = review_output(output, evidence_map, now=NOW)
    review = next(item for item in result.reviews if item.item_id == "fact:1")
    assert review.state == CareerItemState.VERIFIED


def test_assumption_without_verification_step_is_flagged() -> None:
    """待验证假设缺少下一步核查方式 → 确定性门给出可见缺口。"""
    output = _output(
        assumptions=[
            CareerAssumption(
                item_id="assumption:1",
                content="你可能适合产品方向。",
                evidence_refs=[],
                verification_next_step=None,
            )
        ]
    )
    result = review_output(output, {}, now=NOW)
    assert any("缺少下一步核查方式" in warning for warning in result.warnings)


def test_apply_review_states_writes_chinese_notes() -> None:
    """复核状态回写条目 note 与核查时间：未核实/冲突/过时对用户可见。"""
    output = _output(facts=[CareerFact(item_id="fact:1", content="某说法。", evidence_refs=[])])
    result = review_output(output, {}, now=NOW)
    apply_review_states(output, result, now=NOW)
    fact = output.facts[0]
    assert fact.note is not None
    assert "未核实" in fact.note
    assert fact.verified_at == NOW
