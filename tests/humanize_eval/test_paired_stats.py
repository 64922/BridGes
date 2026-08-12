"""配对统计（Issue 11 Test plan 1/2/6）。

- 合成配对数据验证非配对算法会产生错误结论（样本虚高），新算法按
  case 聚合得到预期偏好分；
- 重复 seed/execution 数量极不均衡时不给某一 case 额外权重；
- 固定种子 bootstrap 报告哈希稳定；计划变化 = 新计划身份。
"""

from __future__ import annotations

from bridges.humanize_eval.judges import (
    JudgeOrder,
    JudgePreference,
    JudgeVerdict,
)
from bridges.humanize_eval.paired_stats import (
    ItemPreference,
    aggregate_case_preferences,
    bootstrap_preference_ci,
    candidate_preference,
    compute_paired_statistics,
)
from bridges.humanize_eval.statistics_plan import StatisticsPlan


def _verdict(
    preference: JudgePreference,
    *,
    order: JudgeOrder,
    item_id: str = "item-1",
    judge_id: str = "judge-1",
) -> JudgeVerdict:
    return JudgeVerdict(
        judge_id=judge_id,
        judge_version="test-v1",
        item_id=item_id,
        order=order,
        preference=preference,
    )


def _resolve(
    verdict_ab: JudgeVerdict,
    verdict_ba: JudgeVerdict,
    *,
    candidate_in_a: bool,
    judge_id: str = "judge-1",
) -> ItemPreference:
    from bridges.humanize_eval.paired_stats import resolve_judge_outcome

    class Outcome:
        def __init__(self) -> None:
            self.judge_id = judge_id
            self.item_id = verdict_ab.item_id
            self.verdict_ab = verdict_ab
            self.verdict_ba = verdict_ba

    return resolve_judge_outcome(
        Outcome(),
        case_by_item={verdict_ab.item_id: "case-1"},
        candidate_in_a_by_item={verdict_ab.item_id: candidate_in_a},
    )


# ---------------------------------------------------------------------------
# 槽位还原（AC-2：A/B 与 B/A 聚到 candidate 视角）
# ---------------------------------------------------------------------------

def test_candidate_preference_restores_slot_perspective():
    # candidate 在 label_a 槽：AB 顺序偏好 A = candidate 胜。
    assert candidate_preference(
        _verdict(JudgePreference.A, order=JudgeOrder.AB), candidate_in_a=True
    ) is JudgePreference.A
    assert candidate_preference(
        _verdict(JudgePreference.B, order=JudgeOrder.AB), candidate_in_a=True
    ) is JudgePreference.B
    # candidate 在 label_b 槽：AB 顺序偏好 B = candidate 胜。
    assert candidate_preference(
        _verdict(JudgePreference.B, order=JudgeOrder.AB), candidate_in_a=False
    ) is JudgePreference.A
    assert candidate_preference(
        _verdict(JudgePreference.A, order=JudgeOrder.AB), candidate_in_a=False
    ) is JudgePreference.B
    # BA 顺序（候选一是 output_b）：偏好语义取反。
    assert candidate_preference(
        _verdict(JudgePreference.A, order=JudgeOrder.BA), candidate_in_a=True
    ) is JudgePreference.B
    assert candidate_preference(
        _verdict(JudgePreference.B, order=JudgeOrder.BA), candidate_in_a=True
    ) is JudgePreference.A
    # TIE / CANNOT_JUDGE 不还原。
    assert candidate_preference(
        _verdict(JudgePreference.TIE, order=JudgeOrder.AB), candidate_in_a=True
    ) is JudgePreference.TIE
    assert candidate_preference(
        _verdict(JudgePreference.CANNOT_JUDGE, order=JudgeOrder.AB),
        candidate_in_a=True,
    ) is JudgePreference.CANNOT_JUDGE


