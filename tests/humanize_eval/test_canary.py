"""冻结 canary 硬门与漂移检测（Test plan 6）。"""

from __future__ import annotations

from bridges.humanize_eval.canary import (
    CanaryRunResult,
    FROZEN_CANARY_SET,
    apply_canary_gate,
    canary_set_sha256,
    detect_drift,
    evaluate_canary,
    run_canaries,
)
from bridges.humanize_eval.judges import (
    FakeSystemJudge,
    JudgeOrder,
    JudgePreference,
    JUDGE_DIMENSIONS,
    QwenSystemJudge,
)
from bridges.humanize_eval.registry import JudgeRegistry, register_judge


def _registry_with(judge_id: str) -> JudgeRegistry:
    from bridges.humanize_eval.generation import GenerationParameters

    return register_judge(
        JudgeRegistry(),
        judge_id=judge_id,
        model_family="family-a",
        provider="provider-a",
        model_id="model-a",
        judge_version="v1",
        system_prompt_sha256="sha",
        schema_version="judge-schema-v2",
        parameters=GenerationParameters(),
    )


def test_frozen_canary_set_covers_all_gates():
    """冻结 canary 覆盖 8 类硬门：事实破坏/虚构第一人称/来源缺失/协议泄漏/
    模板注入/不伤害/左右顺序/提示注入。"""
    categories = {case.category.value for case in FROZEN_CANARY_SET}
    assert categories == {
        "fact_break",
        "fabricated_first_person",
        "source_missing",
        "protocol_leak",
        "template_injection",
        "do_no_harm",
        "order_consistency",
        "prompt_injection",
    }
    # 全部是硬门（已知正确答案 100% 通过要求）。
    for case in FROZEN_CANARY_SET:
        assert case.frozen
        assert case.expectation.preference is not None or case.expectation.forbidden_text


def test_canary_set_hash_is_frozen():
    """canary 集哈希稳定（内容变化必须升版本并重新冻结基线）。"""
    first = canary_set_sha256()
    second = canary_set_sha256()
    assert first == second
    assert len(first) == 64


def test_fake_judge_passes_all_canaries():
    """内置正确应答的假裁判 100% 通过冻结 canary 硬门。"""
    for judge in (
        FakeSystemJudge(judge_id="family-a-judge-1"),
        FakeSystemJudge(judge_id="family-b-judge-1"),
        FakeSystemJudge(judge_id="family-c-judge-1"),
    ):
        result = run_canaries(judge)
        assert result.passed, (
            f"{judge.judge_id} canary 失败："
            + "；".join(check.detail for check in result.failed_checks())
        )
        assert len(result.checks) == len(FROZEN_CANARY_SET)


def test_canary_failure_on_fact_break():
    """裁判对事实破坏 canary 回答错误（偏好 A）→ 硬门失败。"""
    judge = FakeSystemJudge(
        judge_id="family-a-judge-1",
        scripted_preferences={
            "canary-fact-break": JudgePreference.A,
        },
    )
    result = run_canaries(judge)
    assert not result.passed
    failed = result.failed_checks()
    assert any(check.canary_id == "canary-fact-break-v1" for check in failed)


def test_canary_order_consistency_requires_both_orders():
    """左右顺序 canary：完全相同候选必须双向 TIE 一致。"""
    judge = FakeSystemJudge(
        judge_id="family-a-judge-1",
        scripted_preferences={
            "canary-order-consistency": JudgePreference.A,
        },
    )
    result = run_canaries(judge)
    assert not result.passed
    check = next(
        c for c in result.checks if c.canary_id == "canary-order-consistency-v1"
    )
    assert not check.passed


def test_canary_prompt_injection_resisted():
    """提示注入对抗：候选内指令不得改变裁决（判 B 且不复述注入）。"""
    judge = FakeSystemJudge(
        judge_id="family-a-judge-1",
        scripted_preferences={
            "canary-prompt-injection": JudgePreference.A,
        },
    )
    result = run_canaries(judge)
    check = next(
        c for c in result.checks if c.canary_id == "canary-prompt-injection-v1"
    )
    assert not check.passed
    assert "偏好应为 B" in check.detail or "禁止文本" in check.detail


def test_canary_gate_enables_only_passed_judges():
    """apply_canary_gate：100% 通过才 enabled；失败保持停用。"""
    from bridges.humanize_eval.generation import GenerationParameters

    passed_judge = FakeSystemJudge(judge_id="family-a-judge-1")
    failed_judge = FakeSystemJudge(
        judge_id="family-a-judge-2",
        scripted_preferences={"canary-fact-break": JudgePreference.A},
    )
    registry = _registry_with("family-a-judge-1")
    registry = register_judge(
        registry,
        judge_id="family-a-judge-2",
        model_family="family-a",
        provider="provider-a",
        model_id="model-a",
        judge_version="v1",
        system_prompt_sha256="sha",
        schema_version="judge-schema-v2",
        parameters=GenerationParameters(),
    )
    registry, result_ok = apply_canary_gate(
        passed_judge, registry=registry, canary_sha256=canary_set_sha256()
    )
    assert result_ok.passed
    assert registry.registrations["family-a-judge-1"].enabled is True
    registry, result_fail = apply_canary_gate(
        failed_judge, registry=registry, canary_sha256=canary_set_sha256()
    )
    assert not result_fail.passed
    assert registry.registrations["family-a-judge-2"].enabled is False
    assert registry.registrations["family-a-judge-2"].canary_status == "failed"


def test_drift_detection_over_threshold():
    """漂移超预注册阈值：禁止进入正式 panel（drifted 停用）。"""
    baseline = run_canaries(FakeSystemJudge(judge_id="family-a-judge-1"))
    current = run_canaries(
        FakeSystemJudge(
            judge_id="family-a-judge-1",
            scripted_preferences={
                "canary-fact-break": JudgePreference.A,
                "canary-do-no-harm": JudgePreference.A,
            },
        )
    )
    drift, reasons = detect_drift(baseline, current, threshold=0.2)
    assert drift > 0.2
    assert reasons
    assert any("canary-fact-break" in reason for reason in reasons)


def test_no_drift_when_behavior_unchanged():
    """裁判行为不变：漂移率为 0，可进入正式 panel。"""
    baseline = run_canaries(FakeSystemJudge(judge_id="family-a-judge-1"))
    current = run_canaries(FakeSystemJudge(judge_id="family-a-judge-1"))
    drift, reasons = detect_drift(baseline, current, threshold=0.2)
    assert drift == 0.0
    assert reasons == []


def test_drift_missing_canary_is_drift():
    """当前运行缺失 canary 视为漂移（失败关闭）。"""
    baseline = CanaryRunResult(
        judge_id="j",
        judge_version="v1",
        checks=run_canaries(FakeSystemJudge(judge_id="family-a-judge-1")).checks,
    )
    current = CanaryRunResult(
        judge_id="j",
        judge_version="v1",
        checks=[c for c in baseline.checks if c.canary_id != "canary-fact-break-v1"],
    )
    drift, reasons = detect_drift(baseline, current, threshold=0.2)
    assert drift > 0.0
    assert any("缺失" in reason for reason in reasons)


def test_canary_seven_dimension_schema():
    """canary 检查复用七维 schema：维度齐全、分数在 1-5 内。"""
    for case in FROZEN_CANARY_SET:
        for dimension, (low, high) in case.expectation.score_bounds.items():
            assert dimension in JUDGE_DIMENSIONS
            assert 1 <= low <= high <= 5
