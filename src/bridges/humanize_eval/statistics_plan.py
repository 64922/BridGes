"""预注册统计计划（Issue 11）。

在运行 holdout 前把统计计划、偏好公式、置信区间、非劣效边界、最小
样本、系统裁判 panel、模型多样性、双向一致性、canary/漂移阈值、
硬失败定义与多重切片报告写入版本化配置并锁定哈希。

计划是纯配置（pydantic 模型），``digest()`` 哈希进入运行锁与报告：
改变任何预注册配置都会产生新的计划身份，必须创建新评测运行，不得
覆盖旧报告。默认值全部引用现有模块的单一事实源（案例最小样本、
panel 裁判数、无法判断/分歧阈值），不重复定义。裁判 panel、模型
多样性、canary/漂移阈值的行使点在 registry/aggregator（其 digest
已进入运行锁，见 registry.py），本计划只记录同值并引用。
"""

from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, Field

from bridges.humanize_eval.aggregator import (
    CANNOT_JUDGE_RATIO,
    DISAGREEMENT_ITEM_RATIO,
)
from bridges.humanize_eval.cases import MIN_CASES_PER_SURFACE
from bridges.humanize_eval.registry import MIN_PANEL_JUDGES

#: 统计计划版本（计划结构/默认值变更必须递增；变化 = 新计划身份）。
STATISTICS_PLAN_VERSION = "1"

#: 主偏好分公式（预注册，见 AC-3）：win 计 1、tie 计 0.5、loss 计 0。
PREFERENCE_FORMULA = "win + 0.5 * tie"

#: 置信区间方法（AC-2）：固定种子的 case-cluster bootstrap 百分位区间。
CI_METHOD = "case_cluster_bootstrap_percentile"

#: 改善门：candidate 相对 current production 的偏好分 CI 下界必须大于
#: 50% 才能声称显著改善（AC-4）。
IMPROVEMENT_CI_LOWER_BOUND = 0.50

#: 非劣效边界：文章 candidate 相对 Humanizer-zh 不得差超过 5 个百分点，
#: 即偏好分 CI 下界至少 45%（AC-5）。
NONINFERIORITY_MARGIN_PP = 5.0
NONINFERIORITY_CI_LOWER_BOUND = 0.50 - NONINFERIORITY_MARGIN_PP / 100.0

#: 预注册关键切片（AC-9 按模式/强度/体裁/长度/风险/do-no-harm 报告）。
KEY_SLICES = (
    "mode",              # 聊天模式 / 文章改写档位
    "rewrite_intensity", # 改写强度（文章）
    "genre",             # 体裁（文章）
    "target_length",     # 目标长度
    "risk",              # 风险级别
    "do_no_harm",        # do-no-harm 高风险边界
)

#: 关键切片明显退化的偏好分 CI 下界下限：低于此值即"明显退化"。
#: 预注册在运行 holdout 之前，不得在看完 holdout 后修改。
SLICE_REGRESSION_CI_LOWER_BOUND = 0.40

#: 非关键保真通过率硬门（AC-7）：非 critical 检查失败占比最多 1%。
NONCRITICAL_FIDELITY_PASS_RATE = 0.99

#: 硬失败定义（AC-7 零容忍类别）：critical 保真检查失败 = 严重失败。
#: 类别与 fidelity 检查器的检查项 id 对齐（缺失 = 失败关闭）；公式与
#: 保护区没有独立检查器，由 negations（因果/结论强度近似）与
#: proper_nouns（保护区逐字保留）覆盖。
HARD_FAILURE_CATEGORIES = (
    "numbers",       # 关键数字
    "units",         # 单位
    "dates",         # 日期
    "proper_nouns",  # 专名与保护短语（含 protected spans）
    "quotes",        # 精确引语
    "urls",          # URL
    "negations",     # 否定边界（因果/结论强度近似）
    "first_person",  # 虚构亲历
)


class StatisticsPlan(BaseModel):
    """预注册统计计划：全部统计与发布门参数的版本化配置。"""

    plan_version: str = Field(default=STATISTICS_PLAN_VERSION)
    preference_formula: str = Field(
        default=PREFERENCE_FORMULA, description="主偏好分公式。"
    )
    ci_method: str = Field(
        default=CI_METHOD, description="置信区间方法。"
    )
    bootstrap_seed: int = Field(
        default=2026, description="case-cluster bootstrap 固定种子。"
    )
    bootstrap_iterations: int = Field(
        default=2000, description="bootstrap 重采样次数。"
    )
    ci_level: float = Field(default=0.95, description="置信区间水平。")
    improvement_ci_lower_bound: float = Field(
        default=IMPROVEMENT_CI_LOWER_BOUND,
        description="改善门：相对 current production 的偏好分 CI 下界必须大于此值。",
    )
    noninferiority_margin_pp: float = Field(
        default=NONINFERIORITY_MARGIN_PP,
        description="非劣效边界（百分点）：相对 Humanizer-zh 不得差超过此值。",
    )
    noninferiority_ci_lower_bound: float = Field(
        default=NONINFERIORITY_CI_LOWER_BOUND,
        description="非劣效门：相对 Humanizer-zh 的偏好分 CI 下界至少此值。",
    )
    min_cases_per_surface: int = Field(
        default=MIN_CASES_PER_SURFACE, description="每语料面最低有效案例数。"
    )
    min_panel_judges: int = Field(
        default=MIN_PANEL_JUDGES, description="正式 panel 最低裁判数。"
    )
    max_cannot_judge_ratio: float = Field(
        default=CANNOT_JUDGE_RATIO, description="无法判断票占比上限（行使点在聚合器）。"
    )
    disagreement_item_ratio: float = Field(
        default=DISAGREEMENT_ITEM_RATIO,
        description="分歧 item 占比上限（行使点在聚合器，结果经 panel 健康传入门）。",
    )
    # 漂移阈值不在本计划重复：行使点在 registry.drift_threshold，
    # 其值已进入 registry.digest() 与运行锁（版本化锁定，见 registry.py）。
    noncritical_fidelity_pass_rate: float = Field(
        default=NONCRITICAL_FIDELITY_PASS_RATE,
        description="非关键保真通过率硬门（AC-7）。",
    )
    hard_failure_categories: tuple[str, ...] = Field(
        default=HARD_FAILURE_CATEGORIES,
        description="硬失败定义：零容忍的 critical 检查类别。",
    )
    key_slices: tuple[str, ...] = Field(
        default=KEY_SLICES, description="预注册关键切片（AC-9）。"
    )
    slice_regression_ci_lower_bound: float = Field(
        default=SLICE_REGRESSION_CI_LOWER_BOUND,
        description="关键切片明显退化的偏好分 CI 下界下限。",
    )
    min_slice_cases: int = Field(
        default=5, description="关键切片最小样本（低于此值不参与退化判定）。"
    )

    def digest(self) -> str:
        """计划身份哈希（进入运行锁与报告；任何变化 = 新计划）。"""
        payload = self.model_dump(mode="json")
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def default_statistics_plan() -> StatisticsPlan:
    """内置预注册统计计划（版本化；调整 = 升版本并重新判定）。"""
    return StatisticsPlan()


__all__ = [
    "StatisticsPlan",
    "default_statistics_plan",
    "STATISTICS_PLAN_VERSION",
    "PREFERENCE_FORMULA",
    "CI_METHOD",
    "IMPROVEMENT_CI_LOWER_BOUND",
    "NONINFERIORITY_MARGIN_PP",
    "NONINFERIORITY_CI_LOWER_BOUND",
    "KEY_SLICES",
    "HARD_FAILURE_CATEGORIES",
]