def test_resolve_judge_outcome_marks_bidirectional_contradiction_invalid():
    # AB 偏好 A（candidate 在 a = candidate 胜）；BA 偏好 B
    # （candidate 在 a、BA 下 B = candidate 胜）：一致 → 有效。
    item = _resolve(
        _verdict(JudgePreference.A, order=JudgeOrder.AB),
        _verdict(JudgePreference.B, order=JudgeOrder.BA),
        candidate_in_a=True,
    )
    assert item.valid and item.preference is JudgePreference.A
    # AB 偏好 A、BA 也偏好 A：还原后矛盾 → 无效（位置偏差）。
    item = _resolve(
        _verdict(JudgePreference.A, order=JudgeOrder.AB),
        _verdict(JudgePreference.A, order=JudgeOrder.BA),
        candidate_in_a=True,
    )
    assert not item.valid and "位置偏差" in item.invalid_reason
    # 缺少 sealed mapping 记录 → 无效。
    from bridges.humanize_eval.paired_stats import resolve_judge_outcome

    class Outcome:
        judge_id = "j"
        item_id = "missing-item"
        verdict_ab = _verdict(JudgePreference.A, order=JudgeOrder.AB)
        verdict_ba = _verdict(JudgePreference.B, order=JudgeOrder.BA)

    item = resolve_judge_outcome(
        Outcome(),
        case_by_item={},
        candidate_in_a_by_item={},
    )
    assert not item.valid and "sealed mapping" in item.invalid_reason


# ---------------------------------------------------------------------------
# Test plan 1：非配对算法错误 vs 新算法 case 聚类
# ---------------------------------------------------------------------------

def test_case_cluster_does_not_treat_executions_as_independent_samples():
    """重复 execution 不进入样本：n 按 case 计，不按 execution 计。

    非配对 Welch 会把 5 case × 2 execution 当作 10 个独立样本造成伪精确；
    新算法每 case 只贡献一个偏好分。
    """
    prefs = [
        ItemPreference("j1", f"item-{case}-{exec}", f"case-{case}",
                       JudgePreference.A)
        for case in range(1, 5)
        for exec in (1, 2)  # 每个 case 两次执行
    ]
    # case-5 的裁判意见相反（候选败）。
    prefs.extend(
        ItemPreference("j1", f"item-5-{exec}", "case-5", JudgePreference.B)
        for exec in (1, 2)
    )
    aggregated = aggregate_case_preferences(prefs)
    stats = compute_paired_statistics(
        peer_sut_id="current-production",
        preferences=prefs,
        case_ids=sorted({p.case_id for p in prefs}),
    )
    # 5 个 case：4 胜 1 败 → 偏好分 0.8；样本量是 5 不是 10。
    assert stats.n_valid_cases == 5
    assert stats.wins == 4 and stats.losses == 1
    assert stats.preference_mean == 0.8


def test_uneven_execution_counts_do_not_weight_cases():
    """重复次数极不均衡：case A 3 次、case B 1 次，各自只算一票。

    case A：3 次全胜；case B：1 胜 1 败（平票 → 保守 tie）。
    偏好分 = (1 + 0.5) / 2 = 0.75，而不是按 execution 的 4/5 = 0.8。
    """
    prefs = [
        ItemPreference("j1", f"a-{i}", "case-a", JudgePreference.A)
        for i in range(3)
    ]
    prefs.extend([
        ItemPreference("j1", "b-1", "case-b", JudgePreference.A),
        ItemPreference("j1", "b-2", "case-b", JudgePreference.B),
    ])
    aggregated = aggregate_case_preferences(prefs)
    stats = compute_paired_statistics(
        peer_sut_id="current-production",
        preferences=prefs,
        case_ids=["case-a", "case-b"],
    )
    # case-a 胜、case-b 裁判平票（无效）→ 只有 case-a 进入胜负。
    assert stats.n_valid_cases == 1
    assert stats.wins == 1 and stats.ties == 0
    assert stats.invalid_judge_cases == 1
    assert stats.per_case["case-b"] == "invalid"
    assert stats.preference_mean == 1.0


def test_judge_level_aggregation_majority_and_split_tie():
    """裁判聚合：多数票生效；2-1 分裂有明确多数；1-1-1 分裂保守计 tie。"""
    # 3 裁判：2 A 1 B → case 胜。
    aggregated = aggregate_case_preferences([
        ItemPreference("j1", "i1", "c1", JudgePreference.A),
        ItemPreference("j2", "i2", "c1", JudgePreference.A),
        ItemPreference("j3", "i3", "c1", JudgePreference.B),
    ])
    assert aggregated["c1"] is JudgePreference.A
    # 3 裁判：A/B/TIE 各一 → 无多数 → tie。
    aggregated = aggregate_case_preferences([
        ItemPreference("j1", "i1", "c2", JudgePreference.A),
        ItemPreference("j2", "i2", "c2", JudgePreference.B),
        ItemPreference("j3", "i3", "c2", JudgePreference.TIE),
    ])
    assert aggregated["c2"] is JudgePreference.TIE
    # 同一裁判同一 case 平票（j1 对 c3 两方向）→ 该裁判该 case 无效，
    # 另一裁判的票保留。
    aggregated = aggregate_case_preferences([
        ItemPreference("j1", "x1", "c3", JudgePreference.A),
        ItemPreference("j1", "x2", "c3", JudgePreference.B),
        ItemPreference("j2", "y1", "c3", JudgePreference.A),
    ])
    assert aggregated["c3"] is JudgePreference.A


