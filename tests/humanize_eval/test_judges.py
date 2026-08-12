"""系统裁判隔离与双向判断（Test plan 5）。"""

from __future__ import annotations

import tempfile
from pathlib import Path

from conftest import FakeGenerationPort, make_fake_judges

from bridges.humanize_eval.generation import (
    GenerationResult,
    GenerationStatus,
)
from bridges.humanize_eval.judges import (
    JUDGE_DIMENSIONS,
    FakeSystemJudge,
    JudgeOrder,
    JudgePreference,
    JudgeScoreItem,
    JudgeVerdict,
    QwenSystemJudge,
    judge_pair,
)
from bridges.humanize_eval.packet import JudgePacketItem
from bridges.humanize_eval.runner import HumanizeRunner

ITEM = JudgePacketItem(
    item_id="item-test",
    user_request="测试请求",
    source_text="原文内容。",
    mode="rewrite",
    audience="读者",
    channel="博客",
    target_length="短",
    realism_commitment="不虚构。",
    source_boundary="只使用原文。",
    protected_items=["测试保护项"],
    output_a="候选一输出。",
    output_b="候选二输出。",
)


def test_judge_pair_runs_both_orders():
    judge = FakeSystemJudge(judge_id="family-a-judge-1")
    ab, ba = judge_pair(judge, ITEM)
    assert ab.order is JudgeOrder.AB
    assert ba.order is JudgeOrder.BA
    assert ab.is_valid and ba.is_valid


def test_judge_pair_detects_position_bias():
    """恒定偏好 label_a 的裁判：位置翻转后矛盾 → 无效裁决。"""
    judge = FakeSystemJudge(
        judge_id="family-a-judge-1",
        scripted_preferences={ITEM.item_id: JudgePreference.A},
    )
    ab, ba = judge_pair(judge, ITEM)
    assert not ab.is_valid and not ba.is_valid
    assert "位置翻转" in ab.invalid_reason


def test_judge_pair_tie_to_preference_is_invalid():
    """一侧 TIE、另一侧明确偏好：无合理理由的位置偏差，裁决无效。"""
    judge = FakeSystemJudge(
        judge_id="family-a-judge-1",
        scripted_preferences={ITEM.item_id: JudgePreference.A},
    )
    ab, ba = judge_pair(judge, ITEM)
    # AB 偏好 A，BA 也偏好 A（同一 label 偏好）→ 位置矛盾 → 无效。
    assert not ab.is_valid and not ba.is_valid
    assert "位置翻转" in ab.invalid_reason


def test_judge_pair_neutral_both_orders_valid():
    """两侧都 TIE 是有效的中性裁决（不构成位置偏差）。"""
    judge = FakeSystemJudge(judge_id="family-a-judge-1")
    ab, ba = judge_pair(judge, ITEM)
    assert ab.preference is JudgePreference.TIE
    assert ba.preference is JudgePreference.TIE
    assert ab.is_valid and ba.is_valid


def test_judge_pair_detects_fidelity_position_sensitivity():
    """AC-9：关键维度（fidelity）评分位置敏感（两顺序分差 ≥2）→ 无效。"""
    from bridges.humanize_eval.judges import JUDGE_DIMENSIONS

    class SensitiveJudge:
        judge_id = "family-a-sensitive"
        judge_version = "v1"

        def judge(self, item: JudgePacketItem, order: JudgeOrder):
            scores = [
                JudgeScoreItem(dimension=dimension, score=3)
                for dimension in JUDGE_DIMENSIONS
            ]
            # 偏好双向一致（AB 偏好 A、BA 偏好 B 指向同一候选），
            # 但 fidelity 评分位置敏感（AB 给候选一 5 分、BA 只给 1 分）。
            preference = (
                JudgePreference.A if order is JudgeOrder.AB else JudgePreference.B
            )
            fidelity_score = 5 if order is JudgeOrder.AB else 1
            scores[6] = JudgeScoreItem(
                dimension="fidelity", score=fidelity_score
            )
            return JudgeVerdict(
                judge_id=self.judge_id,
                judge_version=self.judge_version,
                item_id=item.item_id,
                order=order,
                preference=preference,
                reason_code="fidelity_risk",
                evidence_span=item.output_a[:20],
                scores=scores,
            )

    ab, ba = judge_pair(SensitiveJudge(), ITEM)  # type: ignore[arg-type]
    assert not ab.is_valid and not ba.is_valid
    assert "位置敏感" in ab.invalid_reason
    assert "fidelity" in ab.invalid_reason


