"""独立复核必须从原始证据发现错判，不能由非空引用或生成者自评放行。"""

from types import SimpleNamespace

import pytest

from bridges.career_plan.analyzing import analyze_samples
from bridges.career_plan.contracts import (
    CareerBackgroundItem,
    CareerBackgroundSource,
    CareerBranch,
    CareerGapCategory,
    CareerPlanProjection,
    CareerPlanStatus,
)
from bridges.career_plan.gap import build_gaps, build_personal_advice
from bridges.career_plan.kernel import _personal_review_gate
from bridges.career_plan.parsing import parse_career_request
from bridges.kernel.contracts import QualityVerdict
from tests.career_plan.test_issue29_personal_career_gap import _background, _sample


def _projection(text: str) -> CareerPlanProjection:
    request = parse_career_request("Java 后端，给我准备建议")
    sample = _sample(skills=["Java"], requirements=["熟悉 Java"])
    background = _background(
        CareerBackgroundItem(
            source=CareerBackgroundSource.USER_STATEMENT,
            text=text,
            source_ref="u1",
        )
    )
    report = analyze_samples([sample])
    gaps = build_gaps(analysis=request, report=report, samples=[sample], background=background)
    advices, _, _ = build_personal_advice(
        analysis=request,
        report=report,
        gaps=gaps,
        background=background,
    )
    return CareerPlanProjection(
        status=CareerPlanStatus.SUCCESS,
        topic="Java",
        original_request=request.original_request,
        branch=CareerBranch.PERSONAL_PLANNING,
        samples=[sample],
        background=background,
        gaps=gaps,
        personal_advices=advices,
    )


def _review(projection: CareerPlanProjection):
    execution = SimpleNamespace(
        artifact=SimpleNamespace(
            payload={"projection": projection.model_dump(mode="json")},
        )
    )
    return _personal_review_gate(None, execution)


@pytest.mark.parametrize("text", ["我会 Java", "我不会 Java", "我想学 Java"])
def test_independent_review_accepts_supported_classification_and_unknown(text: str) -> None:
    result = _review(_projection(text))
    assert result.verdict is QualityVerdict.PASS
    assert result.detail["executed"] is True


def test_independent_review_blocks_wrong_classification_despite_nonempty_refs() -> None:
    projection = _projection("我会 Java")
    projection.gaps[0].category = CareerGapCategory.TO_IMPROVE
    assert _review(projection).verdict is QualityVerdict.BLOCKED


def test_independent_review_blocks_refs_without_semantic_support() -> None:
    projection = _projection("我会 Java")
    projection.background.items[0].text = "我会 Redis"
    assert _review(projection).verdict is QualityVerdict.BLOCKED


def test_independent_review_blocks_confirmed_claim_over_conflicting_background() -> None:
    projection = _projection("我会 Java，但我不会 Java")
    assert _review(projection).verdict is QualityVerdict.PASS
    projection.gaps[0].category = CareerGapCategory.HAS_EVIDENCE
    assert _review(projection).verdict is QualityVerdict.BLOCKED


def test_independent_review_blocks_strategy_with_fabricated_background_basis() -> None:
    projection = _projection("我会 Java")
    projection.personal_advices[0].background_basis = ["本轮自述：我精通 Java"]
    assert _review(projection).verdict is QualityVerdict.BLOCKED


def test_independent_review_blocks_strategy_presented_as_direct_fact() -> None:
    projection = _projection("我会 Java")
    projection.personal_advices[0].inference = False
    assert _review(projection).verdict is QualityVerdict.BLOCKED


def test_independent_review_blocks_fabricated_job_quote() -> None:
    projection = _projection("我会 Java")
    projection.gaps[0].job_evidence = ["熟悉 Java 并具备五年经验"]
    assert _review(projection).verdict is QualityVerdict.BLOCKED


def test_independent_review_blocks_new_comprehensive_inference() -> None:
    projection = _projection("我会 Java")
    projection.gaps[0].inference = True
    assert _review(projection).verdict is QualityVerdict.BLOCKED


def test_independent_review_blocks_strategy_not_supported_by_classification() -> None:
    projection = _projection("我想学 Java")
    projection.personal_advices[0].kind = "skill"
    assert _review(projection).verdict is QualityVerdict.BLOCKED


def test_job_only_skips_independent_personal_review() -> None:
    projection = _projection("我会 Java")
    projection.branch = CareerBranch.JOB_INTEL
    result = _review(projection)
    assert result.verdict is QualityVerdict.PASS
    assert result.detail["executed"] is False