def test_cannot_judge_does_not_count_but_is_reported():
    """CANNOT_JUDGE 不计胜负，case 记为无法判断（报告用）。"""
    prefs = [
        ItemPreference("j1", "i1", "c1", JudgePreference.CANNOT_JUDGE),
        ItemPreference("j2", "i2", "c1", JudgePreference.CANNOT_JUDGE),
    ]
    aggregated = aggregate_case_preferences(prefs)
    assert "c1" not in aggregated
    stats = compute_paired_statistics(
        peer_sut_id="current-production",
        preferences=prefs,
        case_ids=["c1"],
    )
    assert stats.n_valid_cases == 0
    assert stats.cannot_judge_cases == 1
    assert stats.per_case["c1"] == "cannot_judge"


# ---------------------------------------------------------------------------
# Test plan 6：固定种子 bootstrap 稳定；计划变化 = 新身份
# ---------------------------------------------------------------------------

def test_bootstrap_deterministic_with_fixed_seed():
    scores = [1.0] * 20 + [0.0] * 10
    low_a, high_a = bootstrap_preference_ci(
        scores, seed=2026, iterations=2000
    )
    low_b, high_b = bootstrap_preference_ci(
        scores, seed=2026, iterations=2000
    )
    assert (low_a, high_a) == (low_b, high_b)
    assert low_a <= 20 / 30 <= high_a


def test_bootstrap_ci_stable_in_computed_statistics():
    """同一输入重算配对统计：CI 完全一致（报告哈希稳定前提）。"""
    scores = {"c1": JudgePreference.A, "c2": JudgePreference.A,
              "c3": JudgePreference.TIE, "c4": JudgePreference.B}
    prefs = [
        ItemPreference("j1", "i1", case_id, preference)
        for case_id, preference in scores.items()
    ]
    stats_a = compute_paired_statistics(
        peer_sut_id="current-production",
        preferences=prefs,
        case_ids=list(scores),
    )
    stats_b = compute_paired_statistics(
        peer_sut_id="current-production",
        preferences=prefs,
        case_ids=list(scores),
    )
    assert stats_a.ci_low == stats_b.ci_low
    assert stats_a.ci_high == stats_b.ci_high
    assert stats_a.preference_mean == 0.625


def test_plan_change_produces_new_digest():
    """改变预注册配置 = 新计划身份（必须创建新评测运行，不覆盖旧报告）。"""
    plan_a = StatisticsPlan()
    plan_b = StatisticsPlan(bootstrap_seed=7)
    assert plan_a.digest() != plan_b.digest()
    assert plan_a.digest() == StatisticsPlan().digest()


def test_noninferiority_bound_in_plan():
    """非劣效边界 5pp：CI 下界阈值 = 50% - 5pp = 45%。"""
    plan = StatisticsPlan()
    assert plan.noninferiority_margin_pp == 5.0
    assert plan.noninferiority_ci_lower_bound == 0.45
    assert plan.improvement_ci_lower_bound == 0.50
    assert plan.preference_formula == "win + 0.5 * tie"


# ---------------------------------------------------------------------------
# AC-6：七维度成对统计
# ---------------------------------------------------------------------------

def _scored_outcome(
    *,
    item_id: str,
    case_id: str,
    judge_id: str,
    score_ab: int,
    score_ba: int,
    candidate_in_a: bool,
) -> tuple:
    """构造带七维分数的裁判裁决对（AB 评 output_a、BA 评 output_b）。"""
    from bridges.humanize_eval.judges import JUDGE_DIMENSIONS, JudgeScoreItem

    def verdict(order: JudgeOrder, score: int) -> JudgeVerdict:
        return JudgeVerdict(
            judge_id=judge_id,
            judge_version="test-v1",
            item_id=item_id,
            order=order,
            preference=JudgePreference.TIE,
            scores=[
                JudgeScoreItem(dimension=dimension, score=score)
                for dimension in JUDGE_DIMENSIONS
            ],
        )

    from bridges.humanize_eval.paired_stats import compute_dimension_statistics

    class Outcome:
        def __init__(self) -> None:
            self.judge_id = judge_id
            self.item_id = item_id
            self.verdict_ab = verdict(JudgeOrder.AB, score_ab)
            self.verdict_ba = verdict(JudgeOrder.BA, score_ba)

    stats = compute_dimension_statistics(
        outcomes=[Outcome()],
        case_by_item={item_id: case_id},
        candidate_in_a_by_item={item_id: candidate_in_a},
    )
    return stats