def test_judge_isolation_no_shared_state():
    """裁判调用不继承任何历史：输入只有 item 载荷，无额外上下文。"""
    judge = FakeSystemJudge(judge_id="family-a-judge-1")
    judge.judge(ITEM, JudgeOrder.AB)
    judge.judge(ITEM, JudgeOrder.BA)
    for item_id, _order, a, b in judge.calls:
        assert item_id == ITEM.item_id
        assert a == ITEM.output_a[:40]
        assert b == ITEM.output_b[:40]


def test_fake_judge_returns_seven_dimension_scores():
    judge = FakeSystemJudge(judge_id="family-a-judge-1")
    verdict = judge.judge(ITEM, JudgeOrder.AB)
    assert len(verdict.scores) == 7
    assert {s.dimension for s in verdict.scores} == set(JUDGE_DIMENSIONS)
    assert all(1 <= s.score <= 5 for s in verdict.scores)


def test_qwen_judge_parses_structured_output():
    """QwenSystemJudge 从模型输出解析结构化裁决（含无法判断路径）。"""

    class ScriptedPort:
        def __init__(self, text: str) -> None:
            self.text = text

        def generate(self, *, system_prompt, user_prompt, params):
            return GenerationResult(
                text=self.text,
                model_id="fake",
                parameters=params.model_dump(),
                status=GenerationStatus.SUCCESS,
            )

    judge = QwenSystemJudge(ScriptedPort('{"preference": "A", "scores": {}}'))
    verdict = judge.judge(ITEM, JudgeOrder.AB)
    assert verdict.preference is JudgePreference.A

    judge_unparsable = QwenSystemJudge(ScriptedPort("抱歉，我无法判断。"))
    verdict_na = judge_unparsable.judge(ITEM, JudgeOrder.AB)
    assert verdict_na.preference is JudgePreference.CANNOT_JUDGE
    assert verdict_na.cannot_judge_reason

    judge_failed = QwenSystemJudge(
        _FailedPort()
    )
    verdict_failed = judge_failed.judge(ITEM, JudgeOrder.AB)
    assert verdict_failed.preference is JudgePreference.CANNOT_JUDGE


class _FailedPort:
    def generate(self, *, system_prompt, user_prompt, params):
        return GenerationResult(
            text="",
            model_id="fake",
            parameters=params.model_dump(),
            status=GenerationStatus.FAILED,
            error_code="boom",
            error_message="模型调用失败。",
        )


def _run_with_judges(judges, workspace: Path) -> object:
    outdir = Path(tempfile.mkdtemp())
    port = FakeGenerationPort()
    runner = HumanizeRunner(
        outdir=outdir, workspace=workspace, port=port, judges=judges
    )
    return runner.run()


def test_only_one_judge_means_inconclusive(workspace: Path):
    summary = _run_with_judges(
        [FakeSystemJudge(judge_id="family-a-judge-1")], workspace
    )
    assert summary.verdict == "inconclusive"
    assert any("裁判" in r and "少于 3" in r for r in summary.reasons)


def test_three_judges_same_family_means_inconclusive(workspace: Path):
    """三个同家族裁判即使结论一致，聚合层也不允许 passed（多样性硬门）。"""
    from bridges.humanize_eval.judges import FakeSystemJudge as F

    judges = [
        F(judge_id="family-a-judge-1", model_family="qwen"),
        F(judge_id="family-a-judge-2", model_family="qwen"),
        F(judge_id="family-a-judge-3", model_family="qwen"),
    ]
    summary = _run_with_judges(judges, workspace)
    assert summary.verdict == "inconclusive"
    assert any("多样性不足" in r for r in summary.reasons)
    # 无分歧/无顺序失败：保真与协议层面链路是通的。
    assert summary.inconsistent_items == []
    assert summary.invalid_verdicts == 0


def test_aggregation_never_passes_without_diversity(workspace: Path):
    """三个同家族裁判即使结论一致，聚合层也不允许 passed（多样性硬门）。"""
    from bridges.humanize_eval.judges import FakeSystemJudge as F

    judges = [
        F(judge_id="family-a-judge-1"),
        F(judge_id="family-b-judge-1"),
        F(judge_id="family-c-judge-1"),
    ]
    summary = _run_with_judges(judges, workspace)
    assert summary.verdict == "inconclusive"
    assert any("多样性不足" in r for r in summary.reasons)


def test_no_waiting_for_human_input(workspace: Path):
    """系统裁判流程不得出现等待人工输入的状态。"""
    judges = make_fake_judges()
    summary = _run_with_judges(judges, workspace)
    assert summary.verdict in ("inconclusive", "passed", "failed")
    for reason in summary.reasons:
        assert "人工" not in reason
