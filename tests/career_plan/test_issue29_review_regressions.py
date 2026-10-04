"""工单29独立验收发现的证据绑定与任务延续回归。"""

from __future__ import annotations

import pytest

from bridges.career_plan.analyzing import analyze_samples
from bridges.career_plan.contracts import CareerBranch, CareerGapCategory
from bridges.career_plan.gap import build_gaps, build_personal_advice
from bridges.career_plan.parsing import parse_career_request
from tests.career_plan.test_issue29_personal_career_gap import _background, _profile_item, _sample


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("我会 Java，但不会 Redis", ["has_evidence", "to_improve"]),
        ("我不会 Java，但会 Redis", ["to_improve", "has_evidence"]),
        ("我会 Java，想学 Redis", ["has_evidence", "to_confirm"]),
        ("岗位要求熟悉 Java 和 Redis", ["to_confirm", "to_confirm"]),
        ("我的朋友会 Java 和 Redis", ["to_confirm", "to_confirm"]),
        ("我会 Java 吗？Redis 需要学吗？", ["to_confirm", "to_confirm"]),
    ],
)
def test_skill_evidence_binds_to_own_assertion(text, expected):
    sample = _sample(skills=["Java", "Redis"], requirements=["熟悉 Java 和 Redis"])
    gaps = build_gaps(
        analysis=parse_career_request("Java 后端开发，给我准备建议"),
        report=analyze_samples([sample]),
        samples=[sample],
        background=_background(_profile_item(text)),
    )
    assert {gap.term: gap.category.value for gap in gaps} == dict(
        zip(["Java", "Redis"], expected, strict=True)
    )


def test_conflicting_same_source_remains_unknown():
    sample = _sample(skills=["Java"], requirements=["熟悉Java"])
    gaps = build_gaps(
        analysis=parse_career_request("Java 后端开发，给我准备建议"),
        report=analyze_samples([sample]),
        samples=[sample],
        background=_background(
            _profile_item("我会Java。"), _profile_item("我不会Java。", ref="p2")
        ),
    )
    assert gaps[0].category is CareerGapCategory.TO_CONFIRM


def test_project_requirement_survives_all_skills_evidenced():
    analysis = parse_career_request("Java 后端开发，给我准备建议，推荐一个实践项目")
    sample = _sample(skills=["Java"], requirements=["熟悉Java"])
    report = analyze_samples([sample])
    background = _background(_profile_item("我会Java"))
    gaps = build_gaps(analysis=analysis, report=report, samples=[sample], background=background)
    advices, requirements, _ = build_personal_advice(
        analysis=analysis, report=report, gaps=gaps, background=background
    )
    assert any(item.kind == "github" for item in requirements)
    assert all(item.inference for item in advices)


def test_personal_task_continuation_keeps_branch_and_time():
    analysis = parse_career_request(
        "换成杭州", task_texts=("Java 后端开发，给我准备建议，每天30分钟",)
    )
    assert analysis.branch is CareerBranch.PERSONAL_PLANNING
    assert analysis.time_budget_minutes == 30
    only = parse_career_request(
        "只看招聘信息", task_texts=("Java 后端开发，给我准备建议，每天30分钟",)
    )
    assert only.branch is CareerBranch.JOB_INTEL


def test_current_statement_overrides_old_negative_profile():
    from bridges.career_plan.contracts import CareerBackgroundItem, CareerBackgroundSource

    sample = _sample(skills=["Java"], requirements=["熟悉Java"])
    background = _background(
        _profile_item("我不会Java"),
        CareerBackgroundItem(
            source=CareerBackgroundSource.USER_STATEMENT, text="我现在熟悉Java", source_ref="u-new"
        ),
    )
    gaps = build_gaps(
        analysis=parse_career_request("Java 后端开发，给我准备建议"),
        report=analyze_samples([sample]),
        samples=[sample],
        background=background,
    )
    assert gaps[0].category is CareerGapCategory.HAS_EVIDENCE
    assert gaps[0].background_refs == ["u-new"]


@pytest.mark.parametrize(
    "text", ["我不确定是否会Java", "小明会Java", "我不是不会Java", "我说的例子是：我会Java吗？"]
)
def test_ambiguous_assertion_never_confirms_ability(text):
    sample = _sample(skills=["Java"], requirements=["熟悉Java"])
    gaps = build_gaps(
        analysis=parse_career_request("Java 后端开发，给我准备建议"),
        report=analyze_samples([sample]),
        samples=[sample],
        background=_background(_profile_item(text)),
    )
    assert gaps[0].category is CareerGapCategory.TO_CONFIRM


def test_time_and_device_constraints_change_actual_step():
    analysis = parse_career_request("Java 后端开发，给我准备建议")
    sample = _sample(skills=["Java"], requirements=["熟悉Java"])
    report = analyze_samples([sample])
    results = []
    for budget, text in [
        (10, "我不会Java"),
        (90, "我不会Java"),
        (90, "我不会Java。只有手机，没有电脑"),
    ]:
        background = _background(_profile_item(text), budget=budget, budget_source="statement")
        gaps = build_gaps(analysis=analysis, report=report, samples=[sample], background=background)
        advices, _, _ = build_personal_advice(
            analysis=analysis, report=report, gaps=gaps, background=background
        )
        results.append(advices[0].detail)
    assert "暂不安排完整项目" in results[0]
    assert "小项目" in results[1]
    assert "暂不安排需要电脑运行的项目" in results[2]


def test_resources_requirement_with_known_skills_retains_job_basis():
    analysis = parse_career_request("Java 后端开发，给我准备建议并推荐学习资料")
    sample = _sample(skills=["Java"], requirements=["熟悉Java"])
    report = analyze_samples([sample])
    background = _background(_profile_item("我会Java"))
    gaps = build_gaps(analysis=analysis, report=report, samples=[sample], background=background)
    _, requirements, _ = build_personal_advice(
        analysis=analysis, report=report, gaps=gaps, background=background
    )
    resources = next(item for item in requirements if item.kind == "resources")
    assert resources.skills == ["Java"]
    assert resources.basis == ["熟悉Java"]


def test_task_evidence_ref_uses_identity_and_version_not_private_excerpt():
    from bridges.career_plan.background import build_statement_items
    analysis = parse_career_request("Java 后端开发，给我准备建议")
    items = build_statement_items(
        user_content="继续", user_message_id="u2",
        task_texts=("我会Java",), task_ref="task:t1#v3",
    )
    assert items[1].source_ref == "task:t1#v3#task-text:0"
    assert analysis.original_request not in items[1].source_ref