def test_dimension_statistics_paired_diff():
    from bridges.humanize_eval.judges import (
        JudgeScoreItem,
        JudgeOrder,
        JudgeVerdict,
    )
    from bridges.humanize_eval.paired_stats import compute_dimension_statistics

    # candidate 在 label_a：AB 分数（5 分）评 candidate，BA 分数（3 分）评 peer。
    stats = _scored_outcome(
        item_id="item-1",
        case_id="case-1",
        judge_id="j1",
        score_ab=5,
        score_ba=3,
        candidate_in_a=True,
    )
    dim = stats["naturalness"]
    assert dim.candidate_mean == 5.0
    assert dim.peer_mean == 3.0
    assert dim.mean_diff == 2.0
    assert dim.n_cases == 1
    # candidate 在 label_b：AB 分数评 peer、BA 分数评 candidate（方向取反）。
    stats = _scored_outcome(
        item_id="item-2",
        case_id="case-2",
        judge_id="j1",
        score_ab=2,
        score_ba=4,
        candidate_in_a=False,
    )
    dim = stats["naturalness"]
    assert dim.candidate_mean == 4.0
    assert dim.peer_mean == 2.0
    assert dim.mean_diff == 2.0


def test_dimension_statistics_aggregates_by_case_with_multiple_judges():
    from bridges.humanize_eval.judges import (
        JUDGE_DIMENSIONS,
        JudgeOrder,
        JudgeScoreItem,
        JudgeVerdict,
    )
    from bridges.humanize_eval.paired_stats import compute_dimension_statistics

    def verdict(order: JudgeOrder, score: int, judge_id: str) -> JudgeVerdict:
        return JudgeVerdict(
            judge_id=judge_id,
            judge_version="v1",
            item_id="item-1",
            order=order,
            preference=JudgePreference.TIE,
            scores=[
                JudgeScoreItem(dimension=d, score=score)
                for d in JUDGE_DIMENSIONS
            ],
        )

    class Outcome:
        def __init__(self, judge_id: str, score_ab: int, score_ba: int) -> None:
            self.judge_id = judge_id
            self.item_id = "item-1"
            self.verdict_ab = verdict(JudgeOrder.AB, score_ab, judge_id)
            self.verdict_ba = verdict(JudgeOrder.BA, score_ba, judge_id)

    stats = compute_dimension_statistics(
        outcomes=[
            Outcome("j1", 4, 2),  # candidate 4、peer 2
            Outcome("j2", 2, 4),  # candidate 2、peer 4 → 分歧
        ],
        case_by_item={"item-1": "case-1"},
        candidate_in_a_by_item={"item-1": True},
    )
    dim = stats["naturalness"]
    # 同 case 多裁判分数取均值：candidate (4+2)/2=3、peer (2+4)/2=3。
    assert dim.n_cases == 1
    assert dim.candidate_mean == 3.0
    assert dim.peer_mean == 3.0
    assert dim.mean_diff == 0.0
    # 裁判间分歧：同 case 同维度分差 ≥2（4 vs 2）→ 计 1。
    assert dim.judge_disagreement_count == 1


def test_dimension_statistics_requires_valid_verdicts():
    """无效裁决（双向矛盾）不进入维度统计。"""
    from bridges.humanize_eval.paired_stats import compute_dimension_statistics

    # 直接构造 invalid 裁决。
    from bridges.humanize_eval.judges import (
        JUDGE_DIMENSIONS,
        JudgeOrder,
        JudgeVerdict,
    )

    def invalid_verdict(order: JudgeOrder) -> JudgeVerdict:
        return JudgeVerdict(
            judge_id="j1",
            judge_version="v1",
            item_id="item-y",
            order=order,
            preference=JudgePreference.A,
            invalid_reason="位置偏差",
        )

    class Outcome:
        judge_id = "j1"
        item_id = "item-y"
        verdict_ab = invalid_verdict(JudgeOrder.AB)
        verdict_ba = invalid_verdict(JudgeOrder.BA)

    stats = compute_dimension_statistics(
        outcomes=[Outcome()],
        case_by_item={"item-y": "case-y"},
        candidate_in_a_by_item={"item-y": True},
    )
    assert stats == {}
